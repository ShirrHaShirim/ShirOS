"""Profile libraries and identity labels follow actual typed music relationships."""

from pathlib import Path

import pytest
from sqlalchemy import Engine
from test_music import run, service

from shiros.domains.music_profiles import identity_id


@pytest.mark.integration
def test_publisher_profile_lists_published_works_without_performance_credit(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    publisher = run(s, "save", object={"kind": "organization", "title": "Score Publisher"})["item"]
    parent = run(s, "save", object={"kind": "work", "title": "Published Cycle"})["item"]
    work = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Published Movement",
            "relations": [
                {"object_id": publisher["id"], "role": "publisher"},
                {"object_id": parent["id"], "role": "parent_work"},
            ],
        },
    )["item"]
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Independent Performance",
            "relations": [{"object_id": work["id"], "role": "work"}],
        },
    )["item"]
    run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Independent Issue",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )
    assert run(s, "profile-sections", id=publisher["id"])["items"] == [
        {"section": "work", "total": 1}
    ]
    direct = run(s, "list", profile_id=publisher["id"], profile_section="work")
    assert [item["id"] for item in direct["items"]] == [work["id"]]
    grouped = run(
        s, "list", profile_id=publisher["id"], profile_section="work", root_works=True
    )
    assert [item["id"] for item in grouped["items"]] == [parent["id"]]
    assert run(s, "list", profile_id=publisher["id"], profile_section="recording")["total"] == 0
    assert run(s, "list", profile_id=publisher["id"], profile_section="release")["total"] == 0


@pytest.mark.integration
def test_vocal_identity_uses_explicit_work_classes(db_engine: Engine, tmp_path: Path):
    s = service(db_engine, tmp_path)
    run(s, "taxonomy-seed")
    people = {}
    for name, classes, expected in (
        ("Rock Vocalist", ["rock"], {"歌手"}),
        ("Classical Vocalist", ["classical"], {"歌唱家"}),
        ("Unclassified Vocalist", [], {"歌手"}),
        ("Mixed Vocalist", ["rock", "classical"], {"歌手", "歌唱家"}),
    ):
        person = run(s, "save", object={"kind": "person", "title": name})["item"]
        work = run(
            s, "save", object={"kind": "work", "title": name + " Work", "music_classes": classes}
        )["item"]
        run(
            s,
            "save",
            object={
                "kind": "recording",
                "title": name + " Recording",
                "relations": [
                    {"object_id": work["id"], "role": "work"},
                    {"object_id": person["id"], "role": "singer"},
                ],
            },
        )
        labels = run(s, "detail", id=person["id"])["item"]["identities"]
        assert {x["name"] for x in labels if x["name"] in {"歌手", "歌唱家"}} == expected
        people[name] = person["id"]
    singers = run(s, "list", kind="person", identity_id=identity_id(s.scope, "歌手"))
    assert {x["id"] for x in singers["items"]} == {
        people[name] for name in ("Rock Vocalist", "Unclassified Vocalist", "Mixed Vocalist")
    }
    classical = run(s, "list", kind="person", identity_id=identity_id(s.scope, "歌唱家"))
    assert {x["id"] for x in classical["items"]} == {
        people[name] for name in ("Classical Vocalist", "Mixed Vocalist")
    }


@pytest.mark.integration
def test_profile_sections_and_derived_roles_follow_relationships(db_engine: Engine, tmp_path: Path):
    s = service(db_engine, tmp_path)
    run(s, "taxonomy-seed")
    person = run(s, "save", object={"kind": "person", "title": "Fixture Musician"})["item"]
    work = run(
        s,
        "save",
        object={"kind": "work", "title": "Fixture Symphony", "music_classes": ["classical"]},
    )["item"]
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Unreleased Performance",
            "relations": [
                {"object_id": work["id"], "role": "work"},
                {"object_id": person["id"], "role": "conductor"},
            ],
        },
    )["item"]
    assert run(s, "profile-sections", id=person["id"])["items"] == [
        {"section": "recording", "total": 1}
    ]
    labels = run(s, "detail", id=person["id"])["item"]["identities"]
    assert {label["name"] for label in labels} == {"音乐家", "指挥家"}
    assert all(label["derived"] for label in labels)
    for classes in (["rock"], []):
        with pytest.raises(ValueError, match="music.class_relation_mismatch"):
            run(
                s,
                "save",
                id=work["id"],
                revision=work["revision"],
                object={"kind": "work", "title": work["title"], "music_classes": classes},
            )
    assert (
        run(s, "list", kinds=["person"], identity_id=identity_id(s.scope, "指挥家"))["total"] == 1
    )
    work = run(
        s,
        "save",
        id=work["id"],
        revision=work["revision"],
        object={
            "kind": "work",
            "title": work["title"],
            "music_classes": ["classical"],
            "relations": [{"object_id": person["id"], "role": "composer"}],
        },
    )["item"]
    assert {x["section"] for x in run(s, "profile-sections", id=person["id"])["items"]} == {
        "work",
        "recording",
    }
    release = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Fixture Bootleg",
            "edition": "Bootleg",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
            "tracks": [
                {
                    "title": "Movement",
                    "position": 1,
                    "relations": [{"object_id": recording["id"], "role": "recording"}],
                }
            ],
        },
    )["item"]
    assert release["recording_ids"] == [recording["id"]]
    with pytest.raises(ValueError, match="music.release_recording_required"):
        run(s, "save", object={"kind": "release", "title": "Missing recording"})
    no_tracks = run(
        s,
        "save",
        object={
            "title": "Release without tracks",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
        },
    )["item"]
    with pytest.raises(ValueError, match="music.release_recording_required"):
        run(
            s,
            "save",
            id=no_tracks["id"],
            revision=no_tracks["revision"],
            object={"kind": "release", "title": "Removed all recordings"},
        )
    for section, title in [
        ("work", work["title"]),
        ("recording", recording["title"]),
        ("release", release["title"]),
    ]:
        assert title in {
            x["title"]
            for x in run(s, "list", profile_id=person["id"], profile_section=section)["items"]
        }
    # Last composition credit removal removes the section and only the automatic label.
    run(
        s,
        "save",
        id=work["id"],
        revision=work["revision"],
        object={"kind": "work", "title": work["title"], "music_classes": ["classical"]},
    )
    assert {x["section"] for x in run(s, "profile-sections", id=person["id"])["items"]} == {
        "recording",
        "release",
    }
    assert "作曲家" not in {
        x["name"] for x in run(s, "detail", id=person["id"])["item"]["identities"]
    }
    other = service(db_engine, tmp_path / "other")
    with pytest.raises(ValueError, match="music.not_found"):
        run(other, "profile-sections", id=person["id"])


