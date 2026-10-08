"""Read-only protocol smoke against synthetic demo; no credentials in output."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {
    "music_query",
    "music_mutate",
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
}
READ_ONLY = {
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
DESTRUCTIVE = {
    "edit_memory",
    "delete_memory",
    "delete_tag",
    "remove_memory_tags",
    "rename_memory",
    "set_memory_sources",
    "migrate_memory_sources",
}


async def main() -> None:
    parser = argparse.ArgumentParser(description="ShirOS MCP protocol smoke")
    parser.add_argument("--client", default="codex")
    arguments = parser.parse_args()
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "shiros.memory_bridge", "serve", "--client", arguments.client],
        cwd=str(ROOT),
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            listing = await session.list_tools()
            assert {tool.name for tool in listing.tools} == TOOLS
            for tool in listing.tools:
                assert tool.annotations
                assert tool.annotations.read_only_hint == (tool.name in READ_ONLY)
                assert tool.annotations.destructive_hint == (tool.name in DESTRUCTIVE)
            for name, tool_arguments in (
                ("status", {}),
                ("music_query", {"action": "list", "payload": {"limit": 2}}),
                ("list_tags", {}),
                ("list_memories", {"limit": 2}),
                ("search", {"query": "Synthetic", "limit": 2}),
                ("context", {"task": "Synthetic reading notes", "budget": 2000}),
            ):
                result = await session.call_tool(name, tool_arguments)
                assert not result.is_error, name
                payload = result.structured_content
                if payload is None:
                    block = result.content[0]
                    assert block.type == "text"
                    payload = json.loads(block.text)
                assert "error" not in payload, name
                if name == "status":
                    assert payload["version"] == "0.4.1"
    print("MCP initialize, tools/list, status, list_tags, search and bounded context passed.")


if __name__ == "__main__":
    asyncio.run(main())
