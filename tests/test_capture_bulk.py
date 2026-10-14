import base64
import io
import json
import zipfile
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_capture import service, snapshot

from shiros.apps.workbench import create_workbench
from shiros.capture import CaptureInput
from shiros.capture_export import export_packet, export_records, import_export
from shiros.file_library import FileRequest


def record(ident="export-chat", body="Synthetic geometry", title="几何笔记"):
    return {
        "id": ident,
        "title": title,
        "mapping": {
            "m1": {
                "parent": None,
                "message": {
                    "id": "m1",
                    "author": {"role": "user"},
                    "content": {"parts": [body]},
                    "create_time": 1234,
                },
            },
            "m2": {
                "parent": "m1",
                "message": {
                    "id": "m2",
                    "author": {"role": "assistant"},
                    "content": {"parts": ["A triangle has three sides."]},
                },
            },
        },
    }


def upload(records, include_files=False):
    return FileRequest(
        filename="conversations.json",
        include_files=include_files,
        data=base64.b64encode(json.dumps(records, ensure_ascii=False).encode()).decode(),
    )


@pytest.mark.integration
def test_bulk_grants_titles_conversation_isolation_and_revoke(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(all_conversations=True))
    assert grant["all_conversations"] and grant["conversation_id"] == "__all__"
    packet = snapshot(conversation="chat-a").model_copy(update={"conversation_title": "几何讨论"})
    first = capture.ingest(UUID(grant["grant_id"]), packet, grant["token"])
    second = capture.ingest(
        UUID(grant["grant_id"]), snapshot(conversation="chat-b"), grant["token"]
    )
    assert first["grant_id"] != second["grant_id"] and first["title"] == "几何讨论"
    assert len(capture.manage("list", CaptureInput())["items"]) == 1
    capture.manage("revoke", CaptureInput(id=UUID(grant["grant_id"])))
    with pytest.raises(PermissionError, match="not_authorized"):
        capture.ingest(UUID(first["grant_id"]), packet)
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(*) FROM capture_grants WHERE scope_id=:s AND enabled"),
                {"s": capture.scope},
            )
            == 0
        )


@pytest.mark.integration
def test_bulk_files_cannot_inherit_wider_individual_consent(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    capture.manage("grant", CaptureInput(conversation_id="chat-1", include_files=True))
    bulk = capture.manage("grant", CaptureInput(all_conversations=True))
    with pytest.raises(PermissionError, match="files_not_authorized"):
        capture.ingest(
            UUID(bulk["grant_id"]),
            snapshot(attachments=[{"id": "f", "filename": "f.txt", "data": "eA=="}]),
            bulk["token"],
        )


@pytest.mark.integration
def test_authorize_probe_requires_pairing_token_and_stops_after_revoke(
    db_engine: Engine, tmp_path: Path
):
    capture, workflow = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(all_conversations=True))
    client = TestClient(
        create_workbench(db_engine, workflow.identity, capture.scope, token="synthetic-ui"),
        base_url="http://127.0.0.1:8001",
    )
    path = "/capture/" + grant["grant_id"] + "/authorize"
    assert client.post(path, json={}, headers={"X-Shiros-Token": "synthetic-ui"}).status_code == 403
    headers = {"X-Capture-Token": grant["token"]}
    assert client.post(path, json={}, headers=headers).json()["all_conversations"]
    capture.manage("revoke", CaptureInput(id=UUID(grant["grant_id"])))
    assert client.post(path, json={}, headers=headers).status_code == 403


