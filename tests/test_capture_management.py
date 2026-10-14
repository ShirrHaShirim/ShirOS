from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, text
from test_capture import service, snapshot

from shiros.capture import CaptureInput


@pytest.mark.integration
def test_revoked_grant_can_be_removed_and_restored_without_losing_memory(
    db_engine: Engine, tmp_path: Path
):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1", include_files=True))
    ident = UUID(grant["grant_id"])
    capture.ingest(
        ident,
        snapshot(attachments=[{"id": "f", "filename": "geometry.txt", "data": "eA=="}]),
        grant["token"],
    )
    with pytest.raises(ValueError, match="revoke_first"):
        capture.manage("delete", CaptureInput(id=ident))
    capture.manage("revoke", CaptureInput(id=ident))
    capture.manage("delete", CaptureInput(id=ident))
    assert capture.manage("list", CaptureInput())["items"] == []
    deleted = capture.manage("list", CaptureInput(include_deleted=True))["items"]
    assert len(deleted) == 1 and deleted[0]["deleted"] and not deleted[0]["enabled"]
    with db_engine.connect() as c:
        memory_id = c.scalar(
            text("SELECT memory_id FROM memories WHERE scope_id=:s"), {"s": capture.scope}
        )
    assert len(capture.manage("links", CaptureInput(memory_id=memory_id))["items"]) == 1
    capture.manage("restore", CaptureInput(id=ident))
    restored = capture.manage("list", CaptureInput())["items"]
    assert len(restored) == 1 and not restored[0]["deleted"] and not restored[0]["enabled"]
    with pytest.raises(PermissionError, match="not_authorized"):
        capture.ingest(ident, snapshot(), grant["token"])


@pytest.mark.integration
def test_delete_cannot_cross_scope_and_readers_cannot_remove_grants(
    db_engine: Engine, tmp_path: Path
):
    capture, _ = service(db_engine, tmp_path)
    other, _ = service(db_engine, tmp_path)
    reader, _ = service(db_engine, tmp_path, "reader")
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    ident = UUID(grant["grant_id"])
    capture.manage("revoke", CaptureInput(id=ident))
    with pytest.raises(ValueError, match="grant_not_found"):
        other.manage("delete", CaptureInput(id=ident))
    with pytest.raises(PermissionError):
        reader.manage("delete", CaptureInput(id=ident))


@pytest.mark.integration
def test_removed_bulk_grant_retains_child_links(db_engine: Engine, tmp_path: Path):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(all_conversations=True))
    ident = UUID(grant["grant_id"])
    capture.ingest(ident, snapshot(), grant["token"])
    capture.manage("revoke", CaptureInput(id=ident))
    capture.manage("delete", CaptureInput(id=ident))
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(*) FROM capture_entries WHERE scope_id=:s"), {"s": capture.scope}
            )
            == 1
        )
    assert capture.manage("list", CaptureInput())["items"] == []


@pytest.mark.integration
def test_reissue_keeps_scope_and_memory_and_invalidates_previous_token(db_engine, tmp_path):
    capture, _ = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    ident = UUID(grant["grant_id"])
    capture.ingest(ident, snapshot(), grant["token"])
    with db_engine.connect() as c:
        stable = c.scalar(
            text("SELECT memory_id FROM memories WHERE scope_id=:s"), {"s": capture.scope}
        )
    renewed = capture.manage(
        "reissue", CaptureInput(id=ident, all_conversations=True, include_files=True)
    )
    assert renewed["grant_id"] == grant["grant_id"] and renewed["token"] != grant["token"]
    assert not renewed["all_conversations"] and not renewed["include_files"]
    with pytest.raises(PermissionError, match="not_authorized"):
        capture.ingest(ident, snapshot(), grant["token"])
    capture.ingest(ident, snapshot("Synthetic updated geometry"), renewed["token"])
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
                text(
                    "SELECT memory_id FROM memories WHERE scope_id=:s "
                    "ORDER BY revision DESC LIMIT 1"
                ),
                {"s": capture.scope},
            )
            == stable
        )
        assert (
            c.scalar(
                text("SELECT count(*) FROM capture_grants WHERE scope_id=:s"), {"s": capture.scope}
            )
            == 1
        )
    capture.manage("revoke", CaptureInput(id=ident))
    with pytest.raises(ValueError, match="grant_not_found"):
        capture.manage("reissue", CaptureInput(id=ident))


@pytest.mark.integration
def test_reissue_requires_admin_and_cannot_cross_scope_or_rotate_child(db_engine, tmp_path):
    capture, _ = service(db_engine, tmp_path)
    other, _ = service(db_engine, tmp_path)
    reader, _ = service(db_engine, tmp_path, "reader")
    grant = capture.manage("grant", CaptureInput(all_conversations=True))
    ident = UUID(grant["grant_id"])
    child = capture.ingest(ident, snapshot(), grant["token"])
    with pytest.raises(ValueError, match="grant_not_found"):
        other.manage("reissue", CaptureInput(id=ident))
    with pytest.raises(PermissionError):
        reader.manage("reissue", CaptureInput(id=ident))
    with pytest.raises(ValueError, match="grant_not_found"):
        capture.manage("reissue", CaptureInput(id=UUID(child["grant_id"])))
    renewed = capture.manage("reissue", CaptureInput(id=ident))
    assert renewed["all_conversations"]
    assert capture.ingest(ident, snapshot(), renewed["token"])["unchanged"] == 1
