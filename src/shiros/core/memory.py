"""L0 Snapshot, L1 Summary and L2 permitted sources remain distinct."""

from typing import Protocol
from uuid import UUID

from pydantic import Field

from shiros.core.records import PersistentRecord


class Memory(PersistentRecord):
    memory_id: UUID
    entity_id: UUID
    revision: int = Field(ge=1)
    supersedes_id: UUID | None = None
    domain: str
    text: str


class MemoryRepository(Protocol):
    """Transaction-bound internal port, never exposed directly to clients."""

    async def get(self, memory_id: UUID) -> Memory | None: ...

    async def save(self, memory: Memory) -> None: ...

    async def search(self, query: str, limit: int = 10) -> list[Memory]: ...
