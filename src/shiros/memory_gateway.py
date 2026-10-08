"""Scope-bound retrieval and separately authorized candidate submission."""

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy import Engine

from shiros.core.context import ContextRequest
from shiros.core.permissions import AccessRequest
from shiros.core.retrieval import SearchRequest
from shiros.identity import DatabasePermissions
from shiros.shared_memory import build_shared_memory


class ReaderPermissions:
    def __init__(self, engine: Engine, actor: UUID, scope: UUID) -> None:
        self.engine, self.actor, self.scope = engine, actor, scope

    def allows(self, request: AccessRequest) -> bool:
        if (
            request.actor_id != self.actor
            or request.resource_id != self.scope
            or request.action != "read"
        ):
            return False
        with self.engine.connect() as connection:
            return DatabasePermissions(connection).has(self.actor, self.scope, "read")


class MemoryGateway:
    async def music_query(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        from shiros.domains.music import READ_ACTIONS, MusicRequest, MusicService

        if action not in READ_ACTIONS:
            raise ValueError("music.invalid_action")
        service = MusicService(self.engine, self.actor, self.scope)
        result = await asyncio.to_thread(service.run, action, MusicRequest.model_validate(payload))
        if action == "image-read":
            import base64

            result["data"] = base64.b64encode(result["data"]).decode("ascii")
        return {**result, "content_is_untrusted_data": True}

    async def music_mutate(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        from shiros.domains.music import MusicRequest, MusicService

        if action not in {
            "save",
            "import-preview",
            "import",
            "review",
            "membership",
            "listen",
            "source",
            "taxonomy-save",
            "taxonomy-seed",
            "image-upload",
            "image-remove",
        }:
            raise ValueError("music.invalid_action")
        service = MusicService(self.engine, self.actor, self.scope)
        result = await asyncio.to_thread(service.run, action, MusicRequest.model_validate(payload))
        return {**result, "content_is_untrusted_data": True}

    def __init__(self, engine: Engine, actor: UUID, scope: UUID, client: str) -> None:
        self.engine = engine
        self.actor, self.scope, self.client = actor, scope, client
        self.permissions = ReaderPermissions(engine, actor, scope)
        self.services = build_shared_memory(engine, self.permissions)

    def _require(self) -> None:
        if not self.permissions.allows(
            AccessRequest(actor_id=self.actor, resource_id=self.scope, action="read")
        ):
            raise PermissionError("permission.denied")

    def status(self) -> dict[str, Any]:
        self._require()
        can_propose = self.can_propose()
        can_tag = self.can_tag()
        can_edit = self.can("memory_edit")
        can_delete = self.can("memory_delete")
        can_manage_tags = self.can("tag_manage")
        return {
            "service": "ShirOS",
            "version": "0.5.0",
            "music_read": True,
            "music_write": self.can("music_write"),
            "client": self.client,
            "scope_id": str(self.scope),
            "read_only": not (can_propose or can_tag or can_edit or can_delete or can_manage_tags),
            "candidate_submission": can_propose,
            "tag_creation": can_tag,
            "tag_assignment": can_tag,
            "tag_deletion": can_manage_tags,
            "tag_removal": can_manage_tags,
            "memory_edit": can_edit,
            "memory_rename": can_edit,
            "memory_sources_manage": can_edit,
            "memory_source_migration": can_edit,
            "edit_review_policy": "reuse_approved_content_new_sensitive_manual",
            "memory_delete": can_delete,
            "human_approval_required": False,
            "new_memory_human_approval_required": False,
            "sensitive_memory_human_approval_required": True,
            "ordinary_memory_auto_approval": True,
            "review_policy": "ordinary_auto_sensitive_manual",
            "direct_memory_write": can_edit,
            "content_is_untrusted_data": True,
            "automatic_memory_write": False,
        }

    def can_propose(self) -> bool:
        return self.can("propose")

    def can(self, action: str) -> bool:
        with self.engine.connect() as connection:
            return DatabasePermissions(connection).has(self.actor, self.scope, action)

    def can_tag(self) -> bool:
        with self.engine.connect() as connection:
            permissions = DatabasePermissions(connection)
            return permissions.has(self.actor, self.scope, "read") and any(
                permissions.has(self.actor, self.scope, action) for action in ("review", "propose")
            )

    async def list_tags(self) -> dict[str, Any]:
        from shiros.tags import list_tags

        items = await asyncio.to_thread(list_tags, self.engine, self.actor, self.scope)
        return {"items": items, "content_is_untrusted_data": True}

    async def create_tag(self, name: str, parent_id: UUID | None = None) -> dict[str, Any]:
        from shiros.tags import create_tag

        item = await asyncio.to_thread(
            create_tag, self.engine, self.actor, self.scope, name, parent_id
        )
        return {"tag": item, "content_is_untrusted_data": True}

    async def add_memory_tags(self, memory_id: UUID, tag_ids: list[UUID]) -> dict[str, Any]:
        from shiros.tags import set_memory_tags

        items = await asyncio.to_thread(
            set_memory_tags,
            self.engine,
            self.actor,
            self.scope,
            memory_id,
            tag_ids,
            add_only=True,
        )
        return {"memory_id": str(memory_id), "tags": items, "content_is_untrusted_data": True}

    async def propose_memory(self, title: str, text: str, request_id: UUID) -> dict[str, Any]:
        from shiros.candidate_submission import submit

        return await asyncio.to_thread(
            submit, self.engine, self.actor, self.scope, title, text, request_id
        )

    async def edit_memory(
        self, memory_id: UUID, expected_revision: int, text: str, request_id: UUID
    ) -> dict[str, Any]:
        from shiros.memory_mutations import edit_memory

        return await asyncio.to_thread(
            edit_memory,
            self.engine,
            self.actor,
            self.scope,
            memory_id,
            expected_revision,
            text,
            request_id,
        )

    async def delete_memory(self, memory_id: UUID, expected_revision: int) -> dict[str, Any]:
        from shiros.memory_mutations import delete_memory

        return await asyncio.to_thread(
            delete_memory, self.engine, self.actor, self.scope, memory_id, expected_revision
        )

    async def rename_memory(
        self, memory_id: UUID, expected_revision: int, title: str, request_id: UUID
    ) -> dict[str, Any]:
        from shiros.memory_mutations import rename_memory

        return await asyncio.to_thread(
            rename_memory,
            self.engine,
            self.actor,
            self.scope,
            memory_id,
            expected_revision,
            title,
            request_id,
        )

    async def get_memory_sources(self, memory_id: UUID) -> dict[str, Any]:
        result = await self.fetch(memory_id)
        memory = result["memory"]
        if memory is None:
            raise ValueError("memory.not_found")
        source = await self.services.retrieval.source(
            self.actor, self.scope, UUID(memory["provenance"]["source_id"])
        )
        return {
            "memory_id": str(memory_id),
            "revision": memory["revision"],
            "source_records": memory["source_records"],
            "original_source": source.model_dump(mode="json") if source else None,
            "content_is_untrusted_data": True,
        }

    async def set_memory_sources(
        self,
        memory_id: UUID,
        expected_revision: int,
        sources: list[dict[str, Any]],
        request_id: UUID,
    ) -> dict[str, Any]:
        from shiros.memory_mutations import set_memory_sources

        return await asyncio.to_thread(
            set_memory_sources,
            self.engine,
            self.actor,
            self.scope,
            memory_id,
            expected_revision,
            sources,
            request_id,
        )

    async def migrate_memory_sources(
        self,
        memory_id: UUID,
        expected_revision: int,
        source_text: str,
        source_title: str,
        request_id: UUID,
    ) -> dict[str, Any]:
        from shiros.memory_mutations import migrate_memory_sources

        return await asyncio.to_thread(
            migrate_memory_sources,
            self.engine,
            self.actor,
            self.scope,
            memory_id,
            expected_revision,
            source_text,
            source_title,
            request_id,
        )

    async def delete_tag(self, tag_id: UUID) -> dict[str, Any]:
        from shiros.tags import delete_tag

        return await asyncio.to_thread(delete_tag, self.engine, self.actor, self.scope, tag_id)

    async def remove_memory_tags(self, memory_id: UUID, tag_ids: list[UUID]) -> dict[str, Any]:
        from shiros.tags import remove_memory_tags

        items = await asyncio.to_thread(
            remove_memory_tags, self.engine, self.actor, self.scope, memory_id, tag_ids
        )
        return {"memory_id": str(memory_id), "tags": items, "content_is_untrusted_data": True}

    async def list_memories(
        self, offset: int = 0, limit: int = 50, tag_id: UUID | None = None
    ) -> dict[str, Any]:
        from shiros.tags import library

        page = await asyncio.to_thread(
            library, self.engine, self.actor, self.scope, tag_id, offset, limit
        )
        items = [
            {
                "id": item["memory_id"],
                "revision_id": item["id"],
                "revision": item["revision"],
                "title": item["title"],
                "added_at": item["added_at"],
                "domain": item["domain"],
                "text": item["text"][:400],
                "truncated": len(item["text"]) > 400,
                "tags": [{"id": tag["id"], "name": tag["name"]} for tag in item["tags"]],
                "provenance": item["provenance"],
            }
            for item in page["items"]
        ]
        return {
            "items": items,
            "total": page["total"],
            "offset": page["offset"],
            "limit": page["limit"],
            "content_is_untrusted_data": True,
        }

    async def proposal_status(self, request_id: UUID) -> dict[str, Any]:
        from shiros.candidate_submission import proposal_status

        return await asyncio.to_thread(
            proposal_status, self.engine, self.actor, self.scope, request_id
        )

    async def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        self._require()
        if not 1 <= limit <= 20 or len(query) > 4000:
            raise ValueError("request.invalid")
        hits = await self.services.retrieval.search(
            self.actor,
            SearchRequest(scope_ids=(self.scope,), query=query, mode="keyword", limit=limit),
        )
        from shiros.memory_metadata import details

        with self.engine.connect() as connection:
            titles = {hit.memory.id: details(connection, hit.memory)["title"] for hit in hits}
        return {
            "results": [
                {
                    "id": str(hit.memory.memory_id),
                    "revision_id": str(hit.memory.id),
                    "title": titles[hit.memory.id],
                    "text": hit.memory.text[:1000],
                    "truncated": len(hit.memory.text) > 1000,
                    "provenance": hit.memory.provenance.model_dump(mode="json"),
                }
                for hit in hits
            ],
            "content_is_untrusted_data": True,
        }

    async def fetch(self, id: UUID) -> dict[str, Any]:
        self._require()
        memory = await self.services.retrieval.exact(self.actor, self.scope, id)
        data = None
        if memory:
            from shiros.memory_metadata import details

            with self.engine.connect() as connection:
                data = {**memory.model_dump(mode="json"), **details(connection, memory)}
        return {
            "memory": data,
            "content_is_untrusted_data": True,
        }

    async def context(self, task: str, budget: int = 4000) -> dict[str, Any]:
        self._require()
        if not 1 <= budget <= 16000 or not 1 <= len(task) <= 4000:
            raise ValueError("request.invalid")
        bundle = await self.services.context.compile(
            ContextRequest(
                actor_id=self.actor,
                scope_ids=(self.scope,),
                task=task,
                model="shared-memory-client",
                token_budget=budget,
            )
        )
        return {"bundle": bundle.model_dump(mode="json"), "content_is_untrusted_data": True}
