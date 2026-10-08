"""Delegated editor mutations: revisioned edits and revocation-based deletion."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from test_candidate_submission import actor, workflow

from shiros.candidate_submission import submit
from shiros.core.retrieval import SearchRequest
from shiros.identity import DatabasePermissions
from shiros.memory_mutations import delete_memory, edit_memory
from shiros.shared_memory import build_shared_memory


def approved_memory(
    engine: Engine, scope: UUID, review_root: Path, marker: str = "original"
) -> tuple[UUID, UUID]:
    proposer = actor(engine, scope, "proposer")
    review = workflow(engine, scope, review_root)
    result = submit(
        engine, proposer, scope, f"Synthetic source {marker}", f"Synthetic {marker} note", uuid4()
    )
    memory = review.approve(scope, UUID(result["candidate_id"]))
    return proposer, memory.memory_id


def keyword_hits(engine: Engine, actor_id: UUID, scope: UUID, query: str) -> list[str]:
    with engine.connect() as connection:
        services = build_shared_memory(engine, DatabasePermissions(connection))
        hits = asyncio.run(
            services.retrieval.search(
                actor_id, SearchRequest(scope_ids=(scope,), query=query, mode="keyword")
            )
        )
    return [str(hit.memory.memory_id) for hit in hits]


@pytest.mark.integration
def test_editor_edit_creates_revision_and_is_idempotent(
    db_engine: Engine, tmp_path: Path
) -> None:
    scope = uuid4()
    _, memory_id = approved_memory(db_engine, scope, tmp_path)
    editor, request = actor(db_engine, scope, "editor"), uuid4()
    edited = edit_memory(
        db_engine, editor, scope, memory_id, 1, "Synthetic revised note", request
    )
    assert edited["memory_id"] == str(memory_id)
    assert edited["revision"] == 2
    assert keyword_hits(db_engine, editor, scope, "revised") == [str(memory_id)]
    # Title remains "Synthetic source original" and is now searchable independently of body.
    assert keyword_hits(db_engine, editor, scope, "original") == [str(memory_id)]
    assert (
        edit_memory(db_engine, editor, scope, memory_id, 1, "Synthetic revised note", request)
        == edited
    )
    with pytest.raises(ValueError, match="idempotency_conflict"):
        edit_memory(db_engine, editor, scope, memory_id, 1, "Synthetic changed note", request)
    with pytest.raises(ValueError, match="revision_conflict"):
        edit_memory(db_engine, editor, scope, memory_id, 1, "Synthetic second note", uuid4())
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM memory_receipts WHERE actor_id=:a"), {"a": editor}
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM review_audit "
                    "WHERE actor_id=:a AND subject_id=:m AND action='edit'"
                ),
                {"a": editor, "m": memory_id},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM events WHERE record_id=:r"),
                {"r": UUID(edited["revision_id"])},
            )
            == 1
        )
    with db_engine.connect() as connection:
        services = build_shared_memory(db_engine, DatabasePermissions(connection))
        memory = asyncio.run(services.retrieval.exact(editor, scope, memory_id))
        assert memory is not None
        assert memory.revision == 2 and memory.supersedes_id is not None
        assert memory.provenance.level == "model_guess"
        assert memory.provenance.verified is False
        assert memory.text == "Synthetic revised note"


@pytest.mark.integration
def test_editor_edit_permission_privacy_and_visibility(
    db_engine: Engine, tmp_path: Path
) -> None:
    scope = uuid4()
    review = workflow(db_engine, scope, tmp_path)
    proposer = actor(db_engine, scope, "proposer")
    result = submit(
        db_engine, proposer, scope, "Synthetic source", "Synthetic guarded note", uuid4()
    )
    memory = review.approve(scope, UUID(result["candidate_id"]))
    editor = actor(db_engine, scope, "editor")
    reader = actor(db_engine, scope, "reader")
    with pytest.raises(PermissionError, match="permission.denied"):
        edit_memory(db_engine, reader, scope, memory.memory_id, 1, "Synthetic note", uuid4())
    with pytest.raises(PermissionError, match="permission.denied"):
        edit_memory(db_engine, editor, uuid4(), memory.memory_id, 1, "Synthetic", uuid4())
    with pytest.raises(PermissionError, match="privacy"):
        edit_memory(
            db_engine, editor, scope, memory.memory_id, 1, "password=synthetic", uuid4()
        )
    with pytest.raises(ValueError, match="too_large"):
        edit_memory(db_engine, editor, scope, memory.memory_id, 1, "x" * 20001, uuid4())
    review.hide(scope, memory.memory_id, True)
    with pytest.raises(PermissionError, match="not_visible"):
        edit_memory(db_engine, editor, scope, memory.memory_id, 1, "Synthetic note", uuid4())
    review.hide(scope, memory.memory_id, False)
    with pytest.raises(ValueError, match="not_found"):
        edit_memory(db_engine, editor, scope, uuid4(), 1, "Synthetic note", uuid4())


@pytest.mark.integration
def test_editor_delete_is_soft_idempotent_and_audited(
    db_engine: Engine, tmp_path: Path
) -> None:
    scope = uuid4()
    review = workflow(db_engine, scope, tmp_path)
    _, memory_id = approved_memory(db_engine, scope, tmp_path)
    editor = actor(db_engine, scope, "editor")
    deleted = delete_memory(db_engine, editor, scope, memory_id, 1)
    assert deleted["deleted"] is True and deleted["already_deleted"] is False
    assert keyword_hits(db_engine, editor, scope, "original") == []
    again = delete_memory(db_engine, editor, scope, memory_id, 1)
    assert again["already_deleted"] is True
    with pytest.raises(ValueError, match="revision_conflict"):
        delete_memory(db_engine, editor, scope, memory_id, 2)
    reader = actor(db_engine, scope, "reader")
    _, second_id = approved_memory(db_engine, scope, tmp_path, marker="second")
    with pytest.raises(PermissionError, match="permission.denied"):
        delete_memory(db_engine, reader, scope, second_id, 1)
    review.hide(scope, second_id, True)
    with pytest.raises(PermissionError, match="not_visible"):
        delete_memory(db_engine, editor, scope, second_id, 1)
    review.hide(scope, second_id, False)
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM review_audit "
                    "WHERE actor_id=:a AND subject_id=:m AND action='revoke'"
                ),
                {"a": editor, "m": memory_id},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM revocations "
                    "WHERE kind='memory' AND target_id=:m"
                ),
                {"m": memory_id},
            )
            == 1
        )
