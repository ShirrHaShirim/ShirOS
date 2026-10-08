"""End-to-end checks that explicit revocations hide approved review data."""

import asyncio
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from test_review_workflow import setup

from shiros.core.context import ContextRequest
from shiros.core.memory import Memory
from shiros.core.permissions import Action, ExplicitGrantPermissions
from shiros.core.retrieval import SearchRequest
from shiros.intake import MemoryCandidate
from shiros.review_workflow import ReviewWorkflow
from shiros.shared_memory import SharedMemoryServices, build_shared_memory

pytestmark = pytest.mark.integration


def _actor(workflow: ReviewWorkflow) -> UUID:
    with workflow.engine.connect() as connection:
        return workflow.identity.current(connection)


def _services(engine: Engine, actor: UUID, scope: UUID) -> SharedMemoryServices:
    actions: tuple[Action, ...] = ("read", "persist", "execute", "review")
    grants: frozenset[tuple[UUID, Action, UUID]] = frozenset(
        (actor, action, scope) for action in actions
    )
    permissions = ExplicitGrantPermissions(grants)
    return build_shared_memory(engine, permissions)


def _count(engine: Engine, table: str, scope: UUID) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(
                text(f"SELECT count(*) FROM {table} WHERE scope_id=:scope"),
                {"scope": scope},
            )
            or 0
        )


def _embedding_count(engine: Engine, scope: UUID) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(
                text(
                    "SELECT count(*) FROM memory_embeddings e "
                    "JOIN memories m ON m.id=e.memory_revision_id WHERE m.scope_id=:scope"
                ),
                {"scope": scope},
            )
            or 0
        )


def _approved(
    engine: Engine, workflow: ReviewWorkflow, scope: UUID, root: Path, name: str
) -> tuple[MemoryCandidate, Memory, SharedMemoryServices]:
    (root / name).write_text(f"Synthetic revocation record {name}", encoding="utf-8")
    candidate = workflow.stage(scope, workflow.preview(scope, name).id)
    memory = workflow.approve(scope, candidate.id)
    return candidate, memory, _services(engine, _actor(workflow), scope)


def test_intake_source_revoke_hides_every_read_path_but_keeps_history(
    db_engine: Engine, tmp_path: Path
) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    candidate, memory, services = _approved(
        db_engine, workflow, scope, tmp_path, "intake-revoke.md"
    )
    actor = _actor(workflow)
    assert asyncio.run(services.layers.process(actor, scope)) == 1
    assert _embedding_count(db_engine, scope) == 1
    assert _count(db_engine, "summaries", scope) == 1
    assert _count(db_engine, "snapshots", scope) == 1

    workflow.revoke(scope, candidate.source_id, kind="intake_source")
    assert workflow.queue(scope) == []

    async def assert_hidden() -> None:
        assert await services.retrieval.exact(actor, scope, memory.memory_id) is None
        assert await services.retrieval.exact(actor, scope, memory.id, revision=True) is None
        assert await services.retrieval.source(actor, scope, memory.provenance.source_id) is None
        for mode in ("structured", "keyword", "semantic", "hybrid"):
            assert (
                await services.retrieval.search(
                    actor,
                    SearchRequest(
                        scope_ids=(scope,), query="Synthetic revocation record", mode=mode
                    ),
                )
                == []
            )
        for layer in ("summary", "snapshot"):
            assert (
                await services.retrieval.layers(
                    actor,
                    SearchRequest(scope_ids=(scope,), query="Synthetic revocation record"),
                    layer,
                )
                == []
            )
        bundle = await services.context.compile(
            ContextRequest(
                actor_id=actor,
                project_id=scope,
                task="Synthetic revocation record",
                model="mock",
                token_budget=4000,
                exact_source_ids=(memory.provenance.source_id,),
            )
        )
        assert bundle.items == ()

    asyncio.run(assert_hidden())

    assert _embedding_count(db_engine, scope) == 1
    assert _count(db_engine, "summaries", scope) == 1
    assert _count(db_engine, "snapshots", scope) == 1
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM revocations WHERE scope_id=:scope AND target_id=:id"),
                {"scope": scope, "id": candidate.source_id},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM candidate_states WHERE candidate_id=:id"),
                {"id": candidate.id},
            )
            == 3
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM review_audit WHERE scope_id=:scope AND subject_id=:id"),
                {"scope": scope, "id": candidate.id},
            )
            == 2
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM review_audit "
                    "WHERE scope_id=:scope AND subject_id=:id AND action='revoke'"
                ),
                {"scope": scope, "id": candidate.source_id},
            )
            == 1
        )


def test_memory_revoke_hides_memory_and_its_source(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    candidate, memory, services = _approved(
        db_engine, workflow, scope, tmp_path, "memory-revoke.md"
    )
    actor = _actor(workflow)
    workflow.revoke(scope, memory.memory_id, kind="memory")

    async def assert_hidden() -> None:
        assert await services.retrieval.exact(actor, scope, memory.memory_id) is None
        assert await services.retrieval.exact(actor, scope, memory.id, revision=True) is None
        assert await services.retrieval.source(actor, scope, memory.provenance.source_id) is None
        assert (
            await services.retrieval.search(
                actor,
                SearchRequest(
                    scope_ids=(scope,), query="Synthetic revocation record", mode="keyword"
                ),
            )
            == []
        )

    asyncio.run(assert_hidden())
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM revocations "
                    "WHERE scope_id=:scope AND kind='memory' AND target_id=:id"
                ),
                {"scope": scope, "id": memory.memory_id},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM review_audit "
                    "WHERE scope_id=:scope AND action='revoke' AND subject_id=:id"
                ),
                {"scope": scope, "id": memory.memory_id},
            )
            == 1
        )


def test_pending_intake_revoke_invalidates_candidate_and_clears_queue(
    db_engine: Engine, tmp_path: Path
) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    (tmp_path / "pending-revoke.md").write_text("Synthetic health source", encoding="utf-8")
    candidate = workflow.stage(scope, workflow.preview(scope, "pending-revoke.md").id)
    assert [item.id for item in workflow.queue(scope)] == [candidate.id]

    workflow.revoke(scope, candidate.source_id, kind="intake_source")

    assert workflow.queue(scope) == []
    with db_engine.connect() as connection:
        state = connection.execute(
            text(
                "SELECT revision,status,reason_code FROM candidate_states "
                "WHERE candidate_id=:id ORDER BY revision DESC LIMIT 1"
            ),
            {"id": candidate.id},
        ).one()
        assert state == (2, "invalidated", "source_revoked")
        assert (
            connection.scalar(
                text("SELECT count(*) FROM revocations WHERE scope_id=:scope AND target_id=:id"),
                {"scope": scope, "id": candidate.source_id},
            )
            == 1
        )
