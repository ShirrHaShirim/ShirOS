from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from test_capture import service, snapshot

from shiros.capture import CaptureInput
from shiros.capture_conversations import PRIVACY_OMITTED


@pytest.mark.integration
@pytest.mark.parametrize("blocked", ["password=synthetic-no-store", "私下说，这句话不要记录"])
def test_denied_turn_has_only_placeholder_and_does_not_drop_whole_chat(
    db_engine: Engine, tmp_path: Path, blocked: str
):
    capture, review = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1", include_files=True))
    ident = UUID(grant["grant_id"])
    packet = snapshot("Earlier geometry")
    forbidden = snapshot(
        blocked, [{"id": "secret-file", "filename": "original.txt", "data": "eA=="}]
    )
    forbidden.messages[0].id = "blocked-turn"
    packet.messages += forbidden.messages
    last = snapshot("Later triangle")
    last.messages[0].id = "later-turn"
    packet.messages += last.messages
    packet.message_order = ["msg-1", "blocked-turn", "later-turn"]
    outcome = capture.ingest(ident, packet, grant["token"])
    assert outcome["pending"] == 1 and outcome["privacy_omitted_messages"] == 1
    queue = review.queue(capture.scope)
    assert len(queue) == 1 and PRIVACY_OMITTED in queue[0].text
    assert (
        queue[0].text.index("Earlier geometry")
        < queue[0].text.index(PRIVACY_OMITTED)
        < queue[0].text.index("Later triangle")
    )
    assert blocked not in queue[0].text
    with db_engine.connect() as c:
        rows = (
            c.execute(
                text(
                    "SELECT m.body,m.attachments FROM capture_messages m "
                    "JOIN capture_conversations cc ON cc.id=m.conversation_id WHERE cc.scope_id=:s"
                ),
                {"s": capture.scope},
            )
            .mappings()
            .all()
        )
        assert len(rows) == 3 and all(blocked not in r["body"] for r in rows)
        assert next(r for r in rows if r["body"] == PRIVACY_OMITTED)["attachments"] == []
        assert (
            c.scalar(
                text("SELECT count(*) FROM library_files WHERE scope_id=:s"), {"s": capture.scope}
            )
            == 0
        )
        assert (
            c.scalar(text("SELECT count(*) FROM memories WHERE scope_id=:s"), {"s": capture.scope})
            == 0
        )
    approved = review.approve(capture.scope, queue[0].id)
    assert blocked not in approved.text
    repeat = capture.ingest(ident, packet, grant["token"])
    assert repeat["unchanged"] == 1 and repeat["privacy_omitted_messages"] == 1


@pytest.mark.integration
def test_denied_title_and_attachment_metadata_are_not_persisted(db_engine: Engine, tmp_path: Path):
    capture, review = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1", include_files=True))
    packet = snapshot("Safe geometry", [{"id": "file", "filename": "密码.txt", "data": "eA=="}])
    packet.conversation_title = "不要记录这个标题"
    outcome = capture.ingest(UUID(grant["grant_id"]), packet, grant["token"])
    assert outcome["privacy_omitted_title"] and outcome["privacy_omitted_messages"] == 1
    assert outcome["pending"] == 1
    item = review.queue(capture.scope)[0]
    assert "不要记录" not in item.text and "密码.txt" not in item.text
    assert "原始标题" in item.text and PRIVACY_OMITTED in item.text


@pytest.mark.integration
def test_new_blocked_turn_keeps_previous_formal_memory_until_review(
    db_engine: Engine, tmp_path: Path
):
    capture, review = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    ident = UUID(grant["grant_id"])
    capture.ingest(ident, snapshot("Old geometry"), grant["token"])
    packet = snapshot("token=synthetic-never-store")
    packet.messages[0].id = "blocked-turn"
    assert capture.ingest(ident, packet, grant["token"])["pending"] == 1
    with db_engine.connect() as c:
        rows = (
            c.execute(
                text("SELECT text,memory_id FROM memories WHERE scope_id=:s"), {"s": capture.scope}
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1 and "Old geometry" in rows[0]["text"]
        stable = rows[0]["memory_id"]
    approved = review.approve(capture.scope, review.queue(capture.scope)[0].id)
    assert (
        approved.memory_id == stable
        and "Old geometry" in approved.text
        and PRIVACY_OMITTED in approved.text
    )
