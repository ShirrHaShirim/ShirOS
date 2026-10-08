"""Provider ports. No real model traffic is sent in Step One."""

from typing import Protocol

from pydantic import Field

from shiros.core.schemas import Provenance, Schema


class Embedding(Schema):
    model: str
    dimensions: int = Field(gt=0)
    values: tuple[float, ...]


class SummaryOutput(Schema):
    text: str
    sources: tuple[Provenance, ...]


class EmbeddingProvider(Protocol):
    """Input must pass privacy and external-disclosure checks first."""

    async def embed(self, texts: list[str]) -> list[Embedding]: ...


class SummaryProvider(Protocol):
    """Derived outputs retain source restrictions and provenance."""

    async def summarize(self, text: str, sources: tuple[Provenance, ...]) -> SummaryOutput: ...
