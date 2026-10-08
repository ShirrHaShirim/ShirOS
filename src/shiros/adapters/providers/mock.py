"""Deterministic test providers. Never write data, log prompts or call a network."""

import asyncio
from collections.abc import AsyncIterator
from typing import Literal
from uuid import UUID

from shiros.core.conversations import (
    AuthMethod,
    Conversation,
    Message,
    ModelInfo,
    SendMessageRequest,
    StreamChunk,
)
from shiros.core.jobs import CostProfile, RunRequest, RunResult


class MockConversationProvider:
    id = "mock"

    def __init__(self) -> None:
        self._conversations: dict[UUID, Conversation] = {}
        self._active: dict[UUID, asyncio.Event] = {}

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock-echo-v1", provider_id=self.id, auth_methods=tuple(AuthMethod))]

    async def create_conversation(
        self, model_id: str, *, parent_id: UUID | None = None
    ) -> Conversation:
        if model_id != "mock-echo-v1":
            raise ValueError("Unknown mock model")
        if parent_id is not None and parent_id not in self._conversations:
            raise ValueError("Unknown parent conversation")
        result = Conversation(provider_id=self.id, model_id=model_id, parent_id=parent_id)
        self._conversations[result.id] = result
        return result

    async def send_message(self, request: SendMessageRequest) -> Message:
        chunks = [chunk.delta async for chunk in self.stream_message(request)]
        return Message(
            conversation_id=request.conversation_id, role="assistant", content="".join(chunks)
        )

    async def stream_message(self, request: SendMessageRequest) -> AsyncIterator[StreamChunk]:
        if request.conversation_id not in self._conversations:
            raise ValueError("Unknown conversation")
        if request.request_id in self._active:
            raise ValueError("Request already active")
        cancelled = asyncio.Event()
        self._active[request.request_id] = cancelled
        try:
            for word in ("Mock: " + request.content).splitlines(keepends=True):
                for offset in range(0, len(word), 8):
                    await asyncio.sleep(0)
                    if cancelled.is_set():
                        raise asyncio.CancelledError("Mock request cancelled")
                    yield StreamChunk(
                        request_id=request.request_id, delta=word[offset : offset + 8]
                    )
            if cancelled.is_set():
                raise asyncio.CancelledError("Mock request cancelled")
            yield StreamChunk(request_id=request.request_id, delta="", done=True)
        finally:
            self._active.pop(request.request_id, None)

    async def cancel(self, request_id: UUID) -> None:
        if request_id in self._active:
            self._active[request_id].set()


class MockWorker:
    id = "mock-worker"
    capabilities: tuple[str, ...] = ("synthetic-task",)
    availability: Literal["available", "unavailable"] = "available"
    cost_profile = CostProfile()

    async def run(self, request: RunRequest) -> RunResult:
        if self.availability != "available":
            raise RuntimeError("Worker unavailable")
        return RunResult(job_id=request.job_id, status="completed", output="Mock task completed")
