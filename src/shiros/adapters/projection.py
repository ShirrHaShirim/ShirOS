"""Open workspace projection port. The mock never touches the filesystem."""

from typing import Literal, Protocol
from uuid import UUID

from shiros.core.schemas import Entity, Schema


class ExternalChange(Schema):
    entity_id: UUID
    kind: Literal["modified", "deleted"]


class WorkspaceProjectionAdapter(Protocol):
    """Only approved, sanitized entities may reach this internal adapter.

    Reverse sync must validate allowed fields and conflicts in a future service.
    """

    async def project(self, entity: Entity) -> str: ...

    async def remove_projection(self, entity: Entity) -> None: ...

    async def detect_external_changes(self) -> list[ExternalChange]: ...


class MockWorkspaceProjection:
    """Deterministic UUID path and Markdown output, in memory only."""

    def __init__(self) -> None:
        self.documents: dict[UUID, str] = {}

    async def project(self, entity: Entity) -> str:
        self.documents[entity.id] = (
            f'---\nid: "{entity.id}"\nschema_version: {entity.schema_version}\n'
            f'created_at: "{entity.created_at.isoformat()}"\n'
            f'source_id: "{entity.provenance.source_id}"\n---\n\n# {entity.title}\n'
        )
        return f"{entity.id}.md"

    async def remove_projection(self, entity: Entity) -> None:
        self.documents.pop(entity.id, None)

    async def detect_external_changes(self) -> list[ExternalChange]:
        return []
