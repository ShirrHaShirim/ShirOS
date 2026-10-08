from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from shiros.adapters.providers.memory_mock import MockEmbeddingProvider, MockSummaryProvider
from shiros.core.ingestion import MemoryWrite
from shiros.core.memory import Memory
from shiros.core.permissions import Action, ExplicitGrantPermissions
from shiros.core.privacy import RuleBasedPrivacyPolicy
from shiros.core.retrieval import SearchRequest
from shiros.layers import MemoryLayerProcessor
from shiros.memory_service import MemoryService
from shiros.retrieval import RetrievalService
from shiros.review import ReviewAuthority

pytestmark = pytest.mark.integration


def harness(engine: Engine) -> tuple[UUID, UUID, UUID, MemoryService, ReviewAuthority]:
    scope, actor, reviewer = uuid4(), uuid4(), uuid4()
    actions: tuple[Action, ...] = ("read", "persist", "execute")
    grants: frozenset[tuple[UUID, Action, UUID]] = frozenset(
        [(actor, action, scope) for action in actions]
        + [(reviewer, "review", scope)]
    )
    policy = RuleBasedPrivacyPolicy()
    reviews = ReviewAuthority(ExplicitGrantPermissions(grants), policy)
    service = MemoryService(
        engine, ExplicitGrantPermissions(grants), policy, reviews, MockEmbeddingProvider()
    )
    return scope, actor, reviewer, service, reviews


def write_request(scope: UUID, **updates: object) -> MemoryWrite:
    data: dict[str, object] = {
        "scope_id": scope,
        "idempotency_key": uuid4(),
        "source_kind": "synthetic",
        "source_title": "Visibility source",
        "source_text": "Synthetic visibility test source.",
        "text": "Visibility target memory.",
        "entity_title": "Visibility target",
        "entity_kind": "concept",
        "domain": "knowledge",
        "observed_at": datetime(2026, 10, 6, tzinfo=UTC),
    }
    data.update(updates)
    return MemoryWrite.model_validate(data)


async def add_memory(
    service: MemoryService,
    reviews: ReviewAuthority,
    actor: UUID,
    reviewer: UUID,
    request: MemoryWrite,
) -> Memory:
    token = reviews.approve(reviewer, actor, request)
    return await service.ingest(actor, request, token)


def add_visibility_row(
    engine: Engine, scope: UUID, actor: UUID, memory_id: UUID, hidden: bool
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO local_identities(id,principal) VALUES (:id,:principal) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": actor, "principal": f"visibility-{actor}"},
        )
        connection.execute(
            text(
                "INSERT INTO memory_visibility(scope_id,memory_id,hidden,actor_id) "
                "VALUES (:scope,:memory,:hidden,:actor)"
            ),
            {"scope": scope, "memory": memory_id, "hidden": hidden, "actor": actor},
        )


def add_revocation(engine: Engine, scope: UUID, actor: UUID, target: UUID, kind: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO local_identities(id,principal) VALUES (:id,:principal) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": actor, "principal": f"visibility-{actor}"},
        )
        connection.execute(
            text(
                "INSERT INTO revocations(id,scope_id,target_id,kind,actor_id) "
                "VALUES (:id,:scope,:target,:kind,:actor)"
            ),
            {"id": uuid4(), "scope": scope, "target": target, "kind": kind, "actor": actor},
        )


def scoped_count(engine: Engine, table: str, scope: UUID) -> int:
    with engine.connect() as connection:
        return int(connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE scope_id=:scope"), {"scope": scope}
        ).scalar_one())


async def test_hidden_memory_is_absent_from_exact_search_layers_and_source(
    db_engine: Engine,
) -> None:
    scope, actor, reviewer, service, reviews = harness(db_engine)
    memory = await add_memory(service, reviews, actor, reviewer, write_request(scope))
    processor = MemoryLayerProcessor(service, MockSummaryProvider())
    assert await processor.process(actor, scope) == 1
    add_visibility_row(db_engine, scope, reviewer, memory.memory_id, True)

    retrieval = RetrievalService(service)
    assert await retrieval.exact(actor, scope, memory.memory_id) is None
    assert await retrieval.exact(actor, scope, memory.id, revision=True) is None
    request = SearchRequest(scope_ids=(scope,), query="Visibility target", mode="keyword")
    assert await retrieval.search(actor, request) == []
    assert await retrieval.layers(actor, request, "summary") == []
    assert await retrieval.layers(actor, request, "snapshot") == []
    assert await retrieval.source(actor, scope, memory.provenance.source_id) is None


