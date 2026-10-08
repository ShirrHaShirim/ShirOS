"""Append-only events; checkpoints belong to jobs, not human conversations."""

from typing import Protocol
from uuid import UUID

from pydantic import Field

from shiros.core.records import PersistentRecord


class Event(PersistentRecord):
    entity_id: UUID
    record_id: UUID
    event_type: str
    sequence: int = Field(ge=1)


class EventStore(Protocol):
    """Append persists the sequence allocated by the enclosing write transaction."""

    async def append(self, event: Event) -> None: ...

    async def read_after(self, sequence: int, limit: int = 100) -> list[Event]: ...
