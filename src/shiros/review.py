"""Trusted in-process review authority. No HTTP endpoint mints approvals.

Approval IDs are random, actor-bound, expire, and cover the entire exact request.
No original input, hash of rejected input, or review token is written to disk.
"""

import hashlib
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from shiros.core.ingestion import InferenceWrite, MemoryWrite
from shiros.core.permissions import AccessRequest, PermissionService
from shiros.core.privacy import PrivacyInput, PrivacyPolicy

WriteRequest = MemoryWrite | InferenceWrite


def fingerprint(request: WriteRequest) -> str:
    return hashlib.sha256(request.model_dump_json().encode("utf-8")).hexdigest()


def request_texts(request: WriteRequest) -> tuple[str, ...]:
    if isinstance(request, MemoryWrite):
        return (request.source_title, request.source_text, request.text, request.entity_title)
    return (request.text,)


@dataclass(frozen=True)
class Approval:
    actor_id: UUID
    reviewer_id: UUID
    digest: str
    expires: float


class ReviewAuthority:
    def __init__(self, permissions: PermissionService, privacy: PrivacyPolicy) -> None:
        self.permissions = permissions
        self.privacy = privacy
        self._approvals: dict[UUID, Approval] = {}

    def approve(self, reviewer_id: UUID, actor_id: UUID, request: WriteRequest) -> UUID:
        if not self.permissions.allows(
            AccessRequest(
                actor_id=reviewer_id,
                action="review",
                resource_id=request.scope_id,
            )
        ):
            raise PermissionError("permission.review_denied")
        for value in request_texts(request):
            if not self.privacy.is_persistence_allowed(PrivacyInput(text=value, reviewed=True)):
                raise PermissionError("privacy.persistence_denied")
        now = time.monotonic()
        self._approvals = {
            key: value for key, value in self._approvals.items() if value.expires > now
        }
        token = uuid4()
        self._approvals[token] = Approval(actor_id, reviewer_id, fingerprint(request), now + 1800)
        return token

    def validate(self, token: UUID, actor_id: UUID, request: WriteRequest) -> Approval:
        approval = self._approvals.get(token)
        if (
            approval is None
            or approval.actor_id != actor_id
            or approval.digest != fingerprint(request)
            or approval.expires <= time.monotonic()
            or not self.permissions.allows(
                AccessRequest(
                    actor_id=approval.reviewer_id,
                    action="review",
                    resource_id=request.scope_id,
                )
            )
        ):
            raise PermissionError("review.invalid_approval")
        return approval
