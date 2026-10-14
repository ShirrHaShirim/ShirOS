"""The actual MCP boundary must support safe partial edits and useful validation errors."""

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine
from test_music import run, service

from shiros.adapters.mcp.server import create_server
from shiros.memory_gateway import MemoryGateway


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_release_metadata_merges_and_reports_invalid_inputs(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    run(
        s,
        "import",
        content=json.dumps(
            [
                {
                    "douban_id": "12345678",
                    "title": "Synthetic album",
                    "status": "listened",
                    "rating": 4,
                }
            ]
        ),
    )
    initial = run(s, "list")["items"][0]
    recording = run(s, "save", object={"kind": "recording", "title": "Synthetic real audio"})[
        "item"
    ]
    initial = run(
        s,
        "save",
        id=initial["id"],
        revision=initial["revision"],
        object={
            "title": "Synthetic album",
            "status": "listened",
            "aliases": ["Synthetic alias"],
            "source_notes": "Existing catalogue note",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
            "tracks": [
                {
                    "title": "Synthetic track",
                    "position": 1,
                    "relations": [{"object_id": recording["id"], "role": "recording"}],
                }
            ],
        },
    )["item"]
    gateway = MemoryGateway(db_engine, s.actor, s.scope, "synthetic")
    server = create_server(gateway)
    bad = await server.call_tool(
        "music_mutate",
        {
            "action": "save",
            "payload": {
                "id": initial["id"],
                "revision": initial["revision"],
                "object": {
                    "kind": "release",
                    "title": "Synthetic album",
                    "barcode": 4011790409825,
                    "private_secret_field": "sensitive rejected value",
                },
            },
        },
    )
    assert "music.invalid_payload" in str(bad)
    assert "object.barcode" in str(bad) and "string_type" in str(bad)
    assert "4011790409825" not in str(bad) and "sensitive rejected value" not in str(bad)
    assert "private_secret_field" not in str(bad)
    assert run(s, "detail", id=initial["id"])["item"]["revision"] == initial["revision"]
    response = await server.call_tool(
        "music_mutate",
        {
            "action": "release-metadata",
            "payload": {
                "id": initial["id"],
                "revision": initial["revision"],
                "metadata": {"barcode": "4011790409825", "medium": "Audio CD"},
            },
        },
    )
    assert not response.is_error and "error" not in response.structured_content
    updated = (await gateway.music_query("detail", {"id": initial["id"]}))["item"]
    assert updated["barcode"] == "4011790409825" and updated["medium"] == "Audio CD"
    assert updated["status"] == "listened" and updated["rating"] == 4
    for key in ("relations", "tracks", "aliases", "source_notes", "recording_ids"):
        assert updated[key] == initial[key]
    assert updated["revision"] == initial["revision"] + 1
    conflict = await server.call_tool(
        "music_mutate",
        {
            "action": "release-metadata",
            "payload": {
                "id": initial["id"],
                "revision": initial["revision"],
                "metadata": {"medium": "Vinyl"},
            },
        },
    )
    assert "music.revision_conflict" in str(conflict)
    invalid = await server.call_tool(
        "music_mutate",
        {
            "action": "release-metadata",
            "payload": {
                "id": initial["id"],
                "revision": updated["revision"],
                "metadata": {"barcode": "not a barcode"},
            },
        },
    )
    assert "music.invalid_payload" in str(invalid) and "not a barcode" not in str(invalid)
    reader = service(db_engine, tmp_path / "reader", role="reader")
    denied = await create_server(
        MemoryGateway(db_engine, reader.actor, reader.scope, "synthetic-reader")
    ).call_tool(
        "music_mutate",
        {
            "action": "release-metadata",
            "payload": {
                "id": initial["id"],
                "revision": updated["revision"],
                "metadata": {"medium": "Vinyl"},
            },
        },
    )
    assert "memory.access_denied" in str(denied)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_full_save_updates_legacy_release_without_fabricated_recording(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    run(
        s,
        "import",
        content=json.dumps(
            [{"douban_id": "12345678", "title": "Synthetic legacy album", "status": "wishlist"}]
        ),
    )
    item = run(s, "list")["items"][0]
    server = create_server(MemoryGateway(db_engine, s.actor, s.scope, "synthetic"))
    result = await server.call_tool(
        "music_mutate",
        {
            "action": "save",
            "payload": {
                "id": item["id"],
                "revision": item["revision"],
                "object": {
                    "kind": "release",
                    "title": "Synthetic legacy album",
                    "status": "wishlist",
                    "barcode": "4011790409825",
                    "medium": "Audio CD",
                    "edition": "Synthetic edition",
                },
            },
        },
    )
    assert not result.is_error and "error" not in result.structured_content
    changed = run(s, "detail", id=item["id"])["item"]
    assert changed["revision"] == item["revision"] + 1
    assert changed["barcode"] == "4011790409825" and changed["recording_ids"] == []
