"""Historical imports preserve dates while manual reviews use server time."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from test_music import release_fixture, service

from shiros.core.schemas import utc_now
from shiros.domains.music import MusicRequest, MusicService
from shiros.domains.music_review_history import LOCAL_ZONE, parse_reviews


def run(s: MusicService, action: str, **data: Any) -> dict[str, Any]:
    return s.run(action, MusicRequest.model_validate(data))


@pytest.mark.integration
def test_historical_import_dates_idempotence_and_deletion(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    s = service(db_engine, tmp_path)
    item = run(s, "save", object={"kind": "recording", "title": "Synthetic reviewed recording"})[
        "item"
    ]
    item = run(
        s,
        "review",
        id=item["id"],
        revision=item["revision"],
        rating=7,
        comment="Current assessment",
    )["item"]
    current_review = item["reviews"][0]
    content = json.dumps(
        {
            "reviews": [
                {"date": "2019-01-02", "rating": 5, "rating_scale": 5, "comment": "Original scale"},
                {"date": "2020-02-03T12:00:00+08:00", "rating": 6, "comment": "Later assessment"},
            ]
        }
    )
    preview = run(s, "review-import-preview", id=item["id"], content=content)
    assert preview["new"] == 2
    assert run(s, "detail", id=item["id"])["item"]["revision"] == item["revision"]
    result = run(s, "review-import", id=item["id"], revision=item["revision"], content=content)
    item = result["item"]
    assert result["imported"] == 2 and item["rating"] == 7
    assert len(item["reviews"]) == 3
    imported = [r for r in item["reviews"] if r["origin"] == "import"]
    assert imported[0]["original_at"].astimezone(LOCAL_ZONE).year == 2020
    assert imported[0]["created_at"] > imported[0]["original_at"]
    assert imported[1]["rating_max"] == 5
    assert run(s, "list", kind="recording", rating_min=7, rating_max=7)["total"] == 1
    duplicate = run(s, "review-import", id=item["id"], revision=item["revision"], content=content)
    assert duplicate["imported"] == 0 and duplicate["item"]["revision"] == item["revision"]
    item = run(
        s, "review-delete", id=item["id"], revision=item["revision"], review_id=current_review["id"]
    )["item"]
    assert item["rating"] == 6 and len(item["reviews"]) == 2
    assert run(s, "list", kind="recording", rating_min=7, rating_max=7)["total"] == 0
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(*) FROM music_reviews WHERE object_id=:id"), {"id": item["id"]}
            )
            == 3
        )
        assert (
            c.scalar(
                text("SELECT count(*) FROM music_review_deletions WHERE object_id=:id"),
                {"id": item["id"]},
            )
            == 1
        )
    deleted_import = item["reviews"][0]
    item = run(
        s, "review-delete", id=item["id"], revision=item["revision"], review_id=deleted_import["id"]
    )["item"]
    assert item["rating"] == 5 and item["rating_scale"] == 5
    retry = run(s, "review-import", id=item["id"], revision=item["revision"], content=content)
    assert retry["imported"] == 0 and len(retry["item"]["reviews"]) == 1
    exported = run(s, "export")
    assert len(exported["tables"]["music_review_deletions"]) == 2
    portable = run(s, "review-export", id=item["id"])
    assert len(parse_reviews(json.dumps(portable, default=str), "json")) == 1


@pytest.mark.integration
def test_past_only_history_add_and_server_time_for_new_review(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    s = service(db_engine, tmp_path)
    item = run(s, "save", object=release_fixture(s, title="Synthetic release"))["item"]
    today = utc_now().astimezone(LOCAL_ZONE).date()
    tomorrow = today + timedelta(days=1)
    with pytest.raises(ValueError, match="music.review_future_date"):
        run(
            s,
            "review-history-add",
            id=item["id"],
            revision=item["revision"],
            historical_date=tomorrow,
            rating=4,
        )
    item = run(
        s,
        "review-history-add",
        id=item["id"],
        revision=item["revision"],
        historical_date=today,
        rating=4,
        comment="Supplemented today",
    )["item"]
    assert item["reviews"][0]["origin"] == "history_manual"
    assert item["reviews"][0]["original_at"].astimezone(LOCAL_ZONE).date() == today
    with pytest.raises(ValueError, match="music.review_date_not_allowed"):
        run(s, "review", id=item["id"], revision=item["revision"], rating=6, historical_date=today)
    item = run(s, "review", id=item["id"], revision=item["revision"], rating=6)["item"]
    assert item["reviews"][0]["original_at"] is None
    assert item["reviews"][0]["created_at"].astimezone(LOCAL_ZONE).date() == today


@pytest.mark.integration
def test_delete_scope_revision_and_atomic_import(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    items = [
        run(s, "save", object=release_fixture(s, title=f"Synthetic release {n}"))["item"]
        for n in range(2)
    ]
    first = run(s, "review", id=items[0]["id"], revision=items[0]["revision"], rating=4)["item"]
    with pytest.raises(ValueError, match="music.review_not_found"):
        run(
            s,
            "review-delete",
            id=items[1]["id"],
            revision=items[1]["revision"],
            review_id=first["reviews"][0]["id"],
        )
    with pytest.raises(ValueError, match="music.revision_conflict"):
        run(
            s,
            "review-delete",
            id=first["id"],
            revision=items[0]["revision"],
            review_id=first["reviews"][0]["id"],
        )
    content = json.dumps([{"date": "2018-01-01", "rating": 3}, {"rating": 99}])
    with pytest.raises(ValueError, match="music.invalid_review_import"):
        run(s, "review-import", id=first["id"], revision=first["revision"], content=content)
    unchanged = run(s, "detail", id=first["id"])["item"]
    assert unchanged["revision"] == first["revision"] and len(unchanged["reviews"]) == 1


@pytest.mark.parametrize(
    "row",
    [
        {"rating": 8},
        {"rating": 6, "rating_scale": 5},
        {"rating": True},
        {"rating": 2.5},
        {"rating_scale": False},
        {"rating_scale": 0},
        {"date": "not a date"},
        {"date": False},
        {"comment": False},
    ],
)
def test_invalid_import_row(row: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="music.invalid_review"):
        parse_reviews(json.dumps([row]), "json")


def test_future_date_rejected_and_csv_preserves_scale() -> None:
    tomorrow = (utc_now().astimezone(LOCAL_ZONE).date() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="music.review_future_date"):
        parse_reviews(json.dumps([{"date": tomorrow, "rating": 2}]), "json")
    rows = parse_reviews(
        'date,rating,rating_scale,comment\n2010-02-03,5,5,"line one\nline two"', "csv"
    )
    assert rows[0].rating_scale == 5 and rows[0].comment == "line one\nline two"
