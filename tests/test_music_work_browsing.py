"""Grouped Work navigation keeps children reachable and searches library-scoped."""

from pathlib import Path

import pytest
from sqlalchemy import Engine
from test_music import run, service


@pytest.mark.integration
def test_numbered_collection_children_sort_numerically_without_catalogue(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    parent = run(s, "save", object={"kind": "work", "title": "Quartet cycle",
                                   "work_form": "collection"})["item"]
    children = {}
    for number in (10, 2, 12):
        children[number] = run(s, "save", object={"kind": "work",
            "title": f"String Quartet No. {number}",
            "relations": [{"object_id": parent["id"], "role": "parent_work"}]})["item"]
    result = run(s, "list", kind="work", parent_work_id=parent["id"], sort="work_order")
    assert [x["id"] for x in result["items"]] == [children[n]["id"] for n in (2, 10, 12)]


@pytest.mark.integration
def test_album_roots_search_children_and_paginate_without_duplicates(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    nodes = run(s, "taxonomy-seed")["items"]
    rock = next(x["id"] for x in nodes if x["name"] == "前卫摇滚")
    author = run(s, "save", object={"kind": "person", "title": "Song Author"})["item"]
    album = run(s, "save", object={"kind": "work", "title": "Album", "music_classes": ["rock"]})[
        "item"
    ]
    children = []
    for i in range(27):
        children.append(
            run(
                s,
                "save",
                object={
                    "kind": "work",
                    "title": f"Song {i:02}",
                    "aliases": [f"Alias {i:02}"],
                    "genre_ids": [rock] if i == 0 else [],
                    "relations": [
                        {"object_id": album["id"], "role": "parent_work"},
                        {"object_id": author["id"], "role": "composer"},
                    ],
                },
            )["item"]
        )
    nested = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Nested Independent Work",
            "relations": [{"object_id": children[0]["id"], "role": "parent_work"}],
        },
    )["item"]
    independent = run(s, "save", object={"kind": "work", "title": "Independent Work"})["item"]
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Unique Recording Search",
            "relations": [{"object_id": children[0]["id"], "role": "work"}],
        },
    )["item"]
    run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Unique Release Search",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )

    roots = run(s, "list", kind="work", root_works=True, sort="title_asc")
    assert roots["total"] == 2
    assert [x["id"] for x in roots["items"]] == [album["id"], independent["id"]]
    assert roots["items"][0]["child_work_count"] == 27
    # Explicit complete lists and relation candidates still contain individual songs.
    assert run(s, "list", kind="work")["total"] == 30
    assert run(s, "relation-options", kind="work", query="Song 00")["total"] == 1
    for query in ("Song", "Alias 00", "Nested Independent", "Song Author", "前卫摇滚"):
        result = run(s, "list", kind="work", root_works=True, query=query)
        assert result["total"] == 1 and result["items"][0]["id"] == album["id"]
    assert run(s, "list", kind="work", root_works=True, genre_id=rock)["total"] == 1
    first = run(s, "list", kind="work", parent_work_id=album["id"], limit=24, sort="title_asc")
    last = run(
        s,
        "list",
        kind="work",
        parent_work_id=album["id"],
        offset=24,
        limit=24,
        sort="title_asc",
    )
    assert first["total"] == last["total"] == 27
    assert [x["id"] for x in first["items"] + last["items"]] == [x["id"] for x in children]
    assert (
        run(s, "list", kind="work", parent_work_id=children[0]["id"])["items"][0]["id"]
        == nested["id"]
    )
    assert run(s, "list", kind="recording", work_id=album["id"])["total"] == 0
    assert (
        run(s, "list", kind="recording", work_id=children[0]["id"])["items"][0]["id"]
        == recording["id"]
    )
    assert (
        run(s, "list", kind="recording", work_id=album["id"], include_child_works=True)["items"][0][
            "id"
        ]
        == recording["id"]
    )
    album_recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Complete Album Recording",
            "relations": [{"object_id": album["id"], "role": "work"}],
        },
    )["item"]
    direct = run(s, "list", kind="recording", work_id=album["id"], limit=1)
    assert direct["total"] == 1 and direct["items"][0]["id"] == album_recording["id"]
    assert run(s, "list", kind="recording", work_id=album["id"], query="Unique")["total"] == 0
    for query in ("Unique Recording Search", "Unique Release Search"):
        # Associated names find the Work while the results stay in the Work library.
        result = run(s, "list", kind="work", root_works=True, query=query)
        assert result["total"] == 1 and result["items"][0]["id"] == album["id"]
        assert {row["id"] for row in run(s, "list", kind="work", query=query)["items"]} == {
            album["id"],
            children[0]["id"],
        }
        assert any(
            x["title"] == query for x in run(s, "list", cross_library=True, query=query)["items"]
        )

    # Song authorship can group the album in the Work column, but never creates
    # Recording or Release entries for the composer.
    assert {
        x["section"]: x["total"]
        for x in run(s, "profile-sections", id=author["id"], root_works=True)["items"]
    } == {"work": 1}
    assert run(s, "list", kind="recording", person_id=author["id"])["total"] == 0
    assert run(s, "list", kind="release", person_id=author["id"])["total"] == 0
    assert run(s, "list", kind="work", root_works=True, person_id=author["id"])["total"] == 1
    grouped = run(s, "list", profile_id=author["id"], profile_section="work", root_works=True)
    assert grouped["total"] == 1 and grouped["items"][0]["id"] == album["id"]
    assert run(s, "list", profile_id=author["id"], profile_section="work")["total"] == 27
    other = service(db_engine, tmp_path / "other")
    assert run(other, "list", kind="work", root_works=True, query="Song")["total"] == 0
    with pytest.raises(ValueError, match="music.not_found"):
        run(other, "list", kind="work", parent_work_id=album["id"])
    with pytest.raises(ValueError, match="music.invalid_relation"):
        run(s, "list", kind="work", parent_work_id=recording["id"])
    with pytest.raises(ValueError, match="music.invalid_filter"):
        run(s, "list", kind="work", root_works=True, parent_work_id=album["id"])
    with pytest.raises(ValueError, match="music.invalid_filter"):
        run(s, "list", kind="work", root_works=True, cross_library=True)

    # Removing the explicit parent link makes that song a top-level Work again.
    song = children[-1]
    run(
        s,
        "save",
        id=song["id"],
        revision=song["revision"],
        object={"kind": "work", "title": song["title"]},
    )
    assert run(s, "list", kind="work", root_works=True)["total"] == 3
    assert run(s, "list", kind="work", parent_work_id=album["id"])["total"] == 26


