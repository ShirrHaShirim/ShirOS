"""Stable IDs and explicit versioned records, not a universal JSON entity store."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(UTC)


UtcTimestamp = Annotated[datetime, AfterValidator(require_utc)]


class Schema(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    schema_version: Literal[1] = 1


class Record(Schema):
    id: UUID = Field(default_factory=uuid4)
    created_at: UtcTimestamp = Field(default_factory=utc_now)


class EvidenceLevel(StrEnum):
    FACT = "fact"
    EXPLICIT_STATEMENT = "explicit_statement"
    DERIVED_FACT = "derived_fact"
    PATTERN = "pattern"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"
    MODEL_GUESS = "model_guess"


class Provenance(Schema):
    source_id: UUID
    observed_at: UtcTimestamp
    created_by: str
    level: EvidenceLevel
    verified: bool = False
    confidence: float = Field(ge=0, le=1)


class Entity(Record):
    """Shared identity only; future domains own their specific records."""

    kind: str = Field(min_length=1)
    title: str = Field(min_length=1)
    provenance: Provenance
