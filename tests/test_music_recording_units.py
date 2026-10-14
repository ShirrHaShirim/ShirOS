"""Complete recording units survive explicit repairs; Work collections stay canonical."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text
from test_music import run, service

from shiros.domains.music import MusicRequest, ObjectInput


def credit(item, role, **details):
    return {"object_id": item["id"], "role": role, **details}


def save(s, kind, title, relations=(), **fields):
    return run(s, "save", object=dict(kind=kind, title=title, relations=list(relations), **fields))[
        "item"
    ]


@pytest.mark.integration
def test_explicit_consolidation_keeps_tracks_works_precise_credits_and_history(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    author = save(s, "person", "Author")
    soloist = save(s, "person", "Violinist")
    collection = save(s, "work", "Six Sonatas", work_form="collection", music_classes=["classical"])
    works = [
        save(
            s,
            "work",
            f"BWV {1001 + i}",
            [credit(collection, "parent_work"), credit(author, "composer")],
        )
        for i in range(6)
    ]
    complete = save(
        s,
        "recording",
        "Complete Session",
        [credit(collection, "work")],
        personal_notes="Keep my whole-recording note",
    )
    complete = run(
        s,
        "review",
        id=complete["id"],
        revision=complete["revision"],
        rating=6,
        rating_scale=7,
        comment="Whole performance",
    )["item"]
    parts = [
        save(
            s,
            "recording",
            f"Artificial Part {i}",
            [
                credit(work, "work"),
                credit(soloist, "performer", performance_role="小提琴", source_text=f"Track {i}"),
            ],
        )
        for i, work in enumerate(works)
    ]
    parts[0] = run(
        s,
        "source",
        id=parts[0]["id"],
        revision=parts[0]["revision"],
        url="https://example.org/performance",
        platform="official",
    )["item"]
    release = save(
        s,
        "release",
        "Edition",
        [credit(p, "recording") for p in parts],
        tracks=[
            dict(
                title=f"Movement {i}",
                disc=1,
                position=i + 1,
                duration_seconds=100 + i,
                relations=[credit(part, "recording"), credit(work, "work")],
            )
            for i, (part, work) in enumerate(zip(parts, works, strict=True))
        ],
    )
    tracks_before = release["tracks"]
    result = run(
        s,
        "recording-consolidate",
        id=complete["id"],
        revision=complete["revision"],
        object_ids=[p["id"] for p in parts],
        source_revisions={p["id"]: p["revision"] for p in parts},
        reason="Explicit correction: these six artificial parts are one recording",
    )
    assert result["archived"] == 6
    after = result["item"]
    assert after["reviews"] == complete["reviews"]
    assert after["personal_notes"] == complete["personal_notes"]
    assert after["sources"][0]["url"] == "https://example.org/performance"
    assert run(s, "list", kind="recording")["total"] == 1
    assert run(s, "list", kind="recording", query="BWV 1006")["total"] == 1
    assert run(s, "list", kind="recording")["items"][0]["child_recording_count"] == 0
    for work in works:
        recordings = run(s, "list", kind="recording", work_id=work["id"])["items"]
        assert [x["id"] for x in recordings] == [complete["id"]]
    release_after = run(s, "detail", id=release["id"])["item"]
    assert release_after["tracks"] == tracks_before
    assert release_after["recording_ids"] == [complete["id"]]
    for i, track in enumerate(tracks_before):
        info = run(s, "detail", id=track["id"])["item"]
        assert [source["url"] for source in info["sources"]] == (
            ["https://example.org/performance"] if i == 0 else []
        )
        assert {r["object_id"] for r in info["relations"] if r["role"] == "work"} == {
            works[i]["id"]
        }
        performer = next(r for r in info["relations"] if r["role"] == "performer")
        assert (
            performer["source_text"] == f"Track {i}" and performer["performance_role"] == "小提琴"
        )
        assert {r["object_id"] for r in info["relations"] if r["role"] == "recording"} == {
            complete["id"]
        }
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(*) FROM music_object_records WHERE archived AND scope_id=:s"),
                {"s": s.scope},
            )
            == 6
        )
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM music_history "
                    "WHERE action='music.archived_consolidated' AND scope_id=:s"
                ),
                {"s": s.scope},
            )
            == 6
        )
    for part in parts:
        with pytest.raises(ValueError, match="music.not_found"):
            run(s, "detail", id=part["id"])


@pytest.mark.integration
def test_consolidation_is_explicit_scoped_atomic_and_protects_personal_history(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    complete = save(s, "recording", "Whole")
    first = save(s, "recording", "Part")
    second = save(s, "recording", "Another Part")
    alternate = save(s, "recording", "Different Genuine Performance")
    second = run(
        s, "review", id=second["id"], revision=second["revision"], rating=7, rating_scale=7
    )["item"]
    payload = dict(
        id=complete["id"],
        revision=complete["revision"],
        object_ids=[first["id"], second["id"]],
        source_revisions={first["id"]: first["revision"], second["id"]: second["revision"]},
        reason="Repair",
    )
    with pytest.raises(ValueError, match="music.consolidation_personal_history"):
        run(s, "recording-consolidate", **payload)
    assert run(s, "list", kind="recording")["total"] == 4
    assert run(s, "detail", id=complete["id"])["item"] == complete
    with pytest.raises(ValueError, match="music.revision_conflict"):
        run(s, "recording-consolidate", **{**payload, "revision": complete["revision"] + 1})
    other = service(db_engine, tmp_path / "other")
    with pytest.raises(ValueError, match="music.not_found"):
        run(other, "recording-consolidate", **payload)
    # A later performance is not automatically merged by a Work or Release match.
    assert run(s, "detail", id=alternate["id"])["item"]["id"] == alternate["id"]
    with pytest.raises(ValueError, match="music.invalid_relation"):
        save(s, "recording", "Illegal Child Recording", [credit(complete, "recording")])


@pytest.mark.integration
def test_classical_collection_roots_search_and_child_navigation(db_engine: Engine, tmp_path: Path):
    s = service(db_engine, tmp_path)
    author = save(s, "person", "Bach")
    collection = save(
        s,
        "work",
        "Sonatas & Partitas BWV 1001–1006",
        [credit(author, "composer")],
        work_form="collection",
        music_classes=["classical"],
    )
    children = [
        save(
            s,
            "work",
            f"BWV {1001 + i}",
            [credit(collection, "parent_work"), credit(author, "composer")],
        )
        for i in range(6)
    ]
    independent = save(s, "work", "Independent Concerto", music_classes=["classical"])
    assert run(s, "list", kind="work")["total"] == 8
    assert len(children) == 6
    roots = run(s, "list", kind="work", root_works=True)
    assert {x["id"] for x in roots["items"]} == {collection["id"], independent["id"]}
    assert run(s, "detail", id=collection["id"])["item"]["work_form"] == "collection"
    assert (
        run(s, "list", kind="work", root_works=True, query="BWV 1006")["items"][0]["id"]
        == collection["id"]
    )
    assert run(s, "list", kind="work", parent_work_id=collection["id"])["total"] == 6
    assert run(s, "profile-sections", id=author["id"], root_works=True)["items"][0]["total"] == 1
    assert len(run(s, "detail", id=collection["id"])["item"]["child_works"]) == 6
    with pytest.raises(ValueError, match="music.work_fields"):
        save(s, "recording", "Invalid Collection", work_form="collection")


def test_retired_recording_hierarchy_options_are_rejected():
    for payload in (
        {"fold_album_recordings": True},
        {"album_recording_id": "00000000-0000-0000-0000-000000000001"},
    ):
        with pytest.raises(ValidationError):
            MusicRequest.model_validate(payload)
    with pytest.raises(ValidationError):
        ObjectInput.model_validate(
            {"kind": "recording", "title": "Old flag", "is_album_recording": True}
        )


@pytest.mark.integration
def test_album_order_uses_actual_tracks_then_numeric_catalogue(db_engine: Engine, tmp_path: Path):
    s = service(db_engine, tmp_path)
    collection = save(s, "work", "Programme", work_form="album")
    first = save(s, "work", "Z Sonata", [credit(collection, "parent_work")], catalogue="BWV 2")
    second = save(s, "work", "A Partita", [credit(collection, "parent_work")], catalogue="BWV 10")

    def ordered():
        rows = run(s, "list", kind="work", parent_work_id=collection["id"], sort="work_order")[
            "items"
        ]
        return [row["id"] for row in rows]

    assert ordered() == [first["id"], second["id"]]
    recording = save(s, "recording", "Complete Programme", [credit(collection, "work")])
    save(
        s,
        "release",
        "Edition",
        [credit(recording, "recording")],
        tracks=[
            dict(
                title="A Partita",
                disc=1,
                position=1,
                relations=[credit(second, "work"), credit(recording, "recording")],
            ),
            dict(
                title="Z Sonata",
                disc=2,
                position=1,
                relations=[credit(first, "work"), credit(recording, "recording")],
            ),
        ],
    )
    assert ordered() == [second["id"], first["id"]]
    with pytest.raises(ValueError, match="music.invalid_filter"):
        run(s, "list", kind="work", sort="work_order")


@pytest.mark.integration
def test_classical_collection_order_and_recordings_are_not_defined_by_partial_programmes(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    collection = save(s, "work", "Symphonies", work_form="collection", music_classes=["classical"])
    first = save(s, "work", "Symphony 2", [credit(collection, "parent_work")], catalogue="Op. 2")
    second = save(s, "work", "Symphony 10", [credit(collection, "parent_work")], catalogue="Op. 10")
    performances = [
        save(s, "recording", title, [credit(work, "work")])
        for title, work in [("Take A", first), ("Take B", first), ("Take C", second)]
    ]
    save(
        s,
        "release",
        "Reversed programme",
        [credit(p, "recording") for p in performances],
        tracks=[
            dict(
                title="Symphony 10",
                disc=1,
                position=1,
                relations=[credit(second, "work"), credit(performances[2], "recording")],
            ),
            dict(
                title="Symphony 2",
                disc=2,
                position=1,
                relations=[credit(first, "work"), credit(performances[0], "recording")],
            ),
        ],
    )
    rows = run(s, "list", kind="work", parent_work_id=collection["id"], sort="work_order")["items"]
    assert [row["id"] for row in rows] == [first["id"], second["id"]]
    recordings = run(s, "list", kind="recording", work_id=collection["id"])
    assert recordings["items"] == []
    assert recordings["total"] == 0
    related = run(s, "detail", id=collection["id"])["item"]["related"]
    assert {row["id"] for row in related} == {first["id"], second["id"]}
    explicit = run(s, "list", kind="recording", work_id=collection["id"], include_child_works=True)
    assert {row["id"] for row in explicit["items"]} == {p["id"] for p in performances}
    complete = save(
        s,
        "recording",
        "Complete cycle",
        [credit(collection, "work"), credit(first, "work"), credit(second, "work")],
    )
    recordings = run(s, "list", kind="recording", work_id=collection["id"])
    assert [row["id"] for row in recordings["items"]] == [complete["id"]]
    assert recordings["total"] == 1