async def test_source_revocation_hides_memory_and_source_backreference(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = harness(db_engine)
    memory = await add_memory(service, reviews, actor, reviewer, write_request(scope))
    add_revocation(db_engine, scope, reviewer, memory.provenance.source_id, "source")
    retrieval = RetrievalService(service)
    assert await retrieval.exact(actor, scope, memory.memory_id) is None
    assert await retrieval.source(actor, scope, memory.provenance.source_id) is None
    assert await retrieval.search(
        actor, SearchRequest(scope_ids=(scope,), query="Visibility target", mode="keyword")
    ) == []


async def test_show_appends_new_state_and_restores_retrieval(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = harness(db_engine)
    memory = await add_memory(service, reviews, actor, reviewer, write_request(scope))
    add_visibility_row(db_engine, scope, reviewer, memory.memory_id, True)
    assert await RetrievalService(service).exact(actor, scope, memory.memory_id) is None
    add_visibility_row(db_engine, scope, reviewer, memory.memory_id, False)
    restored = await RetrievalService(service).exact(actor, scope, memory.memory_id)
    assert restored is not None and restored.id == memory.id


async def test_ingest_receipt_duplicate_and_revision_refuse_hidden_memory(
    db_engine: Engine,
) -> None:
    scope, actor, reviewer, service, reviews = harness(db_engine)
    request = write_request(scope)
    memory = await add_memory(service, reviews, actor, reviewer, request)
    add_visibility_row(db_engine, scope, reviewer, memory.memory_id, True)

    receipt_token = reviews.approve(reviewer, actor, request)
    with pytest.raises(PermissionError, match="memory.not_visible"):
        await service.ingest(actor, request, receipt_token)

    duplicate = request.model_copy(update={"idempotency_key": uuid4()})
    duplicate_token = reviews.approve(reviewer, actor, duplicate)
    with pytest.raises(PermissionError, match="memory.not_visible"):
        await service.ingest(actor, duplicate, duplicate_token)

    revision = write_request(
        scope,
        idempotency_key=uuid4(),
        memory_id=memory.memory_id,
        expected_revision=1,
        text="An attempted revision of hidden content.",
    )
    revision_token = reviews.approve(reviewer, actor, revision)
    with pytest.raises(PermissionError, match="memory.not_visible"):
        await service.ingest(actor, revision, revision_token)
    assert scoped_count(db_engine, "memories", scope) == 1


async def test_inference_receipt_rechecks_visibility_of_all_evidence(db_engine: Engine) -> None:
    from shiros.core.ingestion import InferenceWrite

    scope, actor, reviewer, service, reviews = harness(db_engine)
    memory = await add_memory(service, reviews, actor, reviewer, write_request(scope))
    request = InferenceWrite(
        scope_id=scope,
        idempotency_key=uuid4(),
        text="A synthetic inference.",
        evidence_revision_ids=(memory.id,),
        confidence=0.5,
    )
    token = reviews.approve(reviewer, actor, request)
    inferred = await service.infer(actor, request, token)
    add_visibility_row(db_engine, scope, reviewer, memory.memory_id, True)
    with pytest.raises(PermissionError, match="inference.evidence_denied"):
        await service.infer(actor, request, token)
    assert scoped_count(db_engine, "inferences", scope) == 1
    assert inferred.scope_id == scope


async def test_layers_skip_hidden_memory_but_advance_checkpoint(db_engine: Engine) -> None:
    scope, actor, reviewer, service, reviews = harness(db_engine)
    memory = await add_memory(service, reviews, actor, reviewer, write_request(scope))
    with db_engine.connect() as connection:
        event_sequence: int = connection.execute(
            text("SELECT sequence FROM events WHERE record_id=:id"), {"id": memory.id}
        ).scalar_one()
    add_visibility_row(db_engine, scope, reviewer, memory.memory_id, True)
    processor = MemoryLayerProcessor(service, MockSummaryProvider())
    assert await processor.process(actor, scope) == 1
    assert scoped_count(db_engine, "summaries", scope) == 0
    assert scoped_count(db_engine, "snapshots", scope) == 0
    with db_engine.connect() as connection:
        checkpoint: int = connection.execute(
            text("SELECT through_sequence FROM checkpoints WHERE scope_id=:scope"),
            {"scope": scope},
        ).scalar_one()
    assert checkpoint == event_sequence
    assert await processor.process(actor, scope) == 0


async def test_ingest_uses_caller_transaction_when_connection_is_supplied(
    db_engine: Engine,
) -> None:
    scope, actor, reviewer, service, reviews = harness(db_engine)
    request = write_request(scope)
    token = reviews.approve(reviewer, actor, request)
    with pytest.raises(RuntimeError, match="rollback outer transaction"):
        with db_engine.begin() as connection:
            result = await service._ingest(actor, request, token, connection=connection)
            assert result.scope_id == scope
            raise RuntimeError("rollback outer transaction")
    assert scoped_count(db_engine, "sources", scope) == 0
    assert scoped_count(db_engine, "memories", scope) == 0
