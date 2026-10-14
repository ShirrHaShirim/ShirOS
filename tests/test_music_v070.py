"""Real relational acceptance cases; all data belongs to isolated synthetic scopes."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from test_music import service

from shiros.domains.music import MusicRequest, MusicService


def run(s: MusicService, action: str, **data: Any) -> dict[str, Any]:
    return s.run(action, MusicRequest.model_validate(data))


@pytest.mark.integration
def test_work_recording_release_tracks_genres(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)

    def save(**obj: object) -> dict[str, Any]:
        return dict(run(s, "save", object=obj)["item"])

    ga = run(s, "taxonomy-save", name="A", namespace="genre")["node"]
    gb = run(s, "taxonomy-save", name="B", namespace="genre")["node"]
    person = save(kind="person", title="Synthetic Author")
    work = save(
        kind="work",
        title="Four-movement Symphony",
        music_classes=["classical"],
        genre_ids=[ga["id"]],
        relations=[{"object_id": person["id"], "role": "composer"}],
    )
    other = save(kind="work", title="Other work", genre_ids=[gb["id"]])
    recordings = [
        save(
            kind="recording",
            title=f"Version {n}",
            relations=[
                {"object_id": work["id"], "role": "work"},
                {"object_id": person["id"], "role": "conductor"},
            ],
        )
        for n in range(3)
    ]
    second = save(
        kind="recording",
        title="Other recording",
        relations=[{"object_id": other["id"], "role": "work"}],
    )
    release = save(
        title="Complete release",
        relations=[
            {"object_id": recordings[1]["id"], "role": "recording"},
            {"object_id": second["id"], "role": "recording"},
        ],
        tracks=[
            {
                "title": f"Movement {n}",
                "position": n,
                "relations": [{"object_id": recordings[1]["id"], "role": "recording"}],
            }
            for n in range(1, 5)
        ]
        + [
            {
                "title": "Other",
                "position": 5,
                "relations": [{"object_id": second["id"], "role": "recording"}],
            }
        ],
    )
    extra = [
        save(
            title=f"Reissue {n}",
            relations=[{"object_id": recordings[1]["id"], "role": "recording"}],
        )
        for n in range(2)
    ]
    wd = run(s, "detail", id=work["id"])["item"]
    assert len([r for r in wd["related"] if r["kind"] == "recording"]) == 3
    rd = run(s, "detail", id=recordings[1]["id"])["item"]
    assert {r["id"] for r in rd["releases"]} == {release["id"], *[r["id"] for r in extra]}
    assert len(run(s, "detail", id=release["id"])["item"]["tracks"]) == 5
    assert run(s, "list", kind="work")["total"] == 2
    for g in (ga, gb):
        assert release["id"] in {i["id"] for i in run(s, "list", genre_id=g["id"])["items"]}
    track = run(s, "detail", id=release["tracks"][0]["id"])["item"]
    assert track["works"][0]["id"] == work["id"]
    assert {c["role"] for c in track["contributors"]} == {"composer", "conductor"}
    run(
        s,
        "save",
        id=work["id"],
        revision=work["revision"],
        object={"kind": "work", "title": work["title"], "genre_ids": [gb["id"]],
                "music_classes": ["classical"]},
    )
    assert run(s, "list", genre_id=ga["id"])["total"] == 0
    assert len(run(s, "detail", id=release["id"])["item"]["genres"]) == 1
    with pytest.raises(ValueError, match="music.genre_work_only"):
        save(title="Invalid release", genre_ids=[ga["id"]])
    updated = run(
        s,
        "track-save",
        id=track["id"],
        revision=track["revision"],
        track={
            "title": "Renamed movement",
            "position": 1,
            "relations": [{"object_id": recordings[1]["id"], "role": "recording"}],
        },
    )["item"]
    assert updated["id"] == track["id"] and updated["revision"] == track["revision"] + 1
    with pytest.raises(ValueError, match="music.revision_conflict"):
        run(
            s,
            "track-save",
            id=track["id"],
            revision=track["revision"],
            track={"title": "Stale", "position": 1},
        )


@pytest.mark.integration
def test_seven_star_history_and_independent_filters(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    rec = run(s, "save", object={"kind": "recording", "title": "Synthetic recording"})["item"]
    release = run(
        s,
        "save",
        object={
            "title": "Synthetic release",
            "relations": [{"object_id": rec["id"], "role": "recording"}],
        },
    )["item"]
    old = run(
        s, "review", id=release["id"], revision=release["revision"], rating=5, rating_scale=5
    )["item"]
    for rating in (5, 7, 0):
        rec = run(s, "review", id=rec["id"], revision=rec["revision"], rating=rating)["item"]
        assert rec["rating"] == (rating or None) and rec["rating_scale"] == 7
    assert [r["rating"] for r in rec["reviews"]] == [None, 7, 5]
    assert run(s, "detail", id=release["id"])["item"]["rating_scale"] == 5
    assert run(s, "list", kind="recording", unrated=True)["total"] == 1
    assert run(s, "list", kind="recording", rating_min=0, rating_max=0)["total"] == 1
    rec = run(s, "review", id=rec["id"], revision=rec["revision"], rating=7)["item"]
    assert run(s, "list", kind="recording", rating_min=5, rating_max=7)["total"] == 1
    assert run(s, "list", kind="release", rating_min=5, rating_max=7)["total"] == 0
    release = run(s, "review", id=old["id"], revision=old["revision"], rating=6)["item"]
    assert release["reviews"][1]["rating_max"] == 5 and release["reviews"][1]["rating"] == 5
    assert run(s, "list", rating_min=6, rating_max=6)["total"] == 1
    run(s, "membership", id=release["id"], revision=release["revision"], library="featured")
    assert run(s, "list", view="featured")["total"] == 1
    person = run(s, "save", object={"kind": "person", "title": "No ratings"})["item"]
    with pytest.raises(ValueError, match="music.rating_recording_release_only"):
        run(s, "review", id=person["id"], revision=person["revision"], rating=7)
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(*) FROM music_reviews WHERE scope_id=:s AND rating_max=5"),
                {"s": s.scope},
            )
            == 1
        )


@pytest.mark.integration
def test_people_relations_search_and_listening(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    person = run(
        s,
        "save",
        object={
            "kind": "person",
            "title": "Person",
            "aliases": ["Other name"],
        },
    )["item"]
    org = run(
        s,
        "save",
        object={
            "kind": "group",
            "title": "Orchestra",
            "relations": [
                {
                    "object_id": person["id"],
                    "role": "member",
                    "started_on": "2000-01-01",
                    "source_text": "Synthetic source",
                }
            ],
        },
    )["item"]
    assert org["relations"][0]["source_text"] == "Synthetic source"
    found = run(s, "list", query="Other name", cross_library=True)
    assert {row["id"] for row in found["items"]} == {person["id"], org["id"]}
    assert run(s, "list", kinds=["person", "group", "organization"])["total"] == 2
    assert run(s, "listening")["items"] == []
    work = run(
        s,
        "save",
        object={"kind": "work", "title": "Fixture conducting work", "music_classes": ["classical"]},
    )["item"]
    rec = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Dated recording",
            "recorded_on": "2020-01-01",
            "relations": [
                {"object_id": person["id"], "role": "conductor"},
                {"object_id": work["id"], "role": "work"},
            ],
        },
    )["item"]
    assert (
        run(
            s,
            "list",
            kind="recording",
            person_id=person["id"],
            date_from="2019-01-01",
            date_to="2021-01-01",
        )["total"]
        == 1
    )
    assert run(s, "list", kind="recording", date_from="2021-01-01")["total"] == 0
    for kind, day in [("manual", 1), ("playback", 2), ("external", 3)]:
        rec = run(
            s,
            "listen",
            id=rec["id"],
            revision=rec["revision"],
            started_at=f"2026-10-0{day}T12:00:00+08:00",
            actual_seconds=60,
            event_kind=kind,
            source_event_id=f"synthetic-{kind}",
        )["item"]
    history = run(s, "listening")
    assert {e["event_kind"] for e in history["items"]} == {"manual", "playback", "external"}
    assert len(history["daily"]) == 3 and history["monthly"][0]["events"] == 3
    assert sum(row["actual_seconds"] for row in history["weekly"]) == 180
    assert next(row for row in history["entities"] if row["id"] == person["id"])["events"] == 3
    with pytest.raises(ValueError, match="music.invalid_timezone"):
        run(s, "listening", timezone="Invalid/Zone")
