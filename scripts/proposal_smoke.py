"""Explicit synthetic MCP proposal smoke; leaves a pending item for human review."""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main(client: str) -> None:
    request_id = str(uuid4())
    marker = "SyntheticProposal" + request_id.replace("-", "").translate(
        str.maketrans("0123456789", "ghijklmnop")
    )
    arguments = {
        "title": "Synthetic v0.3.3 candidate write demo",
        "text": f"{marker}: synthetic write test only, not personal information. "
        "A proposed memory requires human approval before retrieval.",
        "request_id": request_id,
    }
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "shiros.memory_bridge", "serve", "--client", client],
        cwd=str(ROOT),
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            first = await session.call_tool("propose_memory", arguments)
            assert not first.is_error and first.structured_content
            assert first.structured_content["status"] == "pending"
            retry = await session.call_tool("propose_memory", arguments)
            assert not retry.is_error and retry.structured_content == first.structured_content
            status = await session.call_tool("proposal_status", {"request_id": request_id})
            assert not status.is_error and status.structured_content == first.structured_content
            search = await session.call_tool("search", {"query": marker})
            assert not search.is_error and search.structured_content
            assert search.structured_content["results"] == []
            print(json.dumps({"client": client, **first.structured_content}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submit-synthetic-candidate", action="store_true", required=True)
    parser.add_argument("--client", choices=("codex", "chatgpt", "work"), default="codex")
    asyncio.run(main(parser.parse_args().client))
