from pathlib import Path

import pytest
from sqlalchemy import Engine
from test_music import run, service


@pytest.mark.integration
def test_name_and_alias_match_rank_before_associations_with_stable_pages(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    mozart = run(
        s,
        "save",
        object={"kind": "person", "title": "Wolfgang Amadeus Mozart", "aliases": ["Mozart"]},
    )["item"]
    work = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Piano Concerto",
            "relations": [{"object_id": mozart["id"], "role": "composer"}],
        },
    )["item"]
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Concert Session",
            "relations": [{"object_id": work["id"], "role": "work"}],
        },
    )["item"]
    first = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Mozart: Piano Concerto",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )["item"]
    second = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "The Mozart Sessions",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )["item"]
    alias = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Historic Edition",
            "aliases": ["Selected Mozart"],
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )["item"]
    related = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "A Newer Collection",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )["item"]
    expected = [first["id"], second["id"], alias["id"], related["id"]]
    for sort in ("newest", "title_asc", "title_desc"):
        result = run(s, "list", kind="release", query="Mozart", sort=sort)
        assert result["total"] == 4
        assert [x["id"] for x in result["items"]] == expected
    assert (
        run(s, "list", kind="release", query="Mozart", limit=1, offset=2)["items"][0]["id"]
        == alias["id"]
    )
    assert run(s, "list", kind="person", query="  mozart  ")["items"][0]["id"] == mozart["id"]
    assert run(s, "list", kind="release", sort="newest")["items"][0]["id"] == related["id"]
    assert run(s, "list", kind="release", query="Mozart Sessions")["items"][0]["id"] == second["id"]
