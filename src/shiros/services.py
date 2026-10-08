"""Composition and one guarded projection service; memory persistence is deferred."""

from dataclasses import dataclass
from uuid import UUID

from shiros.adapters.projection import MockWorkspaceProjection, WorkspaceProjectionAdapter
from shiros.adapters.providers.mock import MockConversationProvider, MockWorker
from shiros.core.conversations import ConversationProvider
from shiros.core.jobs import AgentWorker
from shiros.core.permissions import AccessRequest, PermissionService
from shiros.core.privacy import PrivacyInput, PrivacyPolicy, RuleBasedPrivacyPolicy
from shiros.core.schemas import Entity


@dataclass(frozen=True)
class Services:
    conversations: ConversationProvider
    worker: AgentWorker
    privacy: PrivacyPolicy
    projection: WorkspaceProjectionAdapter


def build_services() -> Services:
    return Services(
        conversations=MockConversationProvider(),
        worker=MockWorker(),
        privacy=RuleBasedPrivacyPolicy(),
        projection=MockWorkspaceProjection(),
    )


class ProjectionService:
    """Permission and privacy gates precede the development projection adapter.

    Review flags are trusted internal policy decisions, not user HTTP input.
    Only title is allowed as free text; other metadata is not projected.
    """

    def __init__(
        self,
        permissions: PermissionService,
        privacy: PrivacyPolicy,
        adapter: WorkspaceProjectionAdapter,
    ) -> None:
        self.permissions = permissions
        self.privacy = privacy
        self.adapter = adapter

    async def project(
        self,
        actor_id: UUID,
        entity: Entity,
        *,
        reviewed: bool = False,
        source_persistence_allowed: bool = True,
    ) -> str:
        access = AccessRequest(actor_id=actor_id, action="project", resource_id=entity.id)
        if not self.permissions.allows(access):
            raise PermissionError("Projection not permitted")
        decision = self.privacy.evaluate(
            PrivacyInput(
                text=entity.title,
                reviewed=reviewed,
                source_persistence_allowed=source_persistence_allowed,
            )
        )
        if not decision.persistence_allowed or decision.safe_text is None:
            raise PermissionError("Persistence not permitted")
        safe_entity = entity.model_copy(update={"title": decision.safe_text})
        return await self.adapter.project(safe_entity)
