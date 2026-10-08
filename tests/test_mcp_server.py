from typing import Any
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

from shiros.adapters.mcp.server import create_http_app, create_server


class FakeGateway:
    async def music_query(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("music_query")
        return {"items": []}

    async def music_mutate(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append("music_mutate")
        return {"ok": True}

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False

    async def search(self, query: str, limit: int = 10) -> dict[str, Any]:
        self.calls.append("search")
        if self.fail:
            raise RuntimeError("secret database password")
        return {"query": query, "limit": limit}

    async def fetch(self, id: UUID) -> dict[str, Any]:
        self.calls.append("fetch")
        return {"id": str(id)}

    async def context(self, task: str, budget: int = 4000) -> dict[str, Any]:
        self.calls.append("context")
        return {"task": task, "budget": budget}

    async def propose_memory(self, title: str, text: str, request_id: UUID) -> dict[str, Any]:
        self.calls.append("propose_memory")
        if self.fail:
            raise RuntimeError("secret database password")
        return {"request_id": str(request_id), "status": "pending"}

    async def proposal_status(self, request_id: UUID) -> dict[str, Any]:
        self.calls.append("proposal_status")
        return {"request_id": str(request_id), "status": "pending"}

    async def list_tags(self) -> dict[str, Any]:
        self.calls.append("list_tags")
        return {"items": []}

    async def create_tag(self, name: str, parent_id: UUID | None = None) -> dict[str, Any]:
        self.calls.append("create_tag")
        return {"name": name, "parent_id": str(parent_id) if parent_id else None}

    async def add_memory_tags(self, memory_id: UUID, tag_ids: list[UUID]) -> dict[str, Any]:
        self.calls.append("add_memory_tags")
        if self.fail:
            raise PermissionError("secret database password")
        return {"memory_id": str(memory_id), "tag_ids": [str(value) for value in tag_ids]}

    async def edit_memory(
        self, memory_id: UUID, expected_revision: int, text: str, request_id: UUID
    ) -> dict[str, Any]:
        self.calls.append("edit_memory")
        if self.fail:
            raise PermissionError("secret database password")
        return {
            "memory_id": str(memory_id),
            "revision": expected_revision + 1,
            "request_id": str(request_id),
        }

    async def delete_memory(self, memory_id: UUID, expected_revision: int) -> dict[str, Any]:
        self.calls.append("delete_memory")
        return {"memory_id": str(memory_id), "deleted": True}

    async def rename_memory(
        self, memory_id: UUID, expected_revision: int, title: str, request_id: UUID
    ) -> dict[str, Any]:
        self.calls.append("rename_memory")
        return {"memory_id": str(memory_id), "title": title, "revision": expected_revision + 1}

    async def get_memory_sources(self, memory_id: UUID) -> dict[str, Any]:
        self.calls.append("get_memory_sources")
        return {"memory_id": str(memory_id), "sources": []}

    async def set_memory_sources(
        self,
        memory_id: UUID,
        expected_revision: int,
        sources: list[dict[str, Any]],
        request_id: UUID,
    ) -> dict[str, Any]:
        self.calls.append("set_memory_sources")
        return {"memory_id": str(memory_id), "sources": sources, "revision": expected_revision + 1}

    async def migrate_memory_sources(
        self,
        memory_id: UUID,
        expected_revision: int,
        source_text: str,
        source_title: str,
        request_id: UUID,
    ) -> dict[str, Any]:
        self.calls.append("migrate_memory_sources")
        return {"memory_id": str(memory_id), "source_text": source_text, "title": source_title}

    async def delete_tag(self, tag_id: UUID) -> dict[str, Any]:
        self.calls.append("delete_tag")
        return {"tag_id": str(tag_id), "deleted": True}

    async def remove_memory_tags(self, memory_id: UUID, tag_ids: list[UUID]) -> dict[str, Any]:
        self.calls.append("remove_memory_tags")
        return {"memory_id": str(memory_id), "tag_ids": [str(value) for value in tag_ids]}

    async def list_memories(
        self, offset: int = 0, limit: int = 50, tag_id: UUID | None = None
    ) -> dict[str, Any]:
        self.calls.append("list_memories")
        return {"items": [], "total": 0, "offset": offset, "limit": limit}

    def status(self) -> dict[str, Any]:
        return {"read_only": True}


async def test_mcp_tools_are_bounded_with_proposals_and_additive_tag_writes() -> None:
    gateway = FakeGateway()
    server = create_server(gateway)
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {
        "search",
        "fetch",
        "context",
        "status",
        "propose_memory",
        "proposal_status",
        "list_tags",
        "create_tag",
        "add_memory_tags",
        "edit_memory",
        "delete_memory",
        "delete_tag",
        "remove_memory_tags",
        "list_memories",
        "rename_memory",
        "get_memory_sources",
        "set_memory_sources",
        "migrate_memory_sources",
        "music_query",
        "music_mutate",
    }
    read_only = {
        "music_query",
        "search",
        "fetch",
        "context",
        "status",
        "proposal_status",
        "list_tags",
        "list_memories",
        "get_memory_sources",
    }
    destructive = {
        "edit_memory",
        "delete_memory",
        "delete_tag",
        "remove_memory_tags",
        "rename_memory",
        "set_memory_sources",
        "migrate_memory_sources",
    }
    for tool in tools:
        assert tool.annotations
        assert tool.annotations.read_only_hint == (tool.name in read_only)
        assert tool.annotations.destructive_hint == (tool.name in destructive)
        assert tool.annotations.idempotent_hint == (tool.name != "music_mutate")
        assert tool.annotations.open_world_hint is False
        assert "scope" not in tool.input_schema.get("properties", {})
        assert "actor" not in tool.input_schema.get("properties", {})
    invalid: list[tuple[str, dict[str, Any]]] = [
        ("search", {"query": "", "limit": 10}),
        ("search", {"query": "a" * 4001}),
        ("search", {"query": "hello", "limit": 21}),
        ("fetch", {"id": "not-a-uuid"}),
        ("context", {"task": "task", "budget": 16001}),
        ("context", {"task": "task", "budget": 0}),
    ]
    for name, arguments in invalid:
        result = await server.call_tool(name, arguments)
        assert "memory.invalid_request" in str(result)
    assert gateway.calls == []
    assert "hello" in str(await server.call_tool("search", {"query": "hello"}))
    memory_id = str(uuid4())
    assert memory_id in str(await server.call_tool("fetch", {"id": memory_id}))
    assert "4000" in str(await server.call_tool("context", {"task": "task"}))
    assert "read_only" in str(await server.call_tool("status", {}))


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("create_tag", {"name": ""}),
        ("create_tag", {"name": " "}),
        ("create_tag", {"name": "a" * 65}),
        ("create_tag", {"name": "\ud800"}),
        ("create_tag", {"name": "Tag", "parent_id": "secret"}),
        ("create_tag", {"name": {"password": "secret"}}),
        ("add_memory_tags", {"memory_id": "secret", "tag_ids": [str(uuid4())]}),
        ("add_memory_tags", {"memory_id": str(uuid4()), "tag_ids": []}),
        ("add_memory_tags", {"memory_id": str(uuid4()), "tag_ids": [str(uuid4())] * 31}),
        ("add_memory_tags", {"memory_id": str(uuid4()), "tag_ids": ["secret"]}),
    ],
)
async def test_mcp_tags_reject_invalid_input_without_echo(
    name: str, arguments: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    result = str(await create_server(gateway).call_tool(name, arguments))
    assert "memory.invalid_request" in result
    assert "secret" not in result
    assert gateway.calls == []


async def test_mcp_tag_tools_delegate_and_hide_permission_errors() -> None:
    gateway = FakeGateway()
    server = create_server(gateway)
    parent_id, memory_id, tag_id = (str(uuid4()) for _ in range(3))
    assert "items" in str(await server.call_tool("list_tags", {}))
    result = str(await server.call_tool("create_tag", {"name": "Tag", "parent_id": parent_id}))
    assert "Tag" in result and parent_id in result
    result = str(
        await server.call_tool("add_memory_tags", {"memory_id": memory_id, "tag_ids": [tag_id]})
    )
    assert memory_id in result and tag_id in result
    assert gateway.calls == ["list_tags", "create_tag", "add_memory_tags"]
    gateway.fail = True
    result = str(
        await server.call_tool("add_memory_tags", {"memory_id": memory_id, "tag_ids": [tag_id]})
    )
    assert "memory.access_denied" in result and "password" not in result


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        (
            "edit_memory",
            {
                "memory_id": "secret",
                "expected_revision": 1,
                "text": "note",
                "request_id": str(uuid4()),
            },
        ),
        (
            "edit_memory",
            {
                "memory_id": str(uuid4()),
                "expected_revision": 0,
                "text": "note",
                "request_id": str(uuid4()),
            },
        ),
        (
            "edit_memory",
            {
                "memory_id": str(uuid4()),
                "expected_revision": 1,
                "text": " ",
                "request_id": str(uuid4()),
            },
        ),
        (
            "edit_memory",
            {
                "memory_id": str(uuid4()),
                "expected_revision": 1,
                "text": "a" * 20001,
                "request_id": str(uuid4()),
            },
        ),
        (
            "edit_memory",
            {
                "memory_id": str(uuid4()),
                "expected_revision": 1,
                "text": "note",
                "request_id": "secret",
            },
        ),
        ("delete_memory", {"memory_id": "secret", "expected_revision": 1}),
        ("delete_memory", {"memory_id": str(uuid4()), "expected_revision": 0}),
        ("delete_memory", {"memory_id": str(uuid4())}),
        ("delete_tag", {"tag_id": "secret"}),
        ("remove_memory_tags", {"memory_id": "secret", "tag_ids": [str(uuid4())]}),
        ("remove_memory_tags", {"memory_id": str(uuid4()), "tag_ids": []}),
        ("remove_memory_tags", {"memory_id": str(uuid4()), "tag_ids": ["secret"]}),
        ("remove_memory_tags", {"memory_id": str(uuid4()), "tag_ids": [str(uuid4())] * 31}),
        ("list_memories", {"limit": 0}),
        ("list_memories", {"limit": 101}),
        ("list_memories", {"offset": -1}),
        ("list_memories", {"tag_id": "secret"}),
    ],
)
async def test_mcp_mutation_tools_reject_invalid_input_without_echo(
    name: str, arguments: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    result = str(await create_server(gateway).call_tool(name, arguments))
    assert "memory.invalid_request" in result
    assert "secret" not in result
    assert gateway.calls == []


async def test_mcp_mutation_tools_delegate_and_hide_permission_errors() -> None:
    gateway = FakeGateway()
    server = create_server(gateway)
    memory_id, tag_id, request = (str(uuid4()) for _ in range(3))
    result = str(
        await server.call_tool(
            "edit_memory",
            {
                "memory_id": memory_id,
                "expected_revision": 3,
                "text": "Revised synthetic note",
                "request_id": request,
            },
        )
    )
    assert memory_id in result and "4" in result and request in result
    result = str(
        await server.call_tool("delete_memory", {"memory_id": memory_id, "expected_revision": 4})
    )
    assert memory_id in result and "deleted" in result
    result = str(await server.call_tool("delete_tag", {"tag_id": tag_id}))
    assert tag_id in result and "deleted" in result
    result = str(
        await server.call_tool("remove_memory_tags", {"memory_id": memory_id, "tag_ids": [tag_id]})
    )
    assert memory_id in result and tag_id in result
    assert gateway.calls == ["edit_memory", "delete_memory", "delete_tag", "remove_memory_tags"]
    listed = str(await server.call_tool("list_memories", {"offset": 5, "limit": 10}))
    assert "10" in listed and "5" in listed
    gateway.fail = True
    result = str(
        await server.call_tool(
            "edit_memory",
            {
                "memory_id": memory_id,
                "expected_revision": 5,
                "text": "Another synthetic note",
                "request_id": request,
            },
        )
    )
    assert "memory.access_denied" in result and "password" not in result


async def test_mcp_title_and_source_tools_delegate_without_losing_identifiers() -> None:
    gateway = FakeGateway()
    server = create_server(gateway)
    memory_id, request_id, source_id = (str(uuid4()) for _ in range(3))
    base = {"memory_id": memory_id, "expected_revision": 2, "request_id": request_id}
    title = "Project｜Synthetic｜Note"
    renamed = str(await server.call_tool("rename_memory", {**base, "title": title}))
    assert title in renamed and memory_id in renamed and "3" in renamed
    assert memory_id in str(await server.call_tool("get_memory_sources", {"memory_id": memory_id}))
    source = {
        "id": source_id,
        "kind": "document",
        "title": "Synthetic source",
        "text": "Document excerpt",
        "locator": "https://example.test/source",
        "observed_at": "2026-10-08T01:00:00+00:00",
    }
    saved = str(await server.call_tool("set_memory_sources", {**base, "sources": [source]}))
    assert source_id in saved and source["locator"] in saved and source["text"] in saved
    migrated = str(
        await server.call_tool(
            "migrate_memory_sources",
            {
                **base,
                "source_text": "Exact source span",
                "source_title": "Synthetic source",
            },
        )
    )
    assert "Exact source span" in migrated and memory_id in migrated
    assert gateway.calls == [
        "rename_memory",
        "get_memory_sources",
        "set_memory_sources",
        "migrate_memory_sources",
    ]


@pytest.mark.parametrize(
    "name,extra",
    [
        ("rename_memory", {"title": " "}),
        ("rename_memory", {"title": "a" * 251}),
        ("rename_memory", {"title": "\ud800"}),
        (
            "set_memory_sources",
            {"sources": [{"kind": "secret", "title": "source", "text": "note"}]},
        ),
        ("set_memory_sources", {"sources": [{"kind": "note", "title": " ", "text": "note"}]}),
        ("set_memory_sources", {"sources": [{"kind": "note", "title": "source", "text": ""}]}),
        (
            "set_memory_sources",
            {
                "sources": [
                    {
                        "kind": "note",
                        "title": "source",
                        "text": "note",
                        "observed_at": "secret",
                    }
                ]
            },
        ),
        (
            "set_memory_sources",
            {
                "sources": [
                    {
                        "kind": "note",
                        "title": "source",
                        "text": "note",
                        "actor": "secret",
                    }
                ]
            },
        ),
        (
            "set_memory_sources",
            {
                "sources": [
                    {
                        "kind": "note",
                        "title": "source",
                        "text": "note",
                    }
                ]
                * 31
            },
        ),
        (
            "set_memory_sources",
            {
                "sources": [
                    {
                        "kind": "note",
                        "title": "source",
                        "text": "a" * 20000,
                    }
                ]
                * 4
            },
        ),
        ("migrate_memory_sources", {"source_text": " ", "source_title": "source"}),
        ("migrate_memory_sources", {"source_text": "note", "source_title": "a" * 251}),
    ],
)
async def test_mcp_source_requests_are_bounded_and_do_not_echo_invalid_data(
    name: str, extra: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    arguments = {
        "memory_id": str(uuid4()),
        "expected_revision": 1,
        "request_id": str(uuid4()),
        **extra,
    }
    result = str(await create_server(gateway).call_tool(name, arguments))
    assert "memory.invalid_request" in result and "secret" not in result
    assert not gateway.calls


@pytest.mark.parametrize(
    "exception,code",
    [
        (PermissionError("memory.requires_human_review"), "memory.review_required"),
        (PermissionError("privacy.blocked"), "privacy.denied"),
        (PermissionError("permission.denied"), "memory.access_denied"),
        (PermissionError("memory.not_visible"), "memory.access_denied"),
        (ValueError("memory.revision_conflict"), "memory.revision_conflict"),
        (ValueError("memory.idempotency_conflict"), "memory.idempotency_conflict"),
        (ValueError("memory.invalid_title"), "memory.invalid_title"),
        (ValueError("memory.invalid_source_id"), "memory.invalid_source_id"),
        (ValueError("memory.source_span_not_unique"), "memory.source_span_not_unique"),
        (ValueError("memory.too_many_sources"), "memory.too_many_sources"),
        (ValueError("memory.invalid_metadata"), "memory.invalid_metadata"),
        (ValueError("secret database password"), "memory.unavailable"),
    ],
)
async def test_mcp_error_codes_distinguish_review_from_scope_denial_and_conflict(
    exception: Exception, code: str
) -> None:
    class DeniedGateway(FakeGateway):
        async def rename_memory(
            self, memory_id: UUID, expected_revision: int, title: str, request_id: UUID
        ) -> dict[str, Any]:
            raise exception

    result = str(
        await create_server(DeniedGateway()).call_tool(
            "rename_memory",
            {
                "memory_id": str(uuid4()),
                "expected_revision": 1,
                "title": "Project｜Synthetic｜Note",
                "request_id": str(uuid4()),
            },
        )
    )
    assert code in result and "password" not in result


@pytest.mark.parametrize(
    "arguments",
    [
        {"title": "", "text": "note"},
        {"title": " " * 5, "text": "note"},
        {"title": "a" * 251, "text": "note"},
        {"title": "title", "text": ""},
        {"title": "title", "text": " "},
        {"title": "title", "text": "a" * 20001},
        {"title": "title", "text": "\U0001f600" * 17000},
        {"title": "title", "text": "\ud800"},
        {"title": "title", "text": "note", "request_id": "secret-not-uuid"},
        {"title": {"password": "secret"}, "text": "note"},
    ],
)
async def test_mcp_proposal_rejects_invalid_input_without_echo(arguments: dict[str, Any]) -> None:
    gateway = FakeGateway()
    arguments.setdefault("request_id", str(uuid4()))
    result = str(await create_server(gateway).call_tool("propose_memory", arguments))
    assert "memory.invalid_request" in result
    assert "secret" not in result
    assert gateway.calls == []


async def test_mcp_proposal_and_own_status_delegate_uuid() -> None:
    gateway = FakeGateway()
    server = create_server(gateway)
    identifier = str(uuid4())
    result = str(
        await server.call_tool(
            "propose_memory", {"title": "a" * 250, "text": "b" * 20000, "request_id": identifier}
        )
    )
    assert identifier in result and "pending" in result
    result = str(await server.call_tool("proposal_status", {"request_id": identifier}))
    assert identifier in result and "pending" in result
    result = str(await server.call_tool("proposal_status", {"request_id": "secret"}))
    assert "memory.invalid_request" in result and "secret" not in result
    assert gateway.calls == ["propose_memory", "proposal_status"]


async def test_mcp_proposal_hides_internal_errors() -> None:
    gateway = FakeGateway()
    gateway.fail = True
    result = str(
        await create_server(gateway).call_tool(
            "propose_memory", {"title": "Title", "text": "Private note", "request_id": str(uuid4())}
        )
    )
    assert "memory.operation_failed" in result
    assert "password" not in result
    assert "Private note" not in result


async def test_mcp_errors_do_not_expose_gateway_exceptions() -> None:
    gateway = FakeGateway()
    gateway.fail = True
    result = str(await create_server(gateway).call_tool("search", {"query": "hello"}))
    assert "memory.operation_failed" in result
    assert "password" not in result
    invalid = str(
        await create_server(gateway).call_tool("search", {"query": {"password": "secret"}})
    )
    assert "memory.invalid_request" in invalid
    assert "password" not in invalid


@pytest.mark.parametrize(
    ("headers", "path", "expected"),
    [
        ({}, "/mcp", 401),
        ({"Authorization": "Bearer wrong"}, "/mcp", 401),
        ({"Host": "evil.example"}, "/mcp", 403),
        ({"Origin": "https://evil.example"}, "/mcp", 403),
        ({}, "/mcp?token=anything", 403),
    ],
)
def test_mcp_http_rejects_unauthenticated_and_cross_origin(
    headers: dict[str, str], path: str, expected: int
) -> None:
    app = create_http_app(create_server(FakeGateway()), "a" * 43, 8002)
    with TestClient(app, base_url="http://127.0.0.1:8002", client=("127.0.0.1", 5000)) as client:
        assert client.post(path, headers=headers).status_code == expected


def test_mcp_http_authenticated_initialization_and_remote_peer_denial() -> None:
    app = create_http_app(create_server(FakeGateway()), "a" * 43, 8002)
    headers = {
        "Authorization": "Bearer " + "a" * 43,
        "Accept": "application/json, text/event-stream",
        "Origin": "http://127.0.0.1:8002",
    }
    with TestClient(app, base_url="http://127.0.0.1:8002", client=("127.0.0.1", 5000)) as client:
        response = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "ShirOS"
    remote = create_http_app(create_server(FakeGateway()), "a" * 43, 8002)
    with TestClient(remote, base_url="http://127.0.0.1:8002", client=("192.0.2.1", 5000)) as client:
        assert client.post("/mcp", headers=headers).status_code == 403


def test_mcp_http_rejects_short_tokens() -> None:
    with pytest.raises(ValueError, match="mcp.invalid_token"):
        create_http_app(create_server(FakeGateway()), "short")
