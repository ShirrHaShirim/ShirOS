"""Synthetic end-to-end retrieval/context, concurrency and rollback regression tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from shiros.adapters.providers import Embedding, SummaryOutput
from shiros.adapters.providers.memory_mock import MockSummaryProvider
from shiros.core.context import ContextRequest
from shiros.core.ingestion import MemoryWrite
from shiros.core.permissions import Action, ExplicitGrantPermissions
from shiros.core.retrieval import SearchRequest
from shiros.core.schemas import Provenance
from shiros.layers import MemoryLayerProcessor
from shiros.shared_memory import SharedMemoryServices, build_shared_memory

pytestmark = pytest.mark.integration


def setup(engine: Engine) -> tuple[SharedMemoryServices, UUID, UUID]:
    actor, scope = uuid4(), uuid4()
    actions: tuple[Action, ...] = ("read", "persist", "execute", "review")
    permissions = ExplicitGrantPermissions(frozenset((actor, action, scope) for action in actions))
    return build_shared_memory(engine, permissions), actor, scope


def request(scope: UUID, content: str, **updates: object) -> MemoryWrite:
    return MemoryWrite.model_validate(
        {
            "scope_id": scope,
            "idempotency_key": uuid4(),
            "source_kind": "synthetic",
            "source_title": "Synthetic source",
            "source_text": content,
            "text": content,
            "entity_title": "Synthetic entity",
            "observed_at": datetime(2026, 10, 6, tzinfo=UTC),
            **updates,
        }
    )


async def write(services: SharedMemoryServices, actor: UUID, value: MemoryWrite) -> UUID:
    approval = services.reviews.approve(actor, actor, value)
    return (await services.memory.ingest(actor, value, approval)).memory_id


async def test_golden_path_retrieval_and_context(db_engine: Engine) -> None:
    services, actor, scope = setup(db_engine)
    first_id = await write(
        services, actor, request(scope, "Algebra studies mathematical structures")
    )
    first = await services.retrieval.exact(actor, scope, first_id)
    assert first is not None
    second_id = await write(
        services,
        actor,
        request(
            scope,
            "拓扑研究空间与连续映射",
            related_entity_ids=(first.entity_id,),
        ),
    )
    assert await services.layers.process(actor, scope) == 2
    source = await services.retrieval.source(actor, scope, first.provenance.source_id)
    assert source is not None and source.text == first.text
    for mode in ("keyword", "semantic", "hybrid", "structured"):
        hits = await services.retrieval.search(
            actor,
            SearchRequest(
                scope_ids=(scope,),
                query=first.text,
                mode=mode,
            ),
        )
        assert any(hit.memory.memory_id == first_id for hit in hits)
        assert all(hit.memory.provenance.source_id for hit in hits)
        assert all(hit.rank > 0 for hit in hits)
        if mode == "semantic":
            assert hits[0].memory.memory_id == first_id and hits[0].semantic_score > 0.999
    chinese = await services.retrieval.search(
        actor,
        SearchRequest(
            scope_ids=(scope,),
            query="连续映射",
            mode="keyword",
        ),
    )
    assert [hit.memory.memory_id for hit in chinese] == [second_id]
    partial = await services.retrieval.search(
        actor,
        SearchRequest(
            scope_ids=(scope,),
            query="连续映射 不存在的合成词",
            mode="keyword",
        ),
    )
    assert [hit.memory.memory_id for hit in partial] == [second_id]
    fulltext = await services.retrieval.search(
        actor,
        SearchRequest(
            scope_ids=(scope,),
            query="Algebra structures",
            mode="keyword",
        ),
    )
    assert fulltext[0].memory.memory_id == first_id
    bundle = await services.context.compile(
        ContextRequest(
            actor_id=actor,
            project_id=scope,
            task="Algebra",
            model="mock",
            token_budget=2000,
        )
    )
    assert bundle.items and bundle.items[0].layer == "snapshot"
    assert bundle.token_count <= 2000 and bundle.deduplicated >= 2
    assert bundle.stages == ("snapshot", "summary", "search", "exact")
    assert len({item.memory_id for item in bundle.items}) == len(bundle.items)
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM relations WHERE scope_id=:s"), {"s": scope}
            )
            == 1
        )


async def test_structured_filters_history_and_scope_isolation(db_engine: Engine) -> None:
    services, actor, scope = setup(db_engine)
    memory_id = await write(
        services, actor, request(scope, "Original synthetic observation", domain="knowledge")
    )
    first = await services.retrieval.exact(actor, scope, memory_id)
    assert first is not None
    later = first.provenance.observed_at + timedelta(days=1)
    update = request(
        scope,
        "Updated synthetic observation",
        memory_id=memory_id,
        expected_revision=1,
        domain="knowledge",
        observed_at=later,
    )
    await write(services, actor, update)
    current = await services.retrieval.exact(actor, scope, memory_id)
    assert current is not None and current.revision == 2
    historical = await services.retrieval.exact(actor, scope, first.id, revision=True)
    assert historical == first
    search = SearchRequest(
        scope_ids=(scope,),
        mode="structured",
        entity_ids=(current.entity_id,),
        source_ids=(current.provenance.source_id,),
        domain="knowledge",
        since=later,
        until=later,
    )
    assert [hit.memory.id for hit in await services.retrieval.search(actor, search)] == [current.id]
    assert (
        await services.retrieval.search(
            actor, search.model_copy(update={"source_ids": (first.provenance.source_id,)})
        )
        == []
    )
    assert (
        await services.retrieval.search(
            actor, search.model_copy(update={"until": later - timedelta(seconds=1), "since": None})
        )
        == []
    )
    assert await services.retrieval.search(uuid4(), search) == []
    assert await services.retrieval.exact(uuid4(), scope, memory_id) is None
    assert await services.retrieval.source(uuid4(), scope, current.provenance.source_id) is None
    assert (
        await services.retrieval.search(actor, search.model_copy(update={"scope_ids": (uuid4(),)}))
        == []
    )
    denied = await services.context.compile(
        ContextRequest(
            actor_id=uuid4(),
            scope_ids=(scope,),
            task="observation",
            model="mock",
            token_budget=4000,
        )
    )
    assert denied.items == ()


async def test_context_fallback_exact_and_budget(db_engine: Engine) -> None:
    services, actor, scope = setup(db_engine)
    memory_id = await write(services, actor, request(scope, "synthetic space " * 300))
    memory = await services.retrieval.exact(actor, scope, memory_id)
    assert memory is not None
    base = ContextRequest(
        actor_id=actor, scope_ids=(scope,), task="space", model="mock", token_budget=900
    )
    before = await services.context.compile(base)
    assert before.items[0].layer == "memory" and before.trimmed
    assert before.token_count <= 900 and before.items[0].truncated
    assert before.items[0].provenance == memory.provenance
    tiny = await services.context.compile(base.model_copy(update={"token_budget": 1}))
    assert tiny.items == () and tiny.trimmed and tiny.token_count == 0
    await services.layers.process(actor, scope)
    after = await services.context.compile(base.model_copy(update={"token_budget": 1800}))
    assert after.items[0].layer == "snapshot"
    assert len(after.items) == 1
    exact = await services.context.compile(
        base.model_copy(
            update={
                "related_entities": (uuid4(),),
                "exact_source_ids": (memory.provenance.source_id,),
                "token_budget": 7000,
            }
        )
    )
    assert exact.items and exact.items[0].layer == "source"


async def test_concurrent_idempotency_and_event_order(db_engine: Engine) -> None:
    services, actor, scope = setup(db_engine)
    value = request(scope, "Concurrent synthetic write")
    approval = services.reviews.approve(actor, actor, value)
    results = await asyncio.gather(
        *(services.memory.ingest(actor, value, approval) for _ in range(4))
    )
    assert len({result.id for result in results}) == 1
    await asyncio.gather(
        write(services, actor, request(scope, "A distinct synthetic write")),
        write(services, actor, request(scope, "Another distinct synthetic write")),
    )
    assert await services.layers.process(actor, scope, limit=1) == 1
    assert await services.layers.process(actor, scope) == 2
    assert await services.layers.process(actor, scope) == 0
    with db_engine.connect() as connection:
        sequences = list(
            connection.scalars(
                text("SELECT sequence FROM events WHERE scope_id=:s ORDER BY sequence"),
                {"s": scope},
            )
        )
        assert len(sequences) == 9 and len(set(sequences)) == 9
        checkpoint = connection.scalar(
            text("SELECT max(through_sequence) FROM checkpoints WHERE scope_id=:s"), {"s": scope}
        )
        last_ingest = connection.scalar(
            text(
                "SELECT max(sequence) FROM events WHERE scope_id=:s AND event_type='memory.created'"
            ),
            {"s": scope},
        )
        assert checkpoint == last_ingest


class FailingEmbedding:
    async def embed(self, texts: list[str]) -> list[Embedding]:
        raise RuntimeError("synthetic.embedding_failure")


class EmptyEmbedding:
    async def embed(self, texts: list[str]) -> list[Embedding]:
        return []


async def test_embedding_failure_rolls_back_all_ingestion(db_engine: Engine) -> None:
    services, actor, scope = setup(db_engine)
    services.memory.embeddings = FailingEmbedding()
    value = request(scope, "Allowed synthetic data")
    approval = services.reviews.approve(actor, actor, value)
    with pytest.raises(RuntimeError, match="synthetic.embedding_failure"):
        await services.memory.ingest(actor, value, approval)
    with db_engine.connect() as connection:
        for table in ("sources", "artifacts", "entities", "memories", "events", "memory_receipts"):
            assert (
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"), {"s": scope}
                )
                == 0
            )


async def test_query_provider_failure_is_controlled(db_engine: Engine) -> None:
    services, actor, scope = setup(db_engine)
    services.memory.embeddings = EmptyEmbedding()
    assert (
        await services.retrieval.search(
            actor,
            SearchRequest(
                scope_ids=(scope,),
                query="test",
                mode="keyword",
            ),
        )
        == []
    )
    with pytest.raises(ValueError, match="embedding.invalid_count"):
        await services.retrieval.search(actor, SearchRequest(scope_ids=(scope,), query="test"))


class PoisonedSummary:
    async def summarize(self, content: str, sources: tuple[Provenance, ...]) -> SummaryOutput:
        return SummaryOutput(text="password=synthetic-forbidden-output", sources=sources)


class FailSecondSummary:
    def __init__(self) -> None:
        self.calls = 0

    async def summarize(self, content: str, sources: tuple[Provenance, ...]) -> SummaryOutput:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("synthetic.mid_batch_failure")
        return SummaryOutput(text=content, sources=sources)


async def test_derived_secret_never_reaches_persistent_outputs(
    db_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    services, actor, scope = setup(db_engine)
    await write(services, actor, request(scope, "Approved synthetic input"))
    processor = MemoryLayerProcessor(services.memory, PoisonedSummary())
    with pytest.raises(PermissionError, match="privacy.persistence_denied"):
        await processor.process(actor, scope)
    with db_engine.connect() as connection:
        for table in ("summaries", "snapshots", "checkpoints", "inferences"):
            assert (
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"), {"s": scope}
                )
                == 0
            )
        assert (
            connection.scalar(text("SELECT count(*) FROM events WHERE scope_id=:s"), {"s": scope})
            == 1
        )
    assert "synthetic-forbidden-output" not in caplog.text


async def test_mid_batch_failure_rolls_back_prior_outputs_and_checkpoint(db_engine: Engine) -> None:
    services, actor, scope = setup(db_engine)
    await write(services, actor, request(scope, "First item before failure"))
    await write(services, actor, request(scope, "Second item triggers failure"))
    processor = MemoryLayerProcessor(services.memory, FailSecondSummary())
    with pytest.raises(RuntimeError, match="synthetic.mid_batch_failure"):
        await processor.process(actor, scope)
    with db_engine.connect() as connection:
        for table in ("summaries", "snapshots", "checkpoints"):
            assert (
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"), {"s": scope}
                )
                == 0
            )
        assert (
            connection.scalar(text("SELECT count(*) FROM events WHERE scope_id=:s"), {"s": scope})
            == 2
        )
    retried = MemoryLayerProcessor(services.memory, MockSummaryProvider())
    assert await retried.process(actor, scope) == 2
    assert await retried.process(actor, scope) == 0
