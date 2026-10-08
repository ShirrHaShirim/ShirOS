"""Bounded, scope-explicit retrieval contracts with auditable rankings."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from shiros.core.memory import Memory
from shiros.core.schemas import Schema, UtcTimestamp


class SearchRequest(Schema):
    scope_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)
    query: str = Field(default="", max_length=4000)
    mode: Literal["structured", "keyword", "semantic", "hybrid"] = "hybrid"
    entity_ids: tuple[UUID, ...] = ()
    source_ids: tuple[UUID, ...] = ()
    domain: str | None = None
    since: UtcTimestamp | None = None
    until: UtcTimestamp | None = None
    limit: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.since and self.until and self.since > self.until:
            raise ValueError("search.invalid_time_range")
        return self


class SearchHit(Schema):
    memory: Memory
    rank: int = Field(ge=1)
    score: float
    keyword_score: float
    semantic_score: float
    method: str
