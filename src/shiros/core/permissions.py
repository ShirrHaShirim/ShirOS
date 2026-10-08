"""Explicit grants only. Authentication and production authorization are deferred."""

from typing import Literal, Protocol
from uuid import UUID

from shiros.core.schemas import Schema

Action = Literal["read", "persist", "project", "execute", "review"]


class AccessRequest(Schema):
    actor_id: UUID
    action: Action
    resource_id: UUID


class PermissionService(Protocol):
    def allows(self, request: AccessRequest) -> bool: ...


class ExplicitGrantPermissions:
    """In-memory development policy with no implicit wildcard access."""

    def __init__(self, grants: frozenset[tuple[UUID, Action, UUID]] = frozenset()) -> None:
        self._grants = grants

    def allows(self, request: AccessRequest) -> bool:
        return (request.actor_id, request.action, request.resource_id) in self._grants
