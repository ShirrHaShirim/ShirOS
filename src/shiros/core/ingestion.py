"""Untrusted request schemas contain no review or persistence override flags."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from shiros.core.schemas import EvidenceLevel, Schema, UtcTimestamp


class MemoryWrite(Schema):
    scope_id: UUID
    idempotency_key: UUID
    source_kind: Literal["note", "synthetic"] = "note"
    source_title: str = Field(min_length=1, max_length=500)
    source_text: str = Field(min_length=1, max_length=100_000)
    text: str = Field(min_length=1, max_length=20_000)
    entity_title: str = Field(min_length=1, max_length=500)
    entity_kind: Literal["note", "concept", "person", "project"] = "note"
    domain: Literal["general", "knowledge", "projects", "people", "music", "papers"] = "general"
    entity_id: UUID | None = None
    related_entity_ids: tuple[UUID, ...] = Field(default=(), max_length=20)
    memory_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    observed_at: UtcTimestamp
    level: EvidenceLevel = EvidenceLevel.EXPLICIT_STATEMENT
    confidence: float = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def consistent_update(self) -> Self:
        if (self.memory_id is None) != (self.expected_revision is None):
            raise ValueError("memory.update_requires_revision")
        return self


class InferenceWrite(Schema):
    scope_id: UUID
    idempotency_key: UUID
    text: str = Field(min_length=1, max_length=20_000)
    evidence_revision_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)
    confidence: float = Field(ge=0, le=1)
