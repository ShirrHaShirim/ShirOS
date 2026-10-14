"""Text and association searches remain scoped and follow music credits."""

from pathlib import Path

import pytest
from sqlalchemy import Engine
from test_music import run, service


@pytest.mark.integration
def test_combined_search_credits_aliases_tracks_and_literal_characters(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    performer = run(
        s, "save", object={"kind": "person", "title": "Ada Player", "aliases": ["AP alias"]}
    )["item"]
    composer = run(
        s, "save", object={"kind": "person", "title": "Composer Person", "aliases": ["CP alias"]}
    )["item"]
    work = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Night Sonata",
            "relations": [{"object_id": composer["id"], "role": "composer"}],
        },
    )["item"]
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Evening Session",
            "relations": [
                {"object_id": work["id"], "role": "work"},
                {"object_id": performer["id"], "role": "performer"},
            ],
        },
    )["item"]
    release = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Night Edition 100%_",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )["item"]
    track_release = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Track Credit Edition",
            "tracks": [
                {
                    "title": "Evening Track",
                    "position": 1,
                    "relations": [{"object_id": recording["id"], "role": "recording"}],
                }
            ],
        },
    )["item"]
    unrelated = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Other Night Edition 100X",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )["item"]
    for query in (
        "night ada",
        "player night",
        "Night AP alias",
        " night   CP alias ",
        "100%_",
        "%",
        "_",
    ):
        result = run(s, "list", kind="release", query=query)
        expected = (
            {release["id"], unrelated["id"], track_release["id"]}
            if "night" in query.lower()
            else {release["id"]}
        )
        assert {row["id"] for row in result["items"]} == expected
    assert run(s, "list", kind="release", query="night missing")["total"] == 0
    assert (
        run(s, "list", kind="release", query="track AP alias")["items"][0]["id"]
        == track_release["id"]
    )
    assert (
        run(s, "list", kind="recording", query="session CP alias")["items"][0]["id"]
        == recording["id"]
    )
    assert (
        run(s, "list", kind="release", query="Night Edition", person_id=performer["id"])["total"]
        == 3
    )
    assert run(s, "relation-options", kinds=["person"], query="player ada")["items"][0][
        "id"
    ] == str(performer["id"])
    assert run(s, "relation-options", kinds=["person"], query="%")["total"] == 0
    assert run(s, "list", kind="release", query="Sonata")["total"] == 3
    assert (
        run(s, "list", kind="recording", query="Track Credit Edition")["items"][0]["id"]
        == recording["id"]
    )
    assert run(s, "list", kind="work", query="Track Credit Edition")["items"][0]["id"] == work["id"]
    assert run(s, "list", kind="work", query="AP alias")["items"][0]["id"] == work["id"]
    assert run(s, "list", kind="person", query="Night Edition")["total"] == 2
    second = run(s, "save", object={"kind": "person", "title": "Second Player"})["item"]
    run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Different Session",
            "relations": [
                {"object_id": work["id"], "role": "work"},
                {"object_id": second["id"], "role": "performer"},
            ],
        },
    )
    # Sharing a composition does not give an unrelated recording another player's credit.
    assert run(s, "list", kind="release", query="Second Player")["total"] == 0
    other = service(db_engine, tmp_path / "other")
    run(other, "save", object={"kind": "person", "title": "Ada Another Player"})
    assert run(s, "relation-options", kinds=["person"], query="Ada")["total"] == 1
    assert run(other, "list", kind="release", query="Night")["total"] == 0
