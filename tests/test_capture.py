import base64
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_review_workflow import setup

from shiros.apps.workbench import create_workbench
from shiros.capture import CaptureInput, CaptureService, CaptureSnapshot
from shiros.capture_adapters import normalize
from shiros.capture_worker import CaptureWorker


def service(engine: Engine, root: Path, role: str = "owner"):
    workflow, scope = setup(engine, root, role)
    with engine.connect() as c:
        actor = workflow.identity.current(c)
    return CaptureService(engine, actor, scope), workflow


def snapshot(content="Synthetic geometry", attachments=None, provider="gpt", conversation="chat-1"):
    return CaptureSnapshot.model_validate(
        {
            "provider": provider,
            "conversation_id": conversation,
            "messages": [
                {"id": "msg-1", "role": "user", "text": content, "attachments": attachments or []}
            ],
        }
    )


@pytest.mark.integration
def test_grant_token_conversation_and_revoke(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="https://chatgpt.com/c/chat-1"))
    ident = UUID(grant["grant_id"])
    with pytest.raises(PermissionError):
        capture.ingest(ident, snapshot(), token="wrong")
    with pytest.raises(PermissionError):
        capture.ingest(ident, snapshot(conversation="chat-2"), grant["token"])
    capture.ingest(ident, snapshot(), grant["token"])
    assert "token" not in capture.manage("list", CaptureInput())["items"][0]
    capture.manage("revoke", CaptureInput(id=ident))
    with pytest.raises(PermissionError):
        capture.ingest(ident, snapshot(), grant["token"])


@pytest.mark.integration
def test_reader_cannot_authorize(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path, "reader")
    with pytest.raises(PermissionError):
        capture.manage("grant", CaptureInput(conversation_id="chat-1"))


@pytest.mark.integration
def test_idempotent_updates_keep_one_memory(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    ident = UUID(grant["grant_id"])
    assert capture.ingest(ident, snapshot(), grant["token"])["changed"] == 1
    assert capture.ingest(ident, snapshot(), grant["token"])["unchanged"] == 1
    capture.ingest(ident, snapshot("Synthetic geometry updated"), grant["token"])
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(DISTINCT memory_id) FROM memories WHERE scope_id=:s"),
                {"s": capture.scope},
            )
            == 1
        )
        assert (
            c.scalar(
                text("SELECT max(revision) FROM memories WHERE scope_id=:s"), {"s": capture.scope}
            )
            == 2
        )


@pytest.mark.integration
def test_sensitive_update_waits_for_human_and_attachments_link_after_approval(
    db_engine: Engine, tmp_path: Path
):
    capture, workflow = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1", include_files=True))
    ident = UUID(grant["grant_id"])
    attachment = {
        "id": "file-1",
        "filename": "note.txt",
        "data": base64.b64encode(b"original bytes").decode(),
    }
    result = capture.ingest(ident, snapshot("Synthetic health note", [attachment]), grant["token"])
    assert result["pending"] == 1
    capture.ingest(ident, snapshot("Synthetic health note updated", [attachment]), grant["token"])
    queue = workflow.queue(capture.scope)
    assert len(queue) == 1 and queue[0].text.endswith("updated")
    memory = workflow.approve(capture.scope, queue[0].id)
    links = capture.manage("links", CaptureInput(memory_id=memory.memory_id))["items"]
    assert len(links) == 1 and links[0]["state"] == "saved"
    assert (
        capture.manage("links", CaptureInput(file_id=links[0]["file_id"]))["items"][0]["memory_id"]
        == memory.memory_id
    )


@pytest.mark.integration
def test_failed_batch_rolls_back_files_and_memory(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1", include_files=True))
    ident = UUID(grant["grant_id"])
    packet = snapshot(attachments=[{"id": "f", "filename": "f.txt", "data": "eA=="}])
    packet.messages.append(
        snapshot(attachments=[{"id": "bad", "filename": "bad.txt", "data": "!invalid!"}])
        .messages[0]
        .model_copy(update={"id": "msg-2"})
    )
    with pytest.raises(ValueError, match="files.invalid_data"):
        capture.ingest(ident, packet, grant["token"])
    with db_engine.connect() as c:
        for table in ("memories", "library_files", "capture_entries"):
            assert (
                c.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"), {"s": capture.scope}
                )
                == 0
            )


