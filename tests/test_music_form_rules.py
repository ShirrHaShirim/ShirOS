"""Type-specific relations, preserved historical links and multiple credits."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text
from test_music import service

from shiros.domains.music import MusicRequest, MusicService, ObjectInput, TrackInput


def run(s: MusicService, action: str, **data: Any) -> dict[str, Any]:
    return s.run(action, MusicRequest.model_validate(data))


@pytest.mark.integration
def test_multiple_credits_and_invalid_subjects(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    people = [
        run(s, "save", object={"kind": "person", "title": f"Author {n}"})["item"] for n in range(3)
    ]
    work = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Joint composition",
            "relations": [{"object_id": p["id"], "role": "composer"} for p in people],
        },
    )["item"]
    assert len(work["relations"]) == 3
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Ensemble performance",
            "relations": [{"object_id": work["id"], "role": "work"}]
            + [{"object_id": p["id"], "role": "performer"} for p in people],
        },
    )["item"]
    assert len(recording["relations"]) == 4
    for subject, role in [
        ("work", "conductor"),
        ("person", "composer"),
        ("group", "performer"),
        ("organization", "singer"),
    ]:
        before = run(s, "list", kind=subject)["total"]
        with pytest.raises(ValueError, match="music.invalid_relation"):
            run(
                s,
                "save",
                object={
                    "kind": subject,
                    "title": "Invalid synthetic credit",
                    "relations": [{"object_id": people[0]["id"], "role": role}],
                },
            )
        assert run(s, "list", kind=subject)["total"] == before
    with pytest.raises(ValueError, match="music.invalid_relation"):
        run(
            s,
            "save",
            id=work["id"],
            revision=work["revision"],
            object={
                "kind": "work",
                "title": work["title"],
                "relations": [{"object_id": work["id"], "role": "work"}],
            },
        )
    unchanged = run(s, "detail", id=work["id"])["item"]
    assert unchanged["revision"] == work["revision"]
    assert len(unchanged["relations"]) == 3


@pytest.mark.integration
def test_preserve_existing_legacy_credit_without_allowing_new_one(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    s = service(db_engine, tmp_path)
    person = run(s, "save", object={"kind": "person", "title": "Legacy participant"})["item"]
    other = run(s, "save", object={"kind": "person", "title": "New participant"})["item"]
    work = run(s, "save", object={"kind": "work", "title": "Legacy work"})["item"]
    # Reproduce a credit accepted by the older service in an isolated synthetic scope.
    with db_engine.begin() as c:
        c.execute(
            text("INSERT INTO music_relations VALUES (:s,:id,:p,'conductor')"),
            {"s": work["scope_id"], "id": work["id"], "p": person["id"]},
        )
    saved = run(
        s,
        "save",
        id=work["id"],
        revision=work["revision"],
        object={
            "kind": "work",
            "title": "Legacy work updated",
            "relations": [{"object_id": person["id"], "role": "conductor"}],
        },
    )["item"]
    assert saved["relations"][0]["object_id"] == person["id"]
    with pytest.raises(ValueError, match="music.invalid_relation"):
        run(
            s,
            "save",
            id=saved["id"],
            revision=saved["revision"],
            object={
                "kind": "work",
                "title": saved["title"],
                "relations": [{"object_id": other["id"], "role": "conductor"}],
            },
        )


@pytest.mark.parametrize("kind", ["person", "group", "organization", "recording"])
def test_catalogue_rejected_outside_work_or_release(kind: str) -> None:
    with pytest.raises(ValidationError, match="music.catalogue_fields"):
        ObjectInput(kind=kind, title="Synthetic", catalogue="Op. 1")  # type: ignore[arg-type]


@pytest.mark.parametrize("title", [" ", "\t\n"])
def test_whitespace_track_name_rejected(title: str) -> None:
    with pytest.raises(ValidationError, match="music.invalid_track"):
        TrackInput(title=title, position=1)
