"""Cross-client edits, immutable metadata, review and atomic source moves."""

import asyncio
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from test_candidate_submission import actor, workflow

from shiros.candidate_submission import proposal_status, submit
from shiros.memory_gateway import MemoryGateway
from shiros.memory_mutations import (
    edit_memory,
    migrate_memory_sources,
    rename_memory,
    set_memory_sources,
)


def fetch(engine: Engine, editor: UUID, scope: UUID, memory_id: UUID) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        asyncio.run(MemoryGateway(engine, editor, scope, "chatgpt").fetch(memory_id))["memory"],
    )


@pytest.mark.integration
@pytest.mark.parametrize("creator_role", ["proposer", "editor", "owner"])
def test_cross_client_approved_content_can_be_organized(
    db_engine: Engine,
    tmp_path: Path,
    creator_role: str,
) -> None:
    scope = uuid4()
    creator = actor(db_engine, scope, creator_role)
    editor = actor(db_engine, scope, "editor")
    review = workflow(db_engine, scope, tmp_path)
    body = "Synthetic medical history already reviewed.\nSources: synthetic document."
    if creator_role == "owner":
        # Owner imports are explicit statements, not model proposals.
        preview = review.preview_text(scope, "approved.txt", body)
        proposal = review.stage(scope, preview.id)
        memory = review.approve(scope, proposal.id)
    else:
        proposal_result = submit(db_engine, creator, scope, "Synthetic", body, uuid4())
        memory = review.approve(scope, UUID(proposal_result["candidate_id"]))
    renamed = rename_memory(
        db_engine, editor, scope, memory.memory_id, 1, "Knowledge | Synthetic topic | Note", uuid4()
    )
    assert renamed["status"] == "applied"
    rearranged = edit_memory(
        db_engine,
        editor,
        scope,
        memory.memory_id,
        2,
        "Sources: synthetic document.\n"
        "Synthetic medical history already reviewed.\nOrdinary annotation.",
        uuid4(),
    )
    assert rearranged["status"] == "applied"
    result = fetch(db_engine, editor, scope, memory.memory_id)
    assert result["title"] == "Knowledge｜Synthetic topic｜Note"
    assert result["provenance"]["source_id"] == str(memory.provenance.source_id)
    assert (
        result["provenance"]["observed_at"]
        == memory.provenance.model_dump(mode="json")["observed_at"]
    )
    assert result["provenance"]["level"] == memory.provenance.level.value
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM memories WHERE memory_id=:m"), {"m": memory.memory_id}
            )
            == 3
        )
        expected_title = "Synthetic" if creator_role != "owner" else "approved.txt"
        assert (
            connection.scalar(
                text("SELECT title FROM sources WHERE id=:s"), {"s": memory.provenance.source_id}
            )
            == expected_title
        )


