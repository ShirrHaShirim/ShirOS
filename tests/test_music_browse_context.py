"""Coverage is explicit; a transient UI range never truncates stored Releases."""

from pathlib import Path

import pytest
from sqlalchemy import Engine
from test_music import run, service
from test_music_recording_units import credit, save


@pytest.mark.integration
def test_complete_groups_from_one_recording_family_and_no_track_count_inference(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    folder = save(s, "work", "Early quartets", work_form="folder")
    empty = save(s, "work", "Empty group", work_form="folder")
    a = save(s, "work", "Quartet 1", [credit(folder, "parent_work")])
    b = save(s, "work", "Quartet 2", [credit(folder, "parent_work")])
    partial = save(s, "recording", "One quartet", [credit(a, "work")])
    group = save(s, "recording", "Both quartets")
    save(s, "recording", "Performance 1", [credit(a, "work"), credit(group, "parent_recording")])
    assert run(s, "list", kind="recording", work_id=folder["id"])["total"] == 0
    save(s, "recording", "Performance 2", [credit(b, "work"), credit(group, "parent_recording")])
    result = run(s, "list", kind="recording", work_id=folder["id"])
    assert [r["id"] for r in result["items"]] == [group["id"]]
    assert run(s, "list", kind="recording", work_id=empty["id"])["total"] == 0
    displayed = run(s, "list", kind="work", recording_works_id=group["id"])
    assert {w["id"] for w in displayed["items"]} == {folder["id"], a["id"], b["id"]}
    assert partial["id"] not in {r["id"] for r in result["items"]}
    # A track referencing a Work is a locator, never evidence of a whole performance.
    unproven = save(s, "recording", "Fragments")
    save(
        s,
        "release",
        "Fragments release",
        [credit(unproven, "recording")],
        tracks=[
            {
                "title": "Fragment",
                "disc": 1,
                "position": 1,
                "relations": [credit(unproven, "recording"), credit(a, "work")],
            }
        ],
    )
    assert unproven["id"] not in {
        r["id"] for r in run(s, "list", kind="recording", work_id=a["id"])["items"]
    }


@pytest.mark.integration
def test_partial_album_is_collapsed_in_recording_works_without_complete_association(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    album = save(s, "work", "Album", work_form="album")
    a = save(s, "work", "Song A", [credit(album, "parent_work")])
    b = save(s, "work", "Song B", [credit(album, "parent_work")])
    single = save(s, "recording", "One song", [credit(a, "work")])
    shown = run(s, "list", kind="work", recording_works_id=single["id"])
    assert [w["id"] for w in shown["items"]] == [album["id"]]
    assert run(s, "list", kind="recording", work_id=album["id"])["total"] == 0
    scoped = run(
        s, "list", kind="work", parent_work_id=album["id"], scope_recording_id=single["id"]
    )
    assert [w["id"] for w in scoped["items"]] == [a["id"]]
    assert run(s, "list", kind="work", parent_work_id=album["id"])["total"] == 2
    full = save(s, "recording", "Both songs", [credit(a, "work"), credit(b, "work")])
    assert [r["id"] for r in run(s, "list", kind="recording", work_id=album["id"])["items"]] == [
        full["id"]
    ]


@pytest.mark.integration
def test_release_range_order_unknown_tracks_and_unmodified_canonical_detail(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    a, b = save(s, "work", "Early work"), save(s, "work", "Late work")
    recording = save(s, "recording", "All works", [credit(a, "work"), credit(b, "work")])
    release = save(
        s,
        "release",
        "Complete release",
        [credit(recording, "recording")],
        tracks=[
            {
                "title": "Late",
                "disc": 1,
                "position": 1,
                "relations": [credit(b, "work"), credit(recording, "recording")],
            },
            {
                "title": "Early II",
                "disc": 2,
                "position": 2,
                "relations": [credit(a, "work"), credit(recording, "recording")],
            },
            {
                "title": "Early I",
                "disc": 2,
                "position": 1,
                "relations": [credit(a, "work"), credit(recording, "recording")],
            },
            {
                "title": "Unlocated",
                "disc": 3,
                "position": 1,
                "relations": [credit(recording, "recording")],
            },
        ],
    )
    original = run(s, "detail", id=release["id"])["item"]
    ranged = run(
        s, "browse-context", id=release["id"], work_id=a["id"], recording_id=recording["id"]
    )
    assert ranged["track_ids"] == [
        t["id"] for t in original["tracks"] if t["title"].startswith("Early")
    ]
    assert ranged["total_tracks"] == 4 and ranged["matched_tracks"] == 2
    assert ranged["unknown_tracks"] == 1
    assert original == run(s, "detail", id=release["id"])["item"]
    with pytest.raises(ValueError, match="music.invalid_relation"):
        run(s, "browse-context", id=release["id"], work_id=recording["id"])
    with pytest.raises(ValueError, match="music.invalid_filter"):
        run(s, "browse-context", id=release["id"])


@pytest.mark.integration
def test_track_in_whole_recording_can_locate_a_child_performance_but_not_siblings(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    a, b = save(s, "work", "Work A"), save(s, "work", "Work B")
    group = save(s, "recording", "Session")
    child = save(
        s, "recording", "A performance", [credit(a, "work"), credit(group, "parent_recording")]
    )
    sibling = save(
        s, "recording", "B performance", [credit(b, "work"), credit(group, "parent_recording")]
    )
    release = save(
        s,
        "release",
        "Session release",
        [credit(group, "recording")],
        tracks=[
            {
                "title": "A",
                "disc": 1,
                "position": 1,
                "relations": [credit(a, "work"), credit(group, "recording")],
            },
            {
                "title": "B",
                "disc": 1,
                "position": 2,
                "relations": [credit(b, "work"), credit(sibling, "recording")],
            },
        ],
    )
    result = run(s, "browse-context", id=release["id"], work_id=a["id"], recording_id=child["id"])
    assert result["matched_tracks"] == 1 and result["unknown_tracks"] == 0


@pytest.mark.integration
def test_multiple_performances_of_same_work_need_precise_track_recording_credit(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    work = save(s, "work", "Shared work")
    a = save(s, "recording", "First take", [credit(work, "work")])
    b = save(s, "recording", "Second take", [credit(work, "work")])
    release = save(
        s,
        "release",
        "Two takes",
        [credit(a, "recording"), credit(b, "recording")],
        tracks=[
            {
                "title": "First",
                "position": 1,
                "relations": [credit(a, "recording"), credit(work, "work")],
            },
            {
                "title": "Second",
                "position": 2,
                "relations": [credit(b, "recording"), credit(work, "work")],
            },
            {"title": "Unassigned", "position": 3, "relations": [credit(work, "work")]},
        ],
    )
    result = run(s, "browse-context", id=release["id"], work_id=work["id"], recording_id=a["id"])
    assert result["matched_tracks"] == 1 and result["unknown_tracks"] == 1
    assert result["total_tracks"] == 3
