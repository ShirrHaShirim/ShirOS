"""Agent Job/Run/Checkpoint contracts; no human chat state is reused."""

from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field

from shiros.core.context import ContextBundle
from shiros.core.schemas import Record, Schema


class CostProfile(Schema):
    currency: str = "USD"
    per_run: float = Field(default=0, ge=0)


class RunRequest(Schema):
    job_id: UUID
    task: str
    context: ContextBundle


class RunResult(Record):
    job_id: UUID
    status: Literal["completed", "failed", "cancelled"]
    output: str


class AgentWorker(Protocol):
    """One run per invocation; routing, tools and persistence are deferred."""

    id: str
    capabilities: tuple[str, ...]
    availability: Literal["available", "unavailable"]
    cost_profile: CostProfile

    async def run(self, request: RunRequest) -> RunResult: ...
