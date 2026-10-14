"""Whole-conversation persistence, ordering, review and long-body boundaries."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_capture import service, snapshot

from shiros.apps.workbench import create_workbench
from shiros.candidate_submission import submit
from shiros.capture import CaptureInput, CaptureSnapshot
from shiros.capture_conversations import conversation_memory_title
from shiros.intake import preview_content
from shiros.markdown_export import memory_markdown
from shiros.memory_limits import MAX_MEMORY_CHARS
from shiros.memory_mutations import edit_memory
from shiros.visibility import visible_memory


def test_capture_title_matches_complete_conversation_record_type():
    assert conversation_memory_title("工程讨论") == "对话｜工程讨论｜完整对话"
    assert conversation_memory_title("ShirOS|工程讨论|完整记录") == "ShirOS｜工程讨论｜完整对话"
    assert conversation_memory_title("数学｜研究讨论｜完整对话") == "数学｜研究讨论｜完整对话"
    assert conversation_memory_title("A|B") == "对话｜A B｜完整对话"


@pytest.mark.integration
def test_reviewed_capture_keeps_canonical_title(db_engine: Engine, tmp_path: Path):
    capture, review = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1", title="测试主题"))
    assert (
        capture.ingest(UUID(grant["grant_id"]), snapshot("Synthetic health note"), grant["token"])[
            "pending"
        ]
        == 1
    )
    queue = review.queue(capture.scope)
    memory = review.approve(capture.scope, queue[0].id)
    with db_engine.connect() as c:
        title = c.scalar(
            text("SELECT title FROM memory_revision_metadata WHERE revision_id=:id"),
            {"id": memory.id},
        )
        assert title == "对话｜测试主题｜完整对话"
        assert memory.text.startswith("【记录类型】完整对话")


def current(c, scope):
    return (
        c.execute(
            text(
                "SELECT m.* FROM memories m WHERE scope_id=:s AND "
                "NOT EXISTS(SELECT 1 FROM memories n WHERE n.supersedes_id=m.id) AND "
                + visible_memory()
            ),
            {"s": scope},
        )
        .mappings()
        .all()
    )


@pytest.mark.integration
def test_incremental_capture_keeps_all_messages_and_stable_memory(
    db_engine: Engine, tmp_path: Path
):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    ident = UUID(grant["grant_id"])
    capture.ingest(ident, snapshot("last answer"), grant["token"])
    with db_engine.connect() as c:
        stable = current(c, capture.scope)[0]["memory_id"]
        title = c.scalar(
            text(
                "SELECT metadata.title FROM memory_revision_metadata metadata "
                "JOIN memories m ON m.id=metadata.revision_id WHERE m.memory_id=:id "
                "ORDER BY m.revision DESC LIMIT 1"
            ),
            {"id": stable},
        )
        assert title.endswith("｜完整对话")
    earlier = snapshot("first question")
    earlier.messages[0].id = "earlier"
    earlier.message_order = ["earlier", "msg-1"]
    capture.ingest(ident, earlier, grant["token"])
    with db_engine.connect() as c:
        rows = current(c, capture.scope)
        assert len(rows) == 1 and rows[0]["memory_id"] == stable
        assert rows[0]["text"].index("first question") < rows[0]["text"].index("last answer")
    capture.manage("revoke", CaptureInput(id=ident))
    again = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    capture.ingest(UUID(again["grant_id"]), earlier, again["token"])
    with db_engine.connect() as c:
        rows = current(c, capture.scope)
        assert len(rows) == 1 and rows[0]["memory_id"] == stable
        assert rows[0]["revision"] == 2


@pytest.mark.integration
def test_new_sensitive_turn_cannot_bypass_review_with_owner_grant(
    db_engine: Engine, tmp_path: Path
):
    capture, review = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    ident = UUID(grant["grant_id"])
    capture.ingest(ident, snapshot("first geometry"), grant["token"])
    added = snapshot("Synthetic health note")
    added.messages[0].id = "sensitive-turn"
    assert capture.ingest(ident, added, grant["token"])["pending"] == 1
    more = snapshot("last triangle")
    more.messages[0].id = "last-turn"
    assert capture.ingest(ident, more, grant["token"])["pending"] == 1
    queue = review.queue(capture.scope)
    assert len(queue) == 1 and all(
        v in queue[0].text for v in ("first geometry", "Synthetic health note", "last triangle")
    )
    with db_engine.connect() as c:
        assert "Synthetic health" not in current(c, capture.scope)[0]["text"]
    approved = review.approve(capture.scope, queue[0].id)
    with db_engine.connect() as c:
        rows = current(c, capture.scope)
        assert len(rows) == 1 and rows[0]["memory_id"] == approved.memory_id
        assert "last triangle" in rows[0]["text"]


@pytest.mark.integration
def test_million_character_body_edit_preview_fetch_export_and_tail_search(
    db_engine: Engine, tmp_path: Path
):
    capture, _ = service(db_engine, tmp_path)
    body = "几何笔记。" * 210000 + " UniqueTailGeometry"
    assert len(body) > 1_000_000
    preview = preview_content("long.md", body)
    assert len(preview.normalized_text) == len(body)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    outcome = capture.ingest(UUID(grant["grant_id"]), snapshot(body), grant["token"])
    assert outcome["changed"] == 1 and outcome["pending"] == 0
    with db_engine.connect() as c:
        record = dict(current(c, capture.scope)[0])
        assert body in record["text"] and body in memory_markdown(record)
    edit_memory(
        db_engine,
        capture.actor,
        capture.scope,
        record["memory_id"],
        1,
        record["text"] + "\nSecondTailGeometry",
        uuid4(),
    )
    from shiros.identity import LocalIdentity

    with db_engine.connect() as c:
        principal = c.scalar(
            text("SELECT principal FROM local_identities WHERE id=:id"), {"id": capture.actor}
        )
    app = create_workbench(
        db_engine,
        LocalIdentity(db_engine, lambda: principal),
        capture.scope,
        token="synthetic-long",
    )
    with TestClient(
        app, base_url="http://127.0.0.1:8001", headers={"X-Shiros-Token": "synthetic-long"}
    ) as client:
        listed = client.post(
            "/ui-api/memories", json={"query": "SecondTailGeometry", "mode": "keyword"}
        )
        assert listed.status_code == 200 and len(listed.json()["items"]) == 1
        item = listed.json()["items"][0]
        assert item["truncated"] and len(item["text"]) == 2000
        fetched = client.post("/ui-api/memory", json={"id": str(record["memory_id"])})
        assert fetched.json()["memory"]["text"].endswith("SecondTailGeometry")
        # The old request/body/preview caps must also allow a long manual input.
        previewed = client.post(
            "/ui-api/preview", json={"kind": "markdown", "title": "long", "text": body}
        )
        assert previewed.status_code == 200


@pytest.mark.integration
def test_legacy_parts_merge_without_erasing_originals_or_self_approving(
    db_engine: Engine, tmp_path: Path
):
    capture, review = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    old_ids = []
    for n, value in enumerate(("Earlier geometry", "Synthetic health note")):
        message_id = f"old-{n}"
        header = f"【角色】user\n【分段】1\n\n{value}"
        result = submit(
            db_engine,
            capture.actor,
            capture.scope,
            "Legacy",
            header,
            uuid4(),
            capture_grant=UUID(grant["grant_id"]),
        )
        candidate = UUID(result["candidate_id"])
        with db_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO capture_entries(id,grant_id,scope_id,message_id,part,"
                    "content_hash,candidate_id) VALUES(:id,:g,:s,:m,0,:hash,:candidate)"
                ),
                {
                    "id": uuid4(),
                    "g": UUID(grant["grant_id"]),
                    "s": capture.scope,
                    "m": message_id,
                    "hash": "old",
                    "candidate": candidate,
                },
            )
        old_ids.append(candidate)
    result = capture.ingest(
        UUID(grant["grant_id"]),
        CaptureSnapshot(
            provider="gpt",
            conversation_id="chat-1",
            messages=[{"id": "migration", "role": "user", "text": ""}],
        ),
        grant["token"],
    )
    assert result["pending"] == 1 and result["messages"] == 2
    queue = review.queue(capture.scope)
    assert len(queue) == 1 and queue[0].id not in old_ids
    assert "Earlier geometry" in queue[0].text and "Synthetic health" in queue[0].text
    with db_engine.connect() as c:
        assert len(current(c, capture.scope)) == 1  # original approved record until review
    approved = review.approve(capture.scope, queue[0].id)
    with db_engine.connect() as c:
        rows = current(c, capture.scope)
        assert len(rows) == 1 and rows[0]["memory_id"] == approved.memory_id
        assert (
            c.scalar(
                text("SELECT count(*) FROM memory_candidates WHERE scope_id=:s"),
                {"s": capture.scope},
            )
            == 3
        )


def test_capture_body_limit_is_ten_million_characters():
    assert MAX_MEMORY_CHARS == 10_000_000
    with pytest.raises(ValueError):
        snapshot("x" * (MAX_MEMORY_CHARS + 1))


@pytest.mark.integration
@pytest.mark.parametrize("fresh_text", ["Earlier geometry", "New geometry"])
def test_fresh_bulk_consent_imports_new_copy_after_delete_without_restoring_old(
    db_engine: Engine, tmp_path: Path, fresh_text: str
):
    capture, review = service(db_engine, tmp_path)
    single = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    capture.ingest(UUID(single["grant_id"]), snapshot("Earlier geometry"), single["token"])
    with db_engine.connect() as c:
        original = current(c, capture.scope)[0]["memory_id"]
    review.hide(capture.scope, original, True)
    with pytest.raises(PermissionError, match="not_visible"):
        capture.ingest(UUID(single["grant_id"]), snapshot("New geometry"), single["token"])
    bulk = capture.manage("grant", CaptureInput(all_conversations=True))
    assert (
        capture.ingest(UUID(bulk["grant_id"]), snapshot(fresh_text), bulk["token"])["pending"] == 0
    )
    with db_engine.connect() as c:
        rows = current(c, capture.scope)
        assert len(rows) == 1 and rows[0]["memory_id"] != original
        assert rows[0]["text"].count(fresh_text) == 1
        assert c.scalar(text("SELECT count(*) FROM capture_message_archives")) >= 1
        archive = c.scalar(
            text(
                "SELECT messages FROM capture_message_archives a "
                "JOIN capture_conversations cc ON cc.id=a.conversation_id "
                "WHERE cc.scope_id=:s"
            ),
            {"s": capture.scope},
        )
        assert archive[0]["body"] == "Earlier geometry"


@pytest.mark.integration
def test_rejection_under_new_consent_is_not_reset_on_next_snapshot(
    db_engine: Engine, tmp_path: Path
):
    capture, review = service(db_engine, tmp_path)
    single = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    capture.ingest(UUID(single["grant_id"]), snapshot("Earlier geometry"), single["token"])
    bulk = capture.manage("grant", CaptureInput(all_conversations=True))
    packet = snapshot("Synthetic health note")
    capture.ingest(UUID(bulk["grant_id"]), packet, bulk["token"])
    review.reject(capture.scope, review.queue(capture.scope)[0].id)
    with pytest.raises(PermissionError, match="review_rejected"):
        capture.ingest(UUID(bulk["grant_id"]), packet, bulk["token"])
