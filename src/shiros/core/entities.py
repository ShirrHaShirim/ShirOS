"""Entity, Relation, Source and Artifact ownership starts in this module."""

from typing import Protocol
from uuid import UUID

from shiros.core.records import EntityRecord


class EntityRepository(Protocol):
    """Internal persistence port. Only guarded services may call save."""

    async def get(self, entity_id: UUID) -> EntityRecord | None: ...

    async def save(self, entity: EntityRecord) -> None: ...
