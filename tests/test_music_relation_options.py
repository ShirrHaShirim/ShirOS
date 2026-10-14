"""Relation pickers use bounded, scope-bound summaries instead of full catalogues."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from test_music import run, service

from shiros.domains import music
from shiros.domains.music import MusicService
from shiros.memory_gateway import MemoryGateway


@pytest.mark.integration
def test_group_members_are_people_not_suborganizations(db_engine: Engine, tmp_path: Path):
    s = service(db_engine, tmp_path)
    person, group, organization = [
        run(s, "save", object={"kind": kind, "title": "Member target " + kind})["item"]
        for kind in ("person", "group", "organization")
    ]
    for role in ("member", "former_member"):
        result = run(
            s, "relation-options", kinds=["person", "group", "organization"], relation_role=role
        )
        assert {str(x["id"]) for x in result["items"]} == {str(person["id"])}
        for target in (group, organization):
            with pytest.raises(ValueError, match="music.invalid_relation"):
                run(
                    s,
                    "save",
                    object={
                        "kind": "group",
                        "title": "Invalid membership",
                        "relations": [{"object_id": target["id"], "role": role}],
                    },
                )
    parent = run(
        s,
        "save",
        object={
            "kind": "organization",
            "title": "Parent institution",
            "relations": [{"object_id": group["id"], "role": "suborganization"}],
        },
    )["item"]
    assert parent["relations"][0]["role"] == "suborganization"




@pytest.mark.integration
def test_relation_options_are_paged_searchable_and_lightweight(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = service(db_engine, tmp_path)
    first = run(s, "save", object={"kind": "recording", "title": "A Recording"})["item"]
    for title in ("B Recording", "B Recording", "C Recording"):
        run(s, "save", object={"kind": "recording", "title": title})
    person = run(
        s, "save", object={"kind": "person", "title": "Named Person", "aliases": ["Find Alias"]}
    )["item"]
    other = service(db_engine, tmp_path / "other")
    run(other, "save", object={"kind": "recording", "title": "A Other Scope"})

    def no_detail(*args: object, **kwargs: object) -> None:
        pytest.fail("The relation picker must not assemble full object details")

    monkeypatch.setattr(music, "_detail", no_detail)
    page = run(s, "relation-options", kinds=["recording"], limit=2)
    following = run(s, "relation-options", kinds=["recording"], limit=2, offset=2)
    assert page["total"] == following["total"] == 4
    assert page["offset"] == 0 and following["offset"] == 2
    rows = page["items"] + following["items"]
    assert len({row["id"] for row in rows}) == 4
    assert rows == sorted(rows, key=lambda row: (row["title"], UUID(row["id"])))
    assert all(set(row) == {"id", "kind", "title", "aliases"} for row in rows)
    assert run(s, "relation-options", kinds=["recording"], id=first["id"])["total"] == 3
    assert run(s, "relation-options", kinds=["recording"], offset=99)["items"] == []
    found = run(s, "relation-options", kinds=["person", "group"], query=" find alias ")
    assert found["items"] == [
        {
            "id": str(person["id"]),
            "kind": "person",
            "title": "Named Person",
            "aliases": ["Find Alias"],
        }
    ]
    assert run(s, "relation-options", kinds=["recording"], query="find alias")["total"] == 0
    assert run(s, "relation-options", kinds=["person"], query="NAMED")["total"] == 1

    with db_engine.begin() as c:
        c.execute(
            text("UPDATE local_grants SET role='reader' WHERE actor_id=:a AND scope_id=:s"),
            {"a": s.actor, "s": s.scope},
        )
    gateway = MemoryGateway(db_engine, s.actor, s.scope, "synthetic-reader")
    read = asyncio.run(gateway.music_query("relation-options", {"kinds": ["person"]}))
    assert read["total"] == 1 and read["content_is_untrusted_data"]
    with pytest.raises(PermissionError):
        run(MusicService(db_engine, other.actor, s.scope), "relation-options", kinds=["person"])