@pytest.mark.integration
def test_source_records_and_migration_are_revisioned_atomic_and_retryable(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    scope = uuid4()
    creator, editor = actor(db_engine, scope, "proposer"), actor(db_engine, scope, "editor")
    review = workflow(db_engine, scope, tmp_path)
    body = "A synthetic reading note.\nSource: medical document already approved."
    proposal = submit(db_engine, creator, scope, "Synthetic", body, uuid4())
    memory = review.approve(scope, UUID(proposal["candidate_id"]))
    request = uuid4()
    moved = migrate_memory_sources(
        db_engine,
        editor,
        scope,
        memory.memory_id,
        1,
        "Source: medical document already approved.",
        "Original note",
        request,
    )
    assert moved["status"] == "applied" and moved["revision"] == 2
    assert (
        migrate_memory_sources(
            db_engine,
            editor,
            scope,
            memory.memory_id,
            1,
            "Source: medical document already approved.",
            "Original note",
            request,
        )
        == moved
    )
    current = fetch(db_engine, editor, scope, memory.memory_id)
    assert current["text"] == "A synthetic reading note."
    records = current["source_records"]
    assert len(records) == 1 and records[0]["id"]
    assert records[0]["text"] == "Source: medical document already approved."
    record_id = records[0]["id"]
    records[0]["title"] = "Retitled source"
    edited = set_memory_sources(db_engine, editor, scope, memory.memory_id, 2, records, uuid4())
    assert edited["revision"] == 3
    assert fetch(db_engine, editor, scope, memory.memory_id)["source_records"][0]["id"] == record_id
    with db_engine.connect() as connection:
        prior = connection.scalar(
            text("SELECT source_records FROM memory_revision_metadata WHERE revision_id=:id"),
            {"id": UUID(moved["revision_id"])},
        )
        assert prior[0]["title"] == "Original note"
    with pytest.raises(ValueError, match="source_span_not_unique"):
        migrate_memory_sources(
            db_engine, editor, scope, memory.memory_id, 3, "Not in the body", "Source", uuid4()
        )
    assert fetch(db_engine, editor, scope, memory.memory_id)["revision"] == 3


@pytest.mark.integration
def test_new_sensitive_edits_review_existing_uuid_and_conflicts(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    scope = uuid4()
    creator, editor = actor(db_engine, scope, "proposer"), actor(db_engine, scope, "editor")
    review = workflow(db_engine, scope, tmp_path)
    proposal = submit(db_engine, creator, scope, "Synthetic", "Ordinary original note", uuid4())
    memory = review.approve(scope, UUID(proposal["candidate_id"]))
    request = uuid4()
    proposed = edit_memory(
        db_engine, editor, scope, memory.memory_id, 1, "New synthetic medical diagnosis", request
    )
    assert proposed["status"] == "pending" and not proposed["edited"]
    assert (
        edit_memory(
            db_engine,
            editor,
            scope,
            memory.memory_id,
            1,
            "New synthetic medical diagnosis",
            request,
        )["candidate_id"]
        == proposed["candidate_id"]
    )
    candidate = UUID(proposed["candidate_id"])
    assert review.inspect(scope, candidate)["candidate"]["target_memory_id"] == str(
        memory.memory_id
    )
    assert fetch(db_engine, editor, scope, memory.memory_id)["revision"] == 1
    review.edit(
        scope,
        candidate,
        "Human reviewed synthetic medical diagnosis",
        "Knowledge｜Synthetic case｜Note",
        [{"title": "Reviewed source", "text": "Synthetic review record"}],
    )
    approved = review.approve(scope, candidate)
    assert approved.memory_id == memory.memory_id and approved.revision == 2
    assert approved.provenance.created_by == str(editor)
    assert approved.privacy.reviewed_by != editor
    assert fetch(db_engine, editor, scope, memory.memory_id)["source_records"][0]["id"]
    stale = edit_memory(
        db_engine,
        editor,
        scope,
        memory.memory_id,
        2,
        "Another synthetic medical diagnosis",
        uuid4(),
    )
    rename_memory(
        db_engine, editor, scope, memory.memory_id, 2, "Knowledge｜Case renamed｜Note", uuid4()
    )
    with pytest.raises(ValueError, match="revision_conflict"):
        review.approve(scope, UUID(stale["candidate_id"]))


@pytest.mark.integration
def test_metadata_scope_visibility_privacy_and_pending_rejection(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    scope = uuid4()
    creator, editor = actor(db_engine, scope, "proposer"), actor(db_engine, scope, "editor")
    reader = actor(db_engine, scope, "reader")
    review = workflow(db_engine, scope, tmp_path)
    proposal = submit(db_engine, creator, scope, "Synthetic", "Ordinary synthetic note", uuid4())
    memory = review.approve(scope, UUID(proposal["candidate_id"]))
    for who, target_scope in [(reader, scope), (editor, uuid4())]:
        with pytest.raises(PermissionError, match="permission.denied"):
            rename_memory(db_engine, who, target_scope, memory.memory_id, 1, "Renamed", uuid4())
    with pytest.raises(PermissionError, match="privacy"):
        set_memory_sources(
            db_engine,
            editor,
            scope,
            memory.memory_id,
            1,
            [{"title": "Key", "text": "password=synthetic-secret"}],
            uuid4(),
        )
    pending = set_memory_sources(
        db_engine,
        editor,
        scope,
        memory.memory_id,
        1,
        [{"title": "Case", "text": "New synthetic health note"}],
        uuid4(),
    )
    assert pending["status"] == "pending"
    review.reject(scope, UUID(pending["candidate_id"]))
    assert fetch(db_engine, editor, scope, memory.memory_id)["source_records"] == []
    pending_request = uuid4()
    hidden_edit = edit_memory(
        db_engine,
        editor,
        scope,
        memory.memory_id,
        1,
        "New synthetic medical detail",
        pending_request,
    )
    review.hide(scope, memory.memory_id, True)
    assert review.queue(scope) == []
    assert proposal_status(db_engine, editor, scope, pending_request)["status"] == "invalidated"
    with pytest.raises(PermissionError, match="not_visible"):
        review.inspect(scope, UUID(hidden_edit["candidate_id"]))
    with pytest.raises(PermissionError, match="not_visible"):
        rename_memory(db_engine, editor, scope, memory.memory_id, 1, "Renamed", uuid4())


@pytest.mark.integration
def test_source_move_rollback_preserves_body_and_lineage(
    db_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = uuid4()
    creator, editor = actor(db_engine, scope, "proposer"), actor(db_engine, scope, "editor")
    review = workflow(db_engine, scope, tmp_path)
    proposal = submit(
        db_engine,
        creator,
        scope,
        "Synthetic",
        "Ordinary note.\nSource: original document.",
        uuid4(),
    )
    memory = review.approve(scope, UUID(proposal["candidate_id"]))

    def fail(*args: object) -> None:
        raise RuntimeError("synthetic.audit_failure")

    with monkeypatch.context() as patch:
        patch.setattr("shiros.memory_mutations.audit", fail)
        with pytest.raises(RuntimeError, match="audit_failure"):
            migrate_memory_sources(
                db_engine,
                editor,
                scope,
                memory.memory_id,
                1,
                "Source: original document.",
                "Original source",
                uuid4(),
            )
    result = fetch(db_engine, editor, scope, memory.memory_id)
    assert result["revision"] == 1 and result["text"] == memory.text
    assert result["source_records"] == []
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM memory_revision_metadata WHERE scope_id=:s"),
                {"s": scope},
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM memories WHERE memory_id=:m"), {"m": memory.memory_id}
            )
            == 1
        )
