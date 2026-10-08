from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from shiros.adapters.providers import SummaryOutput
from shiros.adapters.providers.memory_mock import MockEmbeddingProvider, MockSummaryProvider
from shiros.core.ingestion import InferenceWrite, MemoryWrite
from shiros.core.permissions import Action, ExplicitGrantPermissions
from shiros.core.privacy import RuleBasedPrivacyPolicy
from shiros.core.schemas import EvidenceLevel
from shiros.layers import MemoryLayerProcessor
from shiros.memory_service import MemoryService
from shiros.review import ReviewAuthority

pytestmark = pytest.mark.integration


def make_harness(engine: Engine, scope_id: UUID | None = None) -> tuple[
    UUID, UUID, UUID, MemoryService, ReviewAuthority
]:
    scope = scope_id or uuid4()
    actor, reviewer = uuid4(), uuid4()
    actions: tuple[Action, ...] = ("persist", "read", "execute")
    grants: frozenset[tuple[UUID, Action, UUID]] = frozenset(
        [(actor, action, scope) for action in actions]
        + [(reviewer, "review", scope)]
    )
    permissions = ExplicitGrantPermissions(grants)
    privacy = RuleBasedPrivacyPolicy()
    reviews = ReviewAuthority(permissions, privacy)
    service = MemoryService(engine, permissions, privacy, reviews, MockEmbeddingProvider())
    return scope, actor, reviewer, service, reviews


def make_memory_write(
    scope: UUID, *, key: UUID | None = None, source_text: str = "A synthetic source.",
    text_value: str = "A synthetic memory.", entity_title: str = "Synthetic topic",
    memory_id: UUID | None = None, expected_revision: int | None = None,
) -> MemoryWrite:
    return MemoryWrite(
        scope_id=scope,
        idempotency_key=key or uuid4(),
        source_kind="synthetic",
        source_title="Synthetic source",
        source_text=source_text,
        text=text_value,
        entity_title=entity_title,
        entity_kind="concept",
        domain="knowledge",
        memory_id=memory_id,
        expected_revision=expected_revision,
        observed_at=datetime(2026, 10, 6, 8, tzinfo=UTC),
    )


def approve(reviews: ReviewAuthority, reviewer: UUID, actor: UUID, request: Any) -> UUID:
    return reviews.approve(reviewer, actor, request)


def scope_count(engine: Engine, table: str, scope_id: UUID) -> int:
    with engine.connect() as connection:
        return int(connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE scope_id=:scope"), {"scope": scope_id}
        ).scalar_one())


def memory_embedding_count(engine: Engine, scope_id: UUID) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(
            "SELECT count(*) FROM memory_embeddings e JOIN memories m ON m.id=e.memory_revision_id "
            "WHERE m.scope_id=:scope"
        ), {"scope": scope_id}).scalar_one())


async def ingest_approved(
    service: MemoryService, reviews: ReviewAuthority, actor: UUID, reviewer: UUID,
    request: MemoryWrite,
) -> Any:
    token = approve(reviews, reviewer, actor, request)
    return await service.ingest(actor, request, token)