@pytest.mark.integration
def test_multiple_classes_genres_subworks_and_credit_rules(db_engine: Engine, tmp_path: Path):
    s = service(db_engine, tmp_path)
    nodes = run(s, "taxonomy-seed")["items"]
    genres = {x["name"]: x["id"] for x in nodes if x["namespace"] == "genre"}
    artist = run(
        s,
        "save",
        object={
            "kind": "person",
            "title": "Fixture Guitarist",
            "identity_ids": [identity_id(s.scope, "音乐家")],
        },
    )["item"]
    album = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Fixture Album Work",
            "music_classes": ["rock", "classical"],
            "genre_ids": [genres["前卫摇滚"], genres["当代古典"]],
        },
    )["item"]
    song = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Fixture Song",
            "relations": [{"object_id": album["id"], "role": "parent_work"}],
        },
    )["item"]
    assert set(song["music_classes"]) == {"rock", "classical"}
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Fixture Mixed Recording",
            "relations": [
                {"object_id": song["id"], "role": "work"},
                {"object_id": artist["id"], "role": "performer", "performance_role": "主音吉他"},
                {"object_id": artist["id"], "role": "conductor"},
            ],
        },
    )["item"]
    assert {x["name"] for x in recording["genres"]} == {"前卫摇滚", "当代古典"}
    assert "主音吉他" in {
        x["name"] for x in run(s, "detail", id=artist["id"])["item"]["identities"]
    }
    assert (
        run(s, "list", kinds=["person"], identity_id=identity_id(s.scope, "主音吉他"))["total"] == 1
    )
    before = run(s, "list", kind="work")["total"]
    with pytest.raises(ValueError, match="music.class_genre_mismatch"):
        run(
            s,
            "save",
            object={
                "kind": "work",
                "title": "Invalid classical tag on rock",
                "music_classes": ["rock"],
                "genre_ids": [genres["古典"]],
            },
        )
    assert run(s, "list", kind="work")["total"] == before
    rock = run(s, "save", object={"kind": "work", "title": "Rock only", "music_classes": ["rock"]})[
        "item"
    ]
    with pytest.raises(ValueError, match="music.class_relation_mismatch"):
        run(
            s,
            "save",
            object={
                "kind": "recording",
                "title": "Invalid conductor",
                "relations": [
                    {"object_id": rock["id"], "role": "work"},
                    {"object_id": artist["id"], "role": "conductor"},
                ],
            },
        )
    with pytest.raises(ValueError, match="music.identity_from_relations"):
        run(
            s,
            "save",
            object={
                "kind": "person",
                "title": "Manual composer",
                "identity_ids": [identity_id(s.scope, "作曲家")],
            },
        )
    with pytest.raises(ValueError, match="music.identity_from_relations"):
        run(s, "taxonomy-save", namespace="identity", name="主音吉他")
    with pytest.raises(ValueError, match="music.identity_from_relations"):
        run(s, "save", object={"kind": "person", "title": "Manual conductor", "roles": ["指挥家"]})
    # Explicit broad classification survives credit deletion, inferred subroles do not.
    run(
        s,
        "save",
        id=recording["id"],
        revision=recording["revision"],
        object={
            "kind": "recording",
            "title": recording["title"],
            "relations": [{"object_id": song["id"], "role": "work"}],
        },
    )
    labels = run(s, "detail", id=artist["id"])["item"]["identities"]
    assert [x["name"] for x in labels] == ["音乐家"] and not labels[0]["derived"]


@pytest.mark.integration
def test_profile_members_and_sorting(db_engine: Engine, tmp_path: Path):
    s = service(db_engine, tmp_path)
    members = [
        run(s, "save", object={"kind": "person", "title": name})["item"] for name in ["B", "A"]
    ]
    group = run(
        s,
        "save",
        object={
            "kind": "group",
            "title": "Fixture Band",
            "relations": [{"object_id": x["id"], "role": "member"} for x in members],
        },
    )["item"]
    assert run(s, "profile-sections", id=group["id"])["items"] == [
        {"section": "members", "total": 2}
    ]
    for sort, expected in [
        ("title_asc", ["A", "B"]),
        ("title_desc", ["B", "A"]),
        ("newest", ["A", "B"]),
    ]:
        result = run(
            s, "list", profile_id=group["id"], profile_section="members", sort=sort, limit=1
        )
        second = run(
            s,
            "list",
            profile_id=group["id"],
            profile_section="members",
            sort=sort,
            limit=1,
            offset=1,
        )
        assert [result["items"][0]["title"], second["items"][0]["title"]] == expected
        assert result["total"] == 2