@pytest.mark.integration
def test_release_credit_roles_are_explicit_and_work_authorship_does_not_spread(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    people = [
        run(s, "save", object={"kind": "person", "title": name})["item"]
        for name in ("Work Author", "Actual Conductor", "Track Performer")
    ]
    publisher = run(s, "save", object={"kind": "organization", "title": "Publisher"})["item"]
    work = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Composition",
            "music_classes": ["classical"],
            "relations": [{"object_id": people[0]["id"], "role": "composer"}],
        },
    )["item"]
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Performance",
            "relations": [
                {"object_id": work["id"], "role": "work"},
                {"object_id": people[1]["id"], "role": "conductor"},
            ],
        },
    )["item"]
    release = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Track-linked Release",
            "relations": [
                {"object_id": publisher["id"], "role": "publisher"},
            ],
            "tracks": [
                {
                    "title": "Part",
                    "position": 1,
                    "relations": [
                        {"object_id": recording["id"], "role": "recording"},
                        {
                            "object_id": people[2]["id"],
                            "role": "performer",
                            "performance_role": "小提琴",
                        },
                    ],
                }
            ],
        },
    )["item"]
    credits = release["contributors"]
    shown = [x for x in credits if x["origin_kind"] != "work"]
    assert {(x["id"], x["role"], x["performance_role"]) for x in shown} == {
        (people[1]["id"], "conductor", ""),
        (people[2]["id"], "performer", "小提琴"),
        (publisher["id"], "publisher", ""),
    }
    assert {x["origin_kind"] for x in shown} == {"recording", "track", "release"}
    assert all(x["origin_id"] and x["origin_title"] for x in shown)
    assert {x["section"] for x in run(s, "profile-sections", id=people[0]["id"])["items"]} == {
        "work"
    }
    for person in people[1:]:
        assert {x["section"] for x in run(s, "profile-sections", id=person["id"])["items"]} == {
            "recording",
            "release",
        }
        assert (
            run(s, "list", kind="recording", person_id=person["id"])["items"][0]["id"]
            == recording["id"]
        )
        assert (
            run(s, "list", kind="release", person_id=person["id"])["items"][0]["id"]
            == release["id"]
        )
    # Removing the actual credit removes the profile entries; the Work stays linked.
    run(
        s,
        "save",
        id=recording["id"],
        revision=recording["revision"],
        object={
            "kind": "recording",
            "title": recording["title"],
            "relations": [{"object_id": work["id"], "role": "work"}],
        },
    )
    assert run(s, "profile-sections", id=people[1]["id"])["items"] == []
    current = run(s, "detail", id=release["id"])["item"]
    assert not any(x["id"] == people[1]["id"] for x in current["contributors"])
