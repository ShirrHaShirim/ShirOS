from datetime import UTC, datetime, timedelta
from math import isclose, sqrt
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shiros.adapters.providers.memory_mock import (
    DIMENSIONS,
    MODEL,
    MockEmbeddingProvider,
    MockSummaryProvider,
)
from shiros.core.ingestion import InferenceWrite, MemoryWrite
from shiros.core.retrieval import SearchRequest
from shiros.core.schemas import EvidenceLevel, Provenance


def memory_write_data() -> dict[str, object]:
    return {
        "scope_id": uuid4(),
        "idempotency_key": uuid4(),
        "source_title": "Source",
        "source_text": "Original source text",
        "text": "Extracted memory",
        "entity_title": "Topic",
        "observed_at": datetime(2026, 10, 6, tzinfo=UTC),
    }


def inference_write_data() -> dict[str, object]:
    return {
        "scope_id": uuid4(),
        "idempotency_key": uuid4(),
        "text": "Possible connection",
        "evidence_revision_ids": (uuid4(),),
        "confidence": 0.7,
    }


@pytest.mark.parametrize("untrusted_field", ["reviewed", "persistence_allowed", "verified"])
@pytest.mark.parametrize("schema_factory", [MemoryWrite, InferenceWrite])
def test_ingestion_rejects_trust_override_fields(
    schema_factory: type[MemoryWrite] | type[InferenceWrite], untrusted_field: str
) -> None:
    data = memory_write_data() if schema_factory is MemoryWrite else inference_write_data()
    data[untrusted_field] = True
    with pytest.raises(ValidationError):
        schema_factory.model_validate(data)


@pytest.mark.parametrize(
    ("memory_id", "expected_revision", "valid"),
    [
        (None, None, True),
        (uuid4(), 3, True),
        (uuid4(), None, False),
        (None, 3, False),
    ],
)
def test_memory_update_requires_id_and_revision_together(
    memory_id: object, expected_revision: int | None, valid: bool
) -> None:
    data = memory_write_data()
    if memory_id is not None:
        data["memory_id"] = memory_id
    if expected_revision is not None:
        data["expected_revision"] = expected_revision
    if valid:
        assert MemoryWrite.model_validate(data)
    else:
        with pytest.raises(ValidationError, match="memory.update_requires_revision"):
            MemoryWrite.model_validate(data)


def test_search_time_range_normalizes_offsets_and_allows_equal_bounds() -> None:
    scope = uuid4()
    request = SearchRequest(
        scope_ids=(scope,),
        since=datetime(2026, 10, 6, 8, tzinfo=UTC),
        until=datetime(2026, 10, 6, 16, tzinfo=UTC).astimezone(UTC) + timedelta(hours=1),
    )
    assert request.since is not None and request.since.utcoffset() == UTC.utcoffset(None)
    assert request.until is not None and request.until.utcoffset() == UTC.utcoffset(None)

    exact = datetime(2026, 10, 6, tzinfo=UTC)
    equal_bounds = SearchRequest(scope_ids=(scope,), since=exact, until=exact)
    assert equal_bounds.since == equal_bounds.until


def test_search_rejects_reversed_time_range() -> None:
    with pytest.raises(ValidationError, match="search.invalid_time_range"):
        SearchRequest(
            scope_ids=(uuid4(),),
            since=datetime(2026, 10, 7, tzinfo=UTC),
            until=datetime(2026, 10, 6, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_mock_embeddings_are_repeatable_dimensioned_and_normalized() -> None:
    provider = MockEmbeddingProvider()
    inputs = ["Shalom memory systems", "שלום מערכות זיכרון"]
    first = await provider.embed(inputs)
    again = await provider.embed(inputs)

    assert first == again
    assert len(first) == len(inputs)
    for embedding in first:
        assert embedding.model == MODEL
        assert embedding.dimensions == DIMENSIONS == len(embedding.values)
        assert isclose(sqrt(sum(value * value for value in embedding.values)), 1.0)
    assert first[0].values != first[1].values


@pytest.mark.asyncio
async def test_mock_summary_preserves_language_sources_and_caps_length() -> None:
    provider = MockSummaryProvider()
    sources = (
        Provenance(
            source_id=uuid4(),
            observed_at=datetime(2026, 10, 6, tzinfo=UTC),
            created_by="unit-test",
            level=EvidenceLevel.EXPLICIT_STATEMENT,
            confidence=1,
        ),
    )
    for text in ("The original English wording stays intact.", "中文原文保持不变。"):
        output = await provider.summarize(text, sources)
        assert output.text == text
        assert output.sources == sources

    long_text = "摘要" * 250
    output = await provider.summarize(long_text, sources)
    assert output.text == long_text[:400]
    assert len(output.text) == 400
    assert output.sources == sources
