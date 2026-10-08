"""Task-local context contracts; no implementation loads the full history."""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field

from shiros.core.schemas import Provenance, Schema


class ContextRequest(Schema):
    task: str
    actor_id: UUID
    project_id: UUID | None = None
    model: str
    token_budget: int = Field(gt=0)
    related_entities: tuple[UUID, ...] = ()
    scope_ids: tuple[UUID, ...] = ()
    domain: str | None = None
    exact_source_ids: tuple[UUID, ...] = ()


class ContextItem(Schema):
    text: str
    provenance: Provenance
    record_id: UUID | None = None
    memory_id: UUID | None = None
    layer: Literal["snapshot", "summary", "memory", "source"] = "memory"
    rank: int = Field(default=1, ge=1)
    score: float = 0
    truncated: bool = False


class ContextBundle(Schema):
    items: tuple[ContextItem, ...] = ()
    token_count: int = Field(ge=0)
    token_budget: int = Field(default=0, ge=0)
    estimator: Literal["utf8-json-byte-upper-estimate"] = "utf8-json-byte-upper-estimate"
    trimmed: bool = False
    deduplicated: int = Field(default=0, ge=0)
    stages: tuple[str, ...] = ()


class ContextCompiler(Protocol):
    """Permission filter -> snapshot -> summary -> search -> exact retrieval.

    Step Two must rank, deduplicate, budget and preserve provenance.
    """

    async def compile(self, request: ContextRequest) -> ContextBundle: ...
