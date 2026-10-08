"""Browser permission boundary and synthetic human-review HTTP workflow."""

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_review_workflow import setup

from shiros.apps.workbench import create_workbench


@pytest.mark.integration
def test_browser_review_roundtrip(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    token = "synthetic-test-capability"
    app = create_workbench(db_engine, workflow.identity, scope, token=token)
    with TestClient(app, base_url="http://127.0.0.1:8001") as client:
        assert client.post("/ui-api/session", json={}).status_code == 403
        client.headers["X-Shiros-Token"] = token
        landing = client.get("/")
        assert landing.status_code == 200 and token not in landing.text
        assert landing.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in landing.headers["content-security-policy"]
        assert client.post("/ui-api/session", json={}).json()["stats"]["memories"] == 0
        assert client.post("/ui-api/session", json={"actor": str(uuid4())}).status_code == 422
        for headers in (
            {"Origin": "https://evil.invalid"},
            {"Host": "evil.invalid:8001"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            assert client.post("/ui-api/session", json={}, headers=headers).status_code == 403
        preview = client.post(
            "/ui-api/preview",
            json={
                "title": "Synthetic browser note",
                "text": "Synthetic browser health algebra observation",
            },
        ).json()
        assert preview["privacy"]["persistence_allowed"]
        assert client.post("/ui-api/memories", json={}).json()["items"] == []
        candidate = client.post("/ui-api/stage", json={"id": preview["id"]}).json()
        assert candidate["status"] == "pending"
        assert len(client.post("/ui-api/queue", json={}).json()["items"]) == 1
        assert (
            client.post(
                "/ui-api/edit", json={"id": candidate["id"], "text": "password=synthetic-secret"}
            ).status_code
            == 403
        )
        assert (
            client.post("/ui-api/approve", json={"id": candidate["id"], "revision": 99}).status_code
            == 400
        )
        result = client.post(
            "/ui-api/approve", json={"id": candidate["id"], "revision": candidate["revision"]}
        )
        assert result.status_code == 200, result.text
        memory = result.json()
        assert len(client.post("/ui-api/memories", json={"query": "algebra"}).json()["items"]) == 1
        assert (
            client.post(
                "/ui-api/revoke", json={"id": candidate["source_id"], "kind": "intake_source"}
            ).status_code
            == 200
        )
        assert client.post("/ui-api/memories", json={}).json()["items"] == []
        assert (
            client.post("/ui-api/memory", json={"id": memory["memory_id"]}).json()["memory"] is None
        )
        assert (
            client.post(
                "/ui-api/session",
                content="x" * 400001,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        rejected = client.post(
            "/ui-api/preview", json={"title": "secret.txt", "text": "password=synthetic-private"}
        ).json()
        assert client.post("/ui-api/stage", json={"id": rejected["id"]}).status_code == 403


@pytest.mark.integration
def test_browser_reader_cannot_write(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path, "reader")
    app = create_workbench(db_engine, workflow.identity, scope, token="synthetic")
    with TestClient(
        app, base_url="http://127.0.0.1:8001", headers={"X-Shiros-Token": "synthetic"}
    ) as client:
        assert client.post("/ui-api/memories", json={}).status_code == 200
        assert client.post("/ui-api/preview", json={"text": "Synthetic"}).status_code == 403
        assert (
            client.post("/ui-api/approve", json={"id": str(uuid4()), "revision": 1}).status_code
            == 403
        )
        assert client.post("/ui-api/tag-create", json={"name": "Synthetic"}).status_code == 403


@pytest.mark.integration
def test_browser_tag_library_roundtrip(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    candidate = workflow.stage(
        scope, workflow.preview_text(scope, "Example.txt", "Synthetic library note").id
    )
    memory = workflow.approve(scope, candidate.id)
    app = create_workbench(db_engine, workflow.identity, scope, token="synthetic")
    with TestClient(
        app, base_url="http://127.0.0.1:8001", headers={"X-Shiros-Token": "synthetic"}
    ) as client:
        parent = client.post("/ui-api/tag-create", json={"name": "Projects"})
        assert parent.status_code == 200, parent.text
        child = client.post(
            "/ui-api/tag-create", json={"name": "ShirOS", "parent_id": parent.json()["id"]}
        ).json()
        assigned = client.post(
            "/ui-api/set-memory-tags",
            json={"id": str(memory.memory_id), "tag_ids": [child["id"], parent.json()["id"]]},
        )
        assert assigned.status_code == 200, assigned.text
        assert len(assigned.json()["items"]) == 2
        assert len(client.post("/ui-api/tags", json={}).json()["items"]) == 2
        result = client.post("/ui-api/library", json={"tag_id": child["id"], "limit": 1}).json()
        assert result["total"] == 1
        assert result["items"][0]["memory_id"] == str(memory.memory_id)
        assert client.post("/ui-api/library", json={"offset": 1}).json()["items"] == []
        detail = client.post("/ui-api/memory", json={"id": str(memory.memory_id)}).json()
        assert len(detail["memory"]["tags"]) == 2
        cycle = client.post(
            "/ui-api/tag-update",
            json={"id": parent.json()["id"], "name": "Projects", "parent_id": child["id"]},
        )
        assert cycle.status_code == 400
        assert (
            client.post(
                "/ui-api/set-memory-tags", json={"id": str(memory.memory_id), "tag_ids": []}
            ).status_code
            == 200
        )
        assert client.post("/ui-api/library", json={"tag_id": child["id"]}).json()["total"] == 0