@pytest.mark.integration
def test_binary_files_need_separate_consent(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    with pytest.raises(PermissionError):
        capture.ingest(
            UUID(grant["grant_id"]),
            snapshot(attachments=[{"id": "f", "filename": "f", "data": "eA=="}]),
            grant["token"],
        )


@pytest.mark.integration
def test_scopes_and_deleted_files(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    other, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1", include_files=True))
    ident = UUID(grant["grant_id"])
    capture.ingest(
        ident,
        snapshot(attachments=[{"id": "f", "filename": "f.txt", "data": "eA=="}]),
        grant["token"],
    )
    with pytest.raises(PermissionError):
        other.ingest(ident, snapshot(), grant["token"])
    with db_engine.connect() as c:
        memory = c.scalar(
            text("SELECT memory_id FROM memories WHERE scope_id=:s"), {"s": capture.scope}
        )
    links = capture.manage("links", CaptureInput(memory_id=memory))["items"]
    file_id = links[0]["file_id"]
    assert other.manage("links", CaptureInput(file_id=file_id))["items"] == []
    with db_engine.begin() as c:
        c.execute(text("DELETE FROM library_files WHERE id=:id"), {"id": file_id})
    assert capture.manage("links", CaptureInput(memory_id=memory))["items"][0]["state"] == "deleted"


@pytest.mark.integration
def test_receiver_does_not_accept_web_origin_or_ui_token(db_engine: Engine, tmp_path: Path):
    capture, workflow = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    client = TestClient(
        create_workbench(db_engine, workflow.identity, capture.scope, token="synthetic-ui"),
        base_url="http://127.0.0.1:8001",
    )
    url = "/capture/" + grant["grant_id"]
    payload = snapshot().model_dump(mode="json")
    assert (
        client.post(
            url,
            json=payload,
            headers={"Origin": "https://example.com", "X-Capture-Token": grant["token"]},
        ).status_code
        == 403
    )
    assert (
        client.post(url, json=payload, headers={"X-Shiros-Token": "synthetic-ui"}).status_code
        == 403
    )
    assert (
        client.post(
            url,
            json=payload,
            headers={"Origin": "chrome-extension://synthetic", "X-Capture-Token": grant["token"]},
        ).status_code
        == 200
    )


@pytest.mark.integration
def test_worker_follows_only_granted_source(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    file = tmp_path / "selected.jsonl"
    file.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "id": "m1",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Synthetic answer"}],
                },
            }
        ),
        encoding="utf-8",
    )
    grant = capture.manage(
        "grant", CaptureInput(provider="codex", conversation_id="session-1", source_path=str(file))
    )
    worker = CaptureWorker(db_engine, capture.scope)
    worker.poll()
    worker.poll()
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(*) FROM capture_entries WHERE scope_id=:s"), {"s": capture.scope}
            )
            == 1
        )
    capture.manage("revoke", CaptureInput(id=UUID(grant["grant_id"])))
    file.write_text("invalid", encoding="utf-8")
    worker.poll()


@pytest.mark.parametrize(
    "provider,records",
    [
        (
            "codex",
            [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "hello"}],
                    },
                }
            ],
        ),
        (
            "deepseek-harness",
            [
                {
                    "type": "user/message",
                    "data": {"message": {"content": [{"type": "text", "text": "hello"}]}},
                },
                {
                    "type": "assistant/attempt",
                    "data": {
                        "turn": 1,
                        "step": 1,
                        "stream": [{"type": "text-chunks", "texts": ["an", "swer"]}],
                    },
                },
            ],
        ),
        ("deepseek", [{"role": "user", "content": "hello"}]),
        ("opencode", {"messages": [{"id": "m", "role": "user", "text": "hello"}]}),
        (
            "gpt",
            [
                {
                    "id": "chat-1",
                    "mapping": {
                        "x": {
                            "message": {
                                "id": "m",
                                "author": {"role": "user"},
                                "content": {"parts": ["hello"]},
                            }
                        }
                    },
                }
            ],
        ),
    ],
)
def test_all_provider_readers(provider, records):
    parsed = normalize(provider, "chat-1", json.dumps(records).encode())
    assert parsed.messages[0].text == "hello"


def test_native_reader_does_not_capture_hidden_reasoning_or_read_ungranted_attachment(
    tmp_path: Path,
):
    file = tmp_path / "private.txt"
    file.write_text("never read")
    records = [
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "channel": "analysis",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "hidden"}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "visible"},
                    {"type": "input_image", "image_url": str(file)},
                ],
            },
        },
    ]
    parsed = normalize(
        "codex", "x", json.dumps(records).encode(), include_files=True, attachment_root=None
    )
    assert len(parsed.messages) == 1 and parsed.messages[0].attachments[0].data is None


def test_harness_direct_user_settled_stream_and_session_boundary():
    records = [
        {"type": "session", "id": "session-1"},
        {
            "type": "user/message",
            "data": {"id": "u", "content": [{"type": "text", "text": "question"}]},
        },
        {
            "type": "assistant/attempt",
            "data": {
                "turn": 1,
                "step": 1,
                "stream": [{"type": "text-chunks", "texts": ["partial"]}],
            },
        },
        {
            "type": "assistant/message",
            "data": {
                "turn": 1,
                "step": 1,
                "message": {"content": [{"type": "text", "text": "complete"}]},
            },
        },
    ]
    parsed = normalize("deepseek-harness", "session-1", json.dumps(records).encode())
    assert [x.text for x in parsed.messages] == ["question", "complete"]
    with pytest.raises(PermissionError):
        normalize("deepseek-harness", "other", json.dumps(records).encode())


@pytest.mark.integration
def test_rejected_draft_is_not_resurrected(db_engine: Engine, tmp_path: Path):
    capture, workflow = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    ident = UUID(grant["grant_id"])
    capture.ingest(ident, snapshot("Synthetic health note"), grant["token"])
    draft = workflow.queue(capture.scope)[0]
    workflow.reject(capture.scope, draft.id)
    with pytest.raises(PermissionError):
        capture.ingest(ident, snapshot("Synthetic health changed"), grant["token"])


def test_long_selected_transcript_is_not_truncated():
    records = [{"role": "user", "content": f"Question {n}"} for n in range(601)]
    assert len(normalize("deepseek", "session", json.dumps(records).encode()).messages) == 601


@pytest.mark.integration
def test_identical_text_in_distinct_messages_is_preserved(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    packet = snapshot()
    packet.messages.append(packet.messages[0].model_copy(update={"id": "msg-2"}))
    capture.ingest(UUID(grant["grant_id"]), packet, grant["token"])
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(DISTINCT memory_id) FROM memories WHERE scope_id=:s"),
                {"s": capture.scope},
            )
            == 1
        )
        body = c.scalar(text("SELECT text FROM memories WHERE scope_id=:s"), {"s": capture.scope})
        assert body.count("Synthetic geometry") == 2
