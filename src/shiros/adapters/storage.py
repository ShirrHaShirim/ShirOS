"""Artifact storage holds bytes; domain records refer to stable UUIDs."""

from typing import Protocol
from uuid import UUID


class StorageAdapter(Protocol):
    """Guarded internal port; real filesystem/object storage is deferred."""

    async def put(self, artifact_id: UUID, content: bytes, media_type: str) -> None: ...

    async def get(self, artifact_id: UUID) -> bytes | None: ...