@pytest.mark.integration
def test_export_separate_memories_titles_repeat_and_source_links(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    value = upload([record("a"), record("b", title="第二个几何讨论")])
    first = import_export(db_engine, capture.actor, capture.scope, value)
    assert (
        first["imported"] == first["changed"] == 2 and not first["failed"] and first["pending"] == 0
    )
    second = import_export(db_engine, capture.actor, capture.scope, value)
    assert second["unchanged"] == 2 and second["changed"] == 0
    with db_engine.connect() as c:
        rows = (
            c.execute(
                text("SELECT memory_id,text FROM memories WHERE scope_id=:s"), {"s": capture.scope}
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2 and all("原始时间: 1970-01-01T00:20:34" in x["text"] for x in rows)
        assert (
            c.scalar(
                text("SELECT count(*) FROM library_files WHERE scope_id=:s"), {"s": capture.scope}
            )
            == 1
        )
    for row in rows:
        links = capture.manage("links", CaptureInput(memory_id=row["memory_id"]))["items"]
        assert len(links) == 1 and links[0]["filename"] == "conversations.json"


@pytest.mark.integration
def test_export_partial_failure_sensitive_review_and_long_ordinary(
    db_engine: Engine, tmp_path: Path
):
    capture, workflow = service(db_engine, tmp_path)
    result = import_export(
        db_engine,
        capture.actor,
        capture.scope,
        upload(
            [
                record("a", body="Synthetic geometry. " * 1100),
                record("b", body="Synthetic health note"),
                record("c", body="password=synthetic-no-store"),
                record("d", body="Another triangle discussion"),
            ]
        ),
    )
    assert result["imported"] == 4 and result["pending"] == 2 and not result["failed"]
    assert result["privacy_omitted_messages"] == 1
    assert len(workflow.queue(capture.scope)) == 2
    with db_engine.connect() as c:
        bodies = c.scalars(
            text("SELECT text FROM memories WHERE scope_id=:s"), {"s": capture.scope}
        ).all()
        assert len(bodies) == 2 and max(map(len, bodies)) > 20000
        assert all("Synthetic health" not in x and "synthetic-no-store" not in x for x in bodies)


@pytest.mark.integration
def test_zip_attachment_consent_upgrade_and_reverse_links(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    item = record()
    item["mapping"]["m1"]["message"]["metadata"] = {
        "attachments": [{"id": "file-1", "name": "note.txt"}]
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("conversations.json", json.dumps([item]))
        z.writestr("note.txt", b"original synthetic file")
    value = FileRequest(filename="export.zip", data=base64.b64encode(buffer.getvalue()).decode())
    assert import_export(db_engine, capture.actor, capture.scope, value)["imported"] == 1
    upgraded = import_export(
        db_engine, capture.actor, capture.scope, value.model_copy(update={"include_files": True})
    )
    assert upgraded["unchanged"] == 1 and not upgraded["failed"]
    with db_engine.connect() as c:
        file_id = c.scalar(
            text("SELECT id FROM library_files WHERE scope_id=:s AND filename='note.txt'"),
            {"s": capture.scope},
        )
    assert (
        file_id and capture.manage("links", CaptureInput(file_id=file_id))["items"][0]["memory_id"]
    )


@pytest.mark.integration
def test_sensitive_word_across_chunk_boundary_never_auto_approves(
    db_engine: Engine, tmp_path: Path
):
    capture, workflow = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    result = capture.ingest(
        UUID(grant["grant_id"]),
        snapshot("geometry " * 700 + "health" + " triangle" * 700),
        grant["token"],
    )
    assert (
        result["pending"] == result["changed"]
        and len(workflow.queue(capture.scope)) == result["changed"]
    )


@pytest.mark.parametrize(
    "bad", [b"{}", b"[]", b"not json", b'{"mapping":[]}', b'[{"mapping":null}]']
)
def test_invalid_export_structure(bad):
    with pytest.raises(ValueError, match="invalid_export"):
        export_records(bad, "conversations.json")


def test_export_ignores_hidden_reasoning_and_preserves_branches():
    item = record()
    item["mapping"]["analysis"] = {
        "message": {
            "author": {"role": "assistant"},
            "channel": "analysis",
            "content": {"parts": ["hidden reasoning"]},
        }
    }
    item["mapping"]["branch"] = {
        "parent": "m1",
        "message": {"author": {"role": "assistant"}, "content": {"parts": ["Alternate branch"]}},
    }
    packet = export_packet(item, 0, None, False)
    assert "hidden reasoning" not in "\n".join(
        m.text for m in packet.messages
    ) and "Alternate branch" in "\n".join(m.text for m in packet.messages)


@pytest.mark.integration
def test_explicit_export_reimport_can_replace_rejected_copy(db_engine: Engine, tmp_path: Path):
    capture, review = service(db_engine, tmp_path)
    value = upload([record("retry-conversation", body="Synthetic health note")])
    first = import_export(db_engine, capture.actor, capture.scope, value)
    assert first["pending"] == 1
    old = review.queue(capture.scope)[0].id
    review.reject(capture.scope, old)
    second = import_export(db_engine, capture.actor, capture.scope, value)
    assert second["pending"] == 1 and not second["failed"]
    queue = review.queue(capture.scope)
    assert len(queue) == 1 and queue[0].id != old and "Synthetic health note" in queue[0].text


@pytest.mark.integration
def test_export_new_round_updates_existing_conversation_instead_of_new_memory(
    db_engine: Engine, tmp_path: Path
):
    capture, _ = service(db_engine, tmp_path)
    original = record("stable-conversation-a")
    assert (
        import_export(db_engine, capture.actor, capture.scope, upload([original]))["changed"] == 1
    )
    with db_engine.connect() as c:
        stable = c.scalar(
            text("SELECT memory_id FROM memories WHERE scope_id=:s"), {"s": capture.scope}
        )
    original["title"] = "Updated conversation A title"
    for ident, role, body, parent in [
        ("m3", "user", "Synthetic new question about squares.", "m2"),
        ("m4", "assistant", "A square has four sides.", "m3"),
    ]:
        original["mapping"][ident] = {
            "parent": parent,
            "message": {"id": ident, "author": {"role": role}, "content": {"parts": [body]}},
        }
    value = upload([original]).model_copy(update={"filename": "new-export-conversations.json"})
    assert import_export(db_engine, capture.actor, capture.scope, value)["changed"] == 1
    assert import_export(db_engine, capture.actor, capture.scope, value)["unchanged"] == 1
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(DISTINCT memory_id) FROM memories WHERE scope_id=:s"),
                {"s": capture.scope},
            )
            == 1
        )
        latest = c.execute(
            text(
                "SELECT memory_id,revision,text FROM memories WHERE scope_id=:s "
                "ORDER BY revision DESC LIMIT 1"
            ),
            {"s": capture.scope},
        ).one()
        assert latest.memory_id == stable and latest.revision == 2
        assert (
            "A triangle has three sides." in latest.text
            and "A square has four sides." in latest.text
        )
        assert latest.text.index("Synthetic new question") < latest.text.index("A square")
        assert (
            c.scalar(
                text(
                    "SELECT count(*) FROM capture_messages cm JOIN capture_conversations cc "
                    "ON cc.id=cm.conversation_id WHERE cc.scope_id=:s"
                ),
                {"s": capture.scope},
            )
            == 4
        )