async def test_approved_ingest_persists_linked_records_and_embedding(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    request = make_memory_write(scope)
    memory = await ingest_approved(service, reviews, actor, reviewer, request)

    assert memory.revision == 1 and memory.scope_id == scope
    assert scope_count(db_engine, "sources", scope) == 1
    assert scope_count(db_engine, "artifacts", scope) == 1
    assert scope_count(db_engine, "entities", scope) == 1
    assert scope_count(db_engine, "memories", scope) == 1
    assert memory_embedding_count(db_engine, scope) == 1
    assert scope_count(db_engine, "events", scope) == 1
    with db_engine.connect() as connection:
        source: UUID = connection.execute(text(
            "SELECT id FROM sources WHERE scope_id=:scope"
        ), {"scope": scope}).scalar_one()
        artifact_source: UUID = connection.execute(text(
            "SELECT source_id FROM artifacts WHERE scope_id=:scope"
        ), {"scope": scope}).scalar_one()
        event = connection.execute(text(
            "SELECT record_id,event_type FROM events WHERE scope_id=:scope"
        ), {"scope": scope}).one()
        embedding = connection.execute(text(
            "SELECT model,dimensions FROM memory_embeddings e JOIN memories m "
            "ON m.id=e.memory_revision_id WHERE m.scope_id=:scope"
        ), {"scope": scope}).one()
    assert artifact_source == source == memory.provenance.source_id
    assert event.record_id == memory.id and event.event_type == "memory.created"
    assert (embedding.model, embedding.dimensions) == ("hash-bow-32-v1", 32)


async def test_contact_data_is_redacted_before_any_persistent_record(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    request = make_memory_write(
        scope,
        source_text="Contact synthetic@example.com or +1 415-555-0123.",
        text_value="Email synthetic@example.com, phone +1 415-555-0123.",
        entity_title="Person synthetic@example.com",
    )
    await ingest_approved(service, reviews, actor, reviewer, request)
    with db_engine.connect() as connection:
        rows: list[str] = list(connection.execute(text(
            "SELECT title AS value FROM sources WHERE scope_id=:scope UNION ALL "
            "SELECT text FROM sources WHERE scope_id=:scope UNION ALL "
            "SELECT text FROM artifacts WHERE scope_id=:scope UNION ALL "
            "SELECT title FROM entities WHERE scope_id=:scope UNION ALL "
            "SELECT text FROM memories WHERE scope_id=:scope"
        ), {"scope": scope}).scalars().all())
    joined = " ".join(rows)
    assert "synthetic@example.com" not in joined
    assert "415-555-0123" not in joined
    assert "[REDACTED_EMAIL]" in joined and "[REDACTED_PHONE]" in joined


async def test_secret_is_rejected_with_no_scope_rows_in_any_content_table(
    db_engine: Engine,
) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    request = make_memory_write(
        scope,
        source_text="Synthetic notes; api_key=do-not-store-this",
        text_value="A secret should never persist.",
    )
    with pytest.raises(PermissionError, match="privacy.persistence_denied"):
        token = approve(reviews, reviewer, actor, request)
        await service.ingest(actor, request, token)
    for table in (
        "sources", "artifacts", "entities", "relations", "memories", "events", "summaries",
        "snapshots", "inferences", "checkpoints", "memory_receipts",
    ):
        assert scope_count(db_engine, table, scope) == 0, table
    assert memory_embedding_count(db_engine, scope) == 0


@pytest.mark.parametrize("invalid_token", ["random", "changed", "unauthorized"])
async def test_review_token_and_permission_are_enforced(
    db_engine: Engine, invalid_token: str
) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    request = make_memory_write(scope)
    token = approve(reviews, reviewer, actor, request)
    with pytest.raises(PermissionError):
        if invalid_token == "random":
            await service.ingest(actor, request, uuid4())
        elif invalid_token == "changed":
            altered = request.model_copy(update={"text": "Altered after review"})
            await service.ingest(actor, altered, token)
        else:
            await service.ingest(uuid4(), request, token)
    assert scope_count(db_engine, "sources", scope) == 0


async def test_unreviewed_actor_and_reviewer_cannot_write_or_approve(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    request = make_memory_write(scope)
    with pytest.raises(PermissionError, match="permission.review_denied"):
        reviews.approve(uuid4(), actor, request)
    token = approve(reviews, reviewer, actor, request)
    with pytest.raises(PermissionError, match="permission.denied"):
        await service.ingest(uuid4(), request, token)
    assert scope_count(db_engine, "sources", scope) == 0


async def test_idempotency_replay_and_key_conflict(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    request = make_memory_write(scope)
    first = await ingest_approved(service, reviews, actor, reviewer, request)
    replay = await ingest_approved(service, reviews, actor, reviewer, request)
    assert replay.id == first.id
    assert scope_count(db_engine, "sources", scope) == 1
    assert scope_count(db_engine, "events", scope) == 1

    same_content_new_key = request.model_copy(update={"idempotency_key": uuid4()})
    duplicate = await ingest_approved(
        service, reviews, actor, reviewer, same_content_new_key
    )
    assert duplicate.id == first.id
    assert scope_count(db_engine, "sources", scope) == 1

    conflicting = request.model_copy(update={"text": "Different request with same key"})
    token = approve(reviews, reviewer, actor, conflicting)
    with pytest.raises(ValueError, match="memory.idempotency_conflict"):
        await service.ingest(actor, conflicting, token)
    assert scope_count(db_engine, "sources", scope) == 1


async def test_revisions_preserve_v1_and_link_v2_as_current(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    first = await ingest_approved(
        service, reviews, actor, reviewer, make_memory_write(scope, text_value="Revision one")
    )
    second_request = make_memory_write(
        scope, text_value="Revision two", memory_id=first.memory_id, expected_revision=1
    )
    second = await ingest_approved(service, reviews, actor, reviewer, second_request)
    assert (first.revision, second.revision) == (1, 2)
    assert second.supersedes_id == first.id and second.memory_id == first.memory_id
    with db_engine.connect() as connection:
        revisions = connection.execute(text(
            "SELECT id,revision,text FROM memories WHERE scope_id=:scope "
            "ORDER BY revision"
        ), {"scope": scope}).all()
    assert [(row.revision, row.text) for row in revisions] == [
        (1, "Revision one"), (2, "Revision two")
    ]
    assert revisions[0].id == first.id and revisions[1].id == second.id


async def test_memory_layer_processes_batches_checkpoint_and_noop(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    for content in ("First batch memory", "Second batch memory"):
        await ingest_approved(
            service, reviews, actor, reviewer, make_memory_write(scope, text_value=content)
        )
    processor = MemoryLayerProcessor(service, MockSummaryProvider())
    assert await processor.process(actor, scope, limit=1) == 1
    assert scope_count(db_engine, "summaries", scope) == 1
    assert scope_count(db_engine, "snapshots", scope) == 1
    assert scope_count(db_engine, "checkpoints", scope) == 1
    assert await processor.process(actor, scope, limit=1) == 1
    assert scope_count(db_engine, "summaries", scope) == 2
    assert scope_count(db_engine, "snapshots", scope) == 2
    assert scope_count(db_engine, "checkpoints", scope) == 2
    assert await processor.process(actor, scope) == 0
    assert scope_count(db_engine, "summaries", scope) == 2
    assert scope_count(db_engine, "snapshots", scope) == 2
    assert scope_count(db_engine, "checkpoints", scope) == 2


class FailOnceSummaryProvider:
    def __init__(self) -> None:
        self.fail = True

    async def summarize(self, content: str, sources: tuple[Any, ...]) -> SummaryOutput:
        if self.fail:
            self.fail = False
            raise RuntimeError("injected summary failure")
        return SummaryOutput(text=content[:400], sources=sources)


async def test_layer_failure_rolls_back_all_outputs_and_retry_succeeds(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    await ingest_approved(
        service, reviews, actor, reviewer, make_memory_write(scope, text_value="Retry this summary")
    )
    provider = FailOnceSummaryProvider()
    processor = MemoryLayerProcessor(service, provider)
    before_events = scope_count(db_engine, "events", scope)
    with pytest.raises(RuntimeError, match="injected summary failure"):
        await processor.process(actor, scope)
    assert scope_count(db_engine, "summaries", scope) == 0
    assert scope_count(db_engine, "snapshots", scope) == 0
    assert scope_count(db_engine, "checkpoints", scope) == 0
    assert scope_count(db_engine, "events", scope) == before_events
    assert await processor.process(actor, scope) == 1
    assert scope_count(db_engine, "summaries", scope) == 1
    assert scope_count(db_engine, "snapshots", scope) == 1
    assert scope_count(db_engine, "checkpoints", scope) == 1


async def test_database_trigger_rejects_history_update(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    memory = await ingest_approved(
        service, reviews, actor, reviewer, make_memory_write(scope)
    )
    with pytest.raises(IntegrityError, match="immutable_history"):
        with db_engine.begin() as connection:
            connection.execute(text(
                "UPDATE memories SET text='tampered' WHERE id=:id"
            ), {"id": memory.id})
    with db_engine.connect() as connection:
        stored: str = connection.execute(text(
            "SELECT text FROM memories WHERE id=:id"
        ), {"id": memory.id}).scalar_one()
    assert stored == "A synthetic memory."


async def test_inference_is_scope_bound_unverified_and_idempotent(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = make_harness(db_engine)
    other_scope, other_actor, other_reviewer, other_service, other_reviews = make_harness(db_engine)
    local_memory = await ingest_approved(
        service, reviews, actor, reviewer, make_memory_write(scope, text_value="Local evidence")
    )
    evidence = await ingest_approved(
        other_service, other_reviews, other_actor, other_reviewer,
        make_memory_write(other_scope, text_value="Foreign evidence"),
    )
    cross_scope = InferenceWrite(
        scope_id=scope,
        idempotency_key=uuid4(),
        text="A possible cross-scope link.",
        evidence_revision_ids=(evidence.id,),
        confidence=0.6,
    )
    cross_token = approve(reviews, reviewer, actor, cross_scope)
    with pytest.raises(PermissionError, match="inference.evidence_denied"):
        await service.infer(actor, cross_scope, cross_token)
    assert scope_count(db_engine, "inferences", scope) == 0

    request = InferenceWrite(
        scope_id=scope,
        idempotency_key=uuid4(),
        text="A supported possible link.",
        evidence_revision_ids=(local_memory.id,),
        confidence=0.6,
    )
    token = approve(reviews, reviewer, actor, request)
    inferred = await service.infer(actor, request, token)
    replay = await service.infer(actor, request, token)
    assert inferred.id == replay.id
    assert inferred.provenance.level == EvidenceLevel.INFERENCE
    assert not inferred.provenance.verified
    assert inferred.evidence_revision_ids == (local_memory.id,)
    assert scope_count(db_engine, "inferences", scope) == 1
    assert scope_count(db_engine, "events", scope) == 2
    with db_engine.connect() as connection:
        evidence_links: list[UUID] = list(connection.execute(text(
            "SELECT memory_revision_id FROM inference_evidence WHERE inference_id=:id"
        ), {"id": inferred.id}).scalars().all())
    assert evidence_links == [local_memory.id]
