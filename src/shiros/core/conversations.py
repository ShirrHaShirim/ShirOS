"""Human chat contracts; a provider is not an autonomous worker."""

from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import Field

from shiros.core.schemas import Record, Schema


class AuthMethod(StrEnum):
    API_KEY = "API_KEY"
    OAUTH = "OAUTH"
    HARNESS_SESSION = "HARNESS_SESSION"


class ModelInfo(Schema):
    id: str
    provider_id: str
    auth_methods: tuple[AuthMethod, ...]


class Conversation(Record):
    provider_id: str
    model_id: str
    credential_profile_id: UUID | None = None
    project_id: UUID | None = None
    parent_id: UUID | None = None
    title: str = "Untitled"


class Message(Record):
    conversation_id: UUID
    role: Literal["user", "assistant", "system"]
    content: str


class SendMessageRequest(Schema):
    conversation_id: UUID
    request_id: UUID
    content: str = Field(min_length=1, max_length=100_000)


class StreamChunk(Schema):
    request_id: UUID
    delta: str
    done: bool = False


class ConversationRepository(Protocol):
    """Separate history store. All writes require permission/privacy gates."""

    async def get(self, conversation_id: UUID) -> Conversation | None: ...

    async def save(self, conversation: Conversation) -> None: ...

    async def append_message(self, message: Message) -> None: ...


class ConversationProvider(Protocol):
    """Python snake_case corresponds to listModels/createConversation/etc.

    Credential profile IDs are references, never the actual credentials.
    cancel targets an active request, leaving later turns unaffected.
    """

    id: str

    async def list_models(self) -> list[ModelInfo]: ...

    async def create_conversation(
        self, model_id: str, *, parent_id: UUID | None = None
    ) -> Conversation: ...

    async def send_message(self, request: SendMessageRequest) -> Message: ...

    def stream_message(self, request: SendMessageRequest) -> AsyncIterator[StreamChunk]: ...

    async def cancel(self, request_id: UUID) -> None: ...
