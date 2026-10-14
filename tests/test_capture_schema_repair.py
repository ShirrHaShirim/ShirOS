import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_capture import service, snapshot

from shiros.apps.workbench import create_workbench
from shiros.capture import CaptureInput, CaptureService


@pytest.mark.integration
@pytest.mark.parametrize("already_complete", [False, True])
def test_forward_repair_preserves_both_stamped_schema_variants(db_engine: Engine, already_complete):
    spec = importlib.util.spec_from_file_location(
        "repair", Path("migrations/versions/0023_capture_schema_repair.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with db_engine.connect() as c, c.begin() as transaction:
        schema = "repair_" + uuid4().hex
        c.execute(text(f"CREATE SCHEMA {schema}"))
        c.execute(text(f"SET LOCAL search_path TO {schema}"))
        c.execute(
            text(
                "CREATE TABLE capture_entries (grant_id int, message_id text, part int, "
                "UNIQUE(grant_id,message_id,part))"
            )
        )
        c.execute(text("INSERT INTO capture_entries VALUES(1,'original',0)"))
        if already_complete:
            c.execute(
                text("ALTER TABLE capture_entries ADD archived boolean NOT NULL DEFAULT false")
            )
            c.execute(text("UPDATE capture_entries SET archived=true"))
        with Operations.context(MigrationContext.configure(c)):
            module.upgrade()
            module.upgrade()  # Idempotent even if a deployment retries the repair.
        assert c.execute(text("SELECT * FROM capture_entries")).one() == (
            1,
            "original",
            0,
            already_complete,
        )
        c.execute(text("UPDATE capture_entries SET archived=true"))
        c.execute(text("INSERT INTO capture_entries VALUES(1,'original',0,false)"))
        assert c.scalar(text("SELECT count(*) FROM capture_entries")) == 2
        transaction.rollback()


@pytest.mark.integration
@pytest.mark.parametrize(
    "sqlstate,expected",
    [(None, "capture.storage_unavailable"), ("42703", "capture.schema_not_ready")],
)
def test_receiver_returns_safe_json_for_storage_failure(
    db_engine, tmp_path, monkeypatch, sqlstate, expected
):
    capture, workflow = service(db_engine, tmp_path)
    grant = capture.manage("grant", CaptureInput(conversation_id="chat-1"))
    client = TestClient(
        create_workbench(db_engine, workflow.identity, capture.scope, token="synthetic-ui"),
        base_url="http://127.0.0.1:8001",
    )
    error = RuntimeError("private SQL and conversation content must never be returned")
    error.orig = SimpleNamespace(sqlstate=sqlstate)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(CaptureService, "ingest", fail)
    response = client.post(
        "/capture/" + grant["grant_id"],
        json=snapshot().model_dump(mode="json"),
        headers={"X-Capture-Token": grant["token"]},
    )
    assert response.status_code == 503
    assert response.json() == {"error": expected}
    monkeypatch.setattr(CaptureService, "authorize", fail)
    response = client.post(
        "/capture/" + grant["grant_id"] + "/authorize",
        json={},
        headers={"X-Capture-Token": grant["token"]},
    )
    assert response.status_code == 503
    assert response.json() == {"error": "capture.storage_unavailable"}
