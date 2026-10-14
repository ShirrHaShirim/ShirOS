"""Arrangers are distinct credits; linked authorship is not a performance credit."""

from pathlib import Path

import pytest
from sqlalchemy import Engine
from test_music import run, service

from shiros.domains.music_profiles import identity_id


@pytest.mark.integration
def test_arranger_roundtrip_inheritance_search_and_identity(db_engine: Engine, tmp_path: Path):
    s = service(db_engine, tmp_path)
    run(s, "taxonomy-seed")
    arranger = run(
        s,
        "save",
        object={"kind": "person", "title": "Fixture Arranger", "aliases": ["Arrangement Alias"]},
    )["item"]
    composer = run(s, "save", object={"kind": "person", "title": "Fixture Composer"})["item"]
    work = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Fixture Arrangement",
            "relations": [
                {"object_id": composer["id"], "role": "composer"},
                {
                    "object_id": arranger["id"],
                    "role": "arranger",
                    "source_text": "Synthetic credit",
                    "started_on": "2020-01-01",
                },
            ],
        },
    )["item"]
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Fixture Performance",
            "relations": [{"object_id": work["id"], "role": "work"}],
        },
    )["item"]
    release = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Fixture Issue",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )["item"]
    saved = run(s, "detail", id=work["id"])["item"]
    credit = next(row for row in saved["relations"] if row["role"] == "arranger")
    assert credit["source_text"] == "Synthetic credit"
    assert str(credit["started_on"]) == "2020-01-01"
    for item in (recording, release):
        assert any(
            row["id"] == arranger["id"] and row["role"] == "arranger"
            for row in item["contributors"]
        )
    labels = {row["name"] for row in run(s, "detail", id=arranger["id"])["item"]["identities"]}
    assert "改编者" in labels and "作曲家" not in labels
    assert run(s, "profile-sections", id=arranger["id"])["items"] == [
        {"section": "work", "total": 1}
    ]
    assert (
        run(s, "list", kind="person", identity_id=identity_id(s.scope, "改编者"))["items"][0]["id"]
        == arranger["id"]
    )
    for kind, expected in (("work", work), ("recording", recording), ("release", release)):
        assert expected["id"] in {
            row["id"] for row in run(s, "list", kind=kind, query="Arrangement Alias")["items"]
        }
    updated = run(
        s,
        "save",
        id=work["id"],
        revision=work["revision"],
        object={
            "kind": "work",
            "title": work["title"],
            "relations": [
                {"object_id": arranger["id"], "role": "composer"},
                {"object_id": arranger["id"], "role": "arranger"},
            ],
        },
    )["item"]
    assert {r["role"] for r in updated["relations"]} == {"composer", "arranger"}


@pytest.mark.integration
def test_arranger_targets_subjects_and_track_promotion(db_engine: Engine, tmp_path: Path):
    s = service(db_engine, tmp_path)
    person, group, organization = [
        run(s, "save", object={"kind": kind, "title": "Synthetic " + kind})["item"]
        for kind in ("person", "group", "organization")
    ]
    credits = [{"object_id": person["id"], "role": "arranger"}]
    for target in (group, organization):
        for kind in ("work", "recording"):
            with pytest.raises(ValueError, match="music.invalid_relation"):
                run(
                    s,
                    "save",
                    object={
                        "kind": kind,
                        "title": "Invalid non-person arranger",
                        "relations": [{"object_id": target["id"], "role": "arranger"}],
                    },
                )
    candidates = run(
        s, "relation-options", kinds=["person", "group", "organization"], relation_role="arranger"
    )
    assert {str(x["id"]) for x in candidates["items"]} == {str(person["id"])}
    work = run(s, "save", object={"kind": "work", "title": "Synthetic Work", "relations": credits})[
        "item"
    ]
    recording = run(
        s,
        "save",
        object={"kind": "recording", "title": "Synthetic Recording", "relations": credits},
    )["item"]
    release = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Synthetic Release",
            "relations": [*credits, {"object_id": recording["id"], "role": "recording"}],
            "tracks": [{"position": 1, "title": "Synthetic Track", "relations": credits}],
        },
    )["item"]
    track = run(s, "detail", id=release["tracks"][0]["id"])["item"]
    for item in (work, recording, release, track):
        assert sum(r["role"] == "arranger" for r in item["relations"]) == 1
    promoted = run(
        s, "track-promote", id=track["id"], revision=track["revision"], recording_id=recording["id"]
    )
    child = run(s, "detail", id=promoted["item"]["id"])["item"]
    assert sum(r["role"] == "arranger" for r in child["relations"]) == 1
    with pytest.raises(ValueError, match="music.invalid_relation"):
        run(s, "save", object={"kind": "person", "title": "Invalid Subject", "relations": credits})
    with pytest.raises(ValueError, match="music.invalid_relation"):
        run(
            s,
            "save",
            object={
                "kind": "work",
                "title": "Invalid Target",
                "relations": [{"object_id": work["id"], "role": "arranger"}],
            },
        )
    with pytest.raises(ValueError, match="music.invalid_relation"):
        run(
            s,
            "save",
            object={
                "kind": "recording",
                "title": "Invalid Instrument",
                "relations": [
                    {"object_id": person["id"], "role": "arranger", "performance_role": "小提琴"}
                ],
            },
        )
