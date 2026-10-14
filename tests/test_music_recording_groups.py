"""Explicit recording containment, reuse, lazy reads and opt-in track promotion."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine
from test_music import run, service
from test_music_image_framing import image_data
from test_music_recording_units import credit, save


@pytest.mark.integration
def test_recording_release_lookup_unions_direct_and_track_links_without_duplicates(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    wanted = run(s, "save", object={"kind": "recording", "title": "Lookup target"})["item"]
    other = run(s, "save", object={"kind": "recording", "title": "Other session"})["item"]
    direct = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Both link paths",
            "relations": [{"object_id": wanted["id"], "role": "recording"}],
            "tracks": [
                {
                    "title": "Also direct",
                    "position": 1,
                    "relations": [{"object_id": wanted["id"], "role": "recording"}],
                }
            ],
        },
    )["item"]
    track_only = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Track-only inclusion",
            "relations": [{"object_id": other["id"], "role": "recording"}],
            "tracks": [
                {
                    "title": "Reused target",
                    "position": 1,
                    "relations": [{"object_id": wanted["id"], "role": "recording"}],
                }
            ],
        },
    )["item"]
    unrelated = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Unrelated issue",
            "relations": [{"object_id": other["id"], "role": "recording"}],
        },
    )["item"]
    result = run(s, "list", kind="release", recording_id=wanted["id"], limit=1)
    second = run(s, "list", kind="release", recording_id=wanted["id"], limit=1, offset=1)
    assert result["total"] == second["total"] == 2
    assert {result["items"][0]["id"], second["items"][0]["id"]} == {direct["id"], track_only["id"]}
    assert unrelated["id"] not in {row["id"] for row in result["items"]}
    assert (
        run(s, "list", kind="release", recording_id=wanted["id"], query="Track-only")["total"] == 1
    )




@pytest.mark.integration
def test_child_order_prefers_parent_album_then_numeric_work_number(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    parent = save(s, "recording", "Quartet performances")
    children = {}
    for number in (10, 2, 12):
        children[number] = save(
            s, "recording", f"Quartet No. {number} — Ensemble", [credit(parent, "parent_recording")]
        )

    def ordered():
        return [
            x["id"]
            for x in run(
                s,
                "list",
                kind="recording",
                parent_recording_id=parent["id"],
                sort="recording_order",
            )["items"]
        ]

    assert ordered() == [children[n]["id"] for n in (2, 10, 12)]
    # A compilation containing the performances does not dictate this album's order.
    save(
        s,
        "release",
        "Unrelated compilation",
        tracks=[
            {
                "disc": 1,
                "position": i,
                "title": f"Excerpt {n}",
                "relations": [credit(children[n], "recording")],
            }
            for i, n in enumerate((12, 10, 2), 1)
        ],
    )
    assert ordered() == [children[n]["id"] for n in (2, 10, 12)]
    save(
        s,
        "release",
        "Original two-disc album",
        [credit(parent, "recording")],
        tracks=[
            {
                "disc": disc,
                "position": pos,
                "title": f"Quartet {n}",
                "relations": [credit(children[n], "recording")],
            }
            for disc, pos, n in ((1, 1, 12), (1, 2, 2), (2, 1, 10))
        ],
    )
    assert ordered() == [children[n]["id"] for n in (12, 2, 10)]
    page = run(
        s,
        "list",
        kind="recording",
        parent_recording_id=parent["id"],
        sort="recording_order",
        offset=1,
        limit=1,
    )
    assert page["total"] == 3 and page["items"][0]["id"] == children[2]["id"]


@pytest.mark.integration
def test_child_order_uses_related_catalogue_when_titles_have_no_number(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    parent = save(s, "recording", "Bach performances")
    children = []
    for title, code in (("Adagio", "BWV 1019"), ("Vivace", "BWV 1001")):
        work = save(s, "work", title, catalogue=code)
        children.append(
            save(s, "recording", title, [credit(parent, "parent_recording"), credit(work, "work")])
        )
    ordered = run(
        s, "list", kind="recording", parent_recording_id=parent["id"], sort="recording_order"
    )["items"]
    assert [x["id"] for x in ordered] == [x["id"] for x in reversed(children)]


@pytest.mark.integration
def test_reusable_children_root_search_paging_and_no_child_reviews(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    author = save(s, "person", "Distinct performer")
    first = save(s, "work", "Song Azure", music_classes=["rock"])
    second = save(s, "work", "Song Ochre", music_classes=["rock"])
    album = save(s, "recording", "Original Album")
    anthology = save(s, "recording", "Greatest Hits")
    complete = save(s, "recording", "Complete Bach Session")
    a = save(
        s,
        "recording",
        "Azure performance",
        [
            credit(first, "work"),
            credit(author, "performer"),
        ],
    )
    b = save(
        s,
        "recording",
        "Ochre performance",
        [credit(second, "work"), credit(album, "parent_recording")],
    )
    rated = run(
        s,
        "review",
        id=a["id"],
        revision=a["revision"],
        rating=7,
        rating_scale=7,
        comment="Single only",
    )["item"]
    a = run(
        s,
        "save",
        id=a["id"],
        revision=rated["revision"],
        object=dict(
            kind="recording",
            title=a["title"],
            relations=[
                credit(first, "work"),
                credit(author, "performer"),
                credit(album, "parent_recording"),
                credit(anthology, "parent_recording"),
            ],
        ),
    )["item"]
    assert a["rating"] is None and a["reviews"] == rated["reviews"]
    for action in ("review", "review-history-add", "review-import", "review-delete"):
        with pytest.raises(ValueError, match="music.child_recording_review_disabled"):
            run(s, action, id=a["id"], revision=a["revision"], rating=5)
    assert (
        run(s, "list", kind="recording", parent_recording_id=album["id"], rating_min=1)["total"]
        == 0
    )
    assert (
        run(s, "list", kind="recording", parent_recording_id=album["id"], unrated=True)["total"]
        == 0
    )

    edition = save(
        s,
        "release",
        "Original Edition",
        [credit(album, "recording")],
        tracks=[
            dict(title="Ochre", position=1, relations=[credit(b, "recording")]),
            dict(title="Azure", position=2, relations=[credit(a, "recording")]),
        ],
    )
    hits = save(
        s,
        "release",
        "Hits Edition",
        [credit(anthology, "recording")],
        tracks=[dict(title="Azure reused", position=1, relations=[credit(a, "recording")])],
    )
    assert run(s, "list", kind="recording")["total"] == 5
    roots = run(s, "list", kind="recording", root_recordings=True)
    assert {x["id"] for x in roots["items"]} == {album["id"], anthology["id"], complete["id"]}
    assert next(x for x in roots["items"] if x["id"] == album["id"])["child_recording_count"] == 2
    for query in ("Azure", "Distinct performer", "Song Azure"):
        assert {
            x["id"]
            for x in run(s, "list", kind="recording", root_recordings=True, query=query)["items"]
        } == {album["id"], anthology["id"]}
    assert (
        run(s, "list", kind="recording", parent_recording_id=album["id"], query="Ochre")["total"]
        == 1
    )
    assert (
        run(s, "list", kind="recording", query="Ochre", parent_recording_id=album["id"])["items"][
            0
        ]["id"]
        == b["id"]
    )
    assert (
        run(s, "list", kind="recording", query="Greatest Hits", parent_recording_id=album["id"])[
            "total"
        ]
        == 0
    )
    page = run(
        s,
        "list",
        kind="recording",
        parent_recording_id=album["id"],
        sort="recording_order",
        limit=1,
    )
    assert page["total"] == 2 and page["items"][0]["id"] == b["id"]
    assert (
        run(
            s,
            "list",
            kind="recording",
            parent_recording_id=album["id"],
            sort="recording_order",
            offset=1,
        )["items"][0]["id"]
        == a["id"]
    )
    assert {x["id"] for x in run(s, "list", kind="release", recording_id=a["id"])["items"]} == {
        edition["id"],
        hits["id"],
    }
    detail = run(s, "detail", id=album["id"])["item"]
    assert (
        detail["rating"] is None
        and detail["reviews"] == []
        and detail["child_recording_count"] == 2
    )
    assert not any(row["role"] == "parent_recording" for row in detail["related"])
    assert "child_recordings" not in detail  # children are a separate paged read
    assert run(s, "detail", id=complete["id"])["item"]["child_recording_count"] == 0
    assert run(s, "list", kind="recording", work_id=first["id"])["items"][0]["id"] == a["id"]
    assert run(s, "relation-options", kinds=["recording"], query="Azure")["items"][0]["id"] == str(
        a["id"]
    )
    restored = run(
        s,
        "save",
        id=a["id"],
        revision=a["revision"],
        object=dict(
            kind="recording",
            title=a["title"],
            relations=[credit(first, "work"), credit(author, "performer")],
        ),
    )["item"]
    assert restored["rating"] == 7 and restored["reviews"] == rated["reviews"]


@pytest.mark.integration
def test_one_level_validation_and_scope_do_not_leave_partial_records(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    root = save(s, "recording", "Root")
    child = save(s, "recording", "Child", [credit(root, "parent_recording")])
    for parent in (child,):
        with pytest.raises(ValueError, match="music.recording_nesting"):
            save(s, "recording", "Invalid nested leaf", [credit(parent, "parent_recording")])
    with pytest.raises(ValueError, match="music.recording_nesting"):
        run(
            s,
            "save",
            id=root["id"],
            revision=root["revision"],
            object=dict(
                kind="recording", title="Root", relations=[credit(child, "parent_recording")]
            ),
        )
    with pytest.raises(ValueError, match="music.invalid_relation"):
        save(s, "work", "Not audio", [credit(root, "parent_recording")])
    other = service(db_engine, tmp_path / "other")
    with pytest.raises(ValueError, match="music.not_found"):
        save(other, "recording", "Other scope", [credit(root, "parent_recording")])
    assert run(s, "list", kind="recording")["total"] == 2
    assert run(s, "list", kind="recording", root_recordings=True)["total"] == 1
    options = run(s, "relation-options", kinds=["recording"], relation_role="parent_recording")
    assert {str(x["id"]) for x in options["items"]} == {str(root["id"])}
    with pytest.raises(ValueError, match="music.invalid_filter"):
        run(s, "list", kind="recording", root_recordings=True, parent_recording_id=root["id"])
    with pytest.raises(ValueError, match="music.invalid_filter"):
        run(s, "list", kind="recording", sort="recording_order")


@pytest.mark.integration
def test_promote_one_track_keeps_ids_history_scope_and_no_mass_split(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    work = save(s, "work", "One Song", music_classes=["rock"])
    parent = save(
        s, "recording", "Whole Album", [credit(work, "work")], personal_notes="Whole notes"
    )
    parent = run(
        s,
        "review",
        id=parent["id"],
        revision=parent["revision"],
        rating=6,
        rating_scale=7,
        comment="Whole review",
    )["item"]
    release = save(
        s,
        "release",
        "Edition",
        [credit(parent, "recording")],
        tracks=[
            dict(
                title="Selected single",
                position=1,
                duration_seconds=123,
                relations=[credit(work, "work"), credit(parent, "recording")],
            ),
            dict(title="Unsplit track", position=2, relations=[credit(parent, "recording")]),
        ],
    )
    release = run(
        s,
        "image-upload",
        id=release["id"],
        revision=release["revision"],
        filename="synthetic.png",
        image_data=image_data("blue"),
        image_source_url="https://example.test/album",
        image_credit="Synthetic album",
    )["item"]
    before = run(s, "detail", id=release["tracks"][0]["id"])["item"]
    after = run(
        s, "track-promote", id=before["id"], revision=before["revision"], recording_id=parent["id"]
    )
    child = after["item"]
    assert not after["duplicate"] and child["kind"] == "recording"
    assert child["image_id"] == release["image_id"] and child["image_info"] == release["image_info"]
    assert child["recorded_on"] is None
    assert child["rating"] is None and child["reviews"] == [] and not child["personal_notes"]
    assert {r["object_id"] for r in child["relations"] if r["role"] == "work"} == {work["id"]}
    assert run(s, "list", kind="recording")["total"] == 2
    assert run(s, "list", kind="recording", root_recordings=True)["total"] == 1
    current = run(s, "detail", id=before["id"])["item"]
    for key in (
        "id",
        "title",
        "release_id",
        "disc",
        "position",
        "duration_seconds",
        "sources",
        "reviews",
    ):
        assert current[key] == before[key]
    assert {r["object_id"] for r in current["relations"] if r["role"] == "recording"} == {
        child["id"],
        parent["id"],
    }
    assert run(s, "detail", id=release["id"])["item"]["tracks"] == release["tracks"]
    assert run(s, "detail", id=parent["id"])["item"]["reviews"] == parent["reviews"]
    assert (
        run(s, "detail", id=release["tracks"][1]["id"])["item"]["relations"][0]["object_id"]
        == parent["id"]
    )
    assert run(
        s,
        "track-promote",
        id=current["id"],
        revision=current["revision"],
        recording_id=parent["id"],
    )["duplicate"]
    anthology = save(s, "recording", "Reusable compilation")
    reused = run(
        s,
        "track-promote",
        id=current["id"],
        revision=current["revision"],
        recording_id=anthology["id"],
    )
    assert reused["duplicate"] and reused["item"]["id"] == child["id"]
    assert run(s, "list", kind="recording")["total"] == 3
    with pytest.raises(ValueError, match="music.revision_conflict"):
        run(
            s,
            "track-promote",
            id=before["id"],
            revision=before["revision"],
            recording_id=parent["id"],
        )
    reader = service(db_engine, tmp_path / "reader", "reader")
    with pytest.raises(PermissionError):
        run(
            reader,
            "track-promote",
            id=before["id"],
            revision=before["revision"],
            recording_id=parent["id"],
        )
    # A group must never be silently flattened by the legacy correction action.
    with pytest.raises(ValueError, match="music.recording_group_in_use"):
        run(
            s,
            "recording-consolidate",
            id=parent["id"],
            revision=parent["revision"],
            object_ids=[child["id"]],
            source_revisions={child["id"]: child["revision"]},
            reason="Not an artificial split",
        )
    assert UUID(str(child["id"]))
