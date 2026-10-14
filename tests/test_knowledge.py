"""Real PostgreSQL governance, canonical references, recovery and appearance tests."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_review_workflow import setup

from shiros.apps.workbench import create_workbench
from shiros.knowledge import SECTIONS, KnowledgeRequest, KnowledgeService
from shiros.preferences import ThemeRequest, preference
from shiros.review_workflow import ReviewWorkflow
from shiros.tags import create_tag, set_memory_tags


def actor_of(workflow: ReviewWorkflow, engine: Engine) -> UUID:
    with engine.connect() as c:
        return workflow.identity.current(c)


@pytest.mark.integration
def test_theme_survives_new_session_port_and_scope(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    client = TestClient(
        create_workbench(db_engine, workflow.identity, scope, 8001, "a" * 43),
        base_url="http://127.0.0.1:8001",
    )
    assert client.post("/ui-api/preferences/theme", json={}).status_code == 403
    result = client.post(
        "/ui-api/preferences/theme",
        json={"theme": "graphite"},
        headers={"X-Shiros-Token": "a" * 43},
    )
    assert result.json() == {"theme": "graphite", "saved": True}
    # A completely fresh browser/desktop session, token and port retains the server preference.
    reopened = TestClient(
        create_workbench(db_engine, workflow.identity, scope, 8031, "b" * 43),
        base_url="http://127.0.0.1:8031",
    )
    assert (
        reopened.post(
            "/ui-api/preferences/theme", json={}, headers={"X-Shiros-Token": "b" * 43}
        ).json()["theme"]
        == "graphite"
    )
    assert (
        reopened.post(
            "/ui-api/preferences/theme", json={"theme": "bad"}, headers={"X-Shiros-Token": "b" * 43}
        ).status_code
        == 422
    )
    assert reopened.post(
        "/ui-api/preferences/theme", json={"theme": "white"},
        headers={"X-Shiros-Token": "b" * 43},
    ).json()["theme"] == "white"
    assert reopened.post(
        "/ui-api/preferences/theme", json={}, headers={"X-Shiros-Token": "b" * 43},
    ).json()["theme"] == "white"
    other, other_scope = setup(db_engine, tmp_path)
    assert (
        preference(db_engine, actor_of(other, db_engine), other_scope, ThemeRequest())["saved"]
        is False
    )


@pytest.mark.integration
def test_synthesis_revision_dependencies_review_and_l0(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    owner = actor_of(workflow, db_engine)
    root = create_tag(db_engine, owner, scope, "Synthetic domain")
    memory = workflow.approve(
        scope,
        workflow.stage(
            scope, workflow.preview_text(scope, "note.md", "Synthetic detailed observation").id
        ).id,
    )
    set_memory_tags(db_engine, owner, scope, memory.memory_id, [UUID(root["id"])], add_only=True)
    service = KnowledgeService(db_engine, owner, scope)
    request = KnowledgeRequest(domain_id=UUID(root["id"]))
    service.run("domain-enable", request, trusted_review=True)
    assert service.run("search", request)["items"][0]["id"] == memory.memory_id
    job_request = request.model_copy(update={"request_id": uuid4()})
    job = service.run("enqueue", job_request)
    assert service.run("enqueue", job_request)["id"] == job["id"]
    with pytest.raises(ValueError, match="request_conflict"):
        service.run("enqueue", job_request.model_copy(update={"reason": "weekly"}))
    identifier = UUID(job["id"])
    service.run("claim", KnowledgeRequest(id=identifier))
    service.run("checkpoint", KnowledgeRequest(id=identifier, progress={"cursor": "page-one"}))
    body = {key: "Synthetic coherent synthesis" for key in SECTIONS}
    service.run("draft", KnowledgeRequest(id=identifier, body=body))
    with db_engine.connect() as c:
        assert (
            c.scalar(text("SELECT checkpoint FROM knowledge_heads WHERE scope_id=:s"), {"s": scope})
            is None
        )
    with pytest.raises(PermissionError, match="human_review_required"):
        service.run("publish", KnowledgeRequest(id=identifier))
    result = service.run("publish", KnowledgeRequest(id=identifier), trusted_review=True)
    assert result["revision"] == 1
    assert (
        service.run(
            "enqueue",
            KnowledgeRequest(domain_id=UUID(root["id"]), request_id=uuid4(), reason="daily"),
        )["state"]
        == "unchanged"
    )
    layers = service.run("layers", KnowledgeRequest())["items"]
    assert layers[0]["versions"][0]["dependencies"][0]["id"] == str(memory.memory_id)
    assert layers[0]["versions"][0]["evidence_level"] == "model_guess"
    weekly = service.run(
        "enqueue", KnowledgeRequest(domain_id=UUID(root["id"]), request_id=uuid4(), reason="weekly")
    )
    weekly_id = UUID(weekly["id"])
    service.run("claim", KnowledgeRequest(id=weekly_id))
    service.run("draft", KnowledgeRequest(id=weekly_id, body=body))
    reviewed = service.run(
        "publish", KnowledgeRequest(id=weekly_id, expected_revision=1), trusted_review=True
    )
    assert reviewed["revision"] == 1 and not reviewed["checkpoint_advanced"]
    global_job = service.run("enqueue", KnowledgeRequest(layer="L0", request_id=uuid4()))
    global_id = UUID(global_job["id"])
    service.run("claim", KnowledgeRequest(id=global_id))
    service.run("draft", KnowledgeRequest(id=global_id, body=body))
    with pytest.raises(PermissionError, match="l0_confirmation_required"):
        service.run("publish", KnowledgeRequest(id=global_id, layer="L0"), trusted_review=True)
    service.run(
        "publish", KnowledgeRequest(id=global_id, layer="L0", confirm=True), trusted_review=True
    )
    workflow.hide(scope, memory.memory_id, True)
    for layer in service.run("layers", KnowledgeRequest())["items"]:
        assert layer["stale"] and layer["versions"][0]["body"] is None
    assert service.run("search", request)["total"] == 0
    changed = service.run(
        "enqueue", KnowledgeRequest(domain_id=UUID(root["id"]), request_id=uuid4())
    )
    assert changed["state"] == "queued"
    replacement = UUID(changed["id"])
    service.run("claim", KnowledgeRequest(id=replacement))
    service.run(
        "draft", KnowledgeRequest(id=replacement, body={k: "Updated synthesis" for k in SECTIONS})
    )
    service.run(
        "publish", KnowledgeRequest(id=replacement, expected_revision=1), trusted_review=True
    )
    updated = next(
        x for x in service.run("layers", KnowledgeRequest())["items"] if x["layer"] == "L1"
    )
    assert not updated["stale"] and updated["versions"][0]["body"] is not None
    assert (
        updated["versions"][1]["body"] is None
    )  # Hidden evidence cannot leak through older syntheses.
    # No maintenance operation changed the original L2 ID or revision.
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(*) FROM memories WHERE memory_id=:id"), {"id": memory.memory_id}
            )
            == 1
        )


@pytest.mark.integration
def test_five_clients_leases_recovery_and_scope(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    owner = actor_of(workflow, db_engine)
    domain = UUID(create_tag(db_engine, owner, scope, "Synthetic maintenance")["id"])
    service = KnowledgeService(db_engine, owner, scope)
    service.run("domain-enable", KnowledgeRequest(domain_id=domain), trusted_review=True)
    clients = []
    with db_engine.begin() as c:
        for name in ["chatgpt", "work", "codex", "deepseek-harness", "opencode"]:
            actor = uuid4()
            clients.append(KnowledgeService(db_engine, actor, scope))
            c.execute(
                text("INSERT INTO local_identities(id,principal) VALUES (:a,:p)"),
                {"a": actor, "p": "synthetic:" + name + str(actor)},
            )
            c.execute(
                text("INSERT INTO local_grants VALUES (:a,:s,'proposer')"), {"a": actor, "s": scope}
            )
    job = clients[0].run("enqueue", KnowledgeRequest(domain_id=domain, request_id=uuid4()))
    id = UUID(job["id"])
    clients[0].run("claim", KnowledgeRequest(id=id))
    clients[0].run("checkpoint", KnowledgeRequest(id=id, progress={"cursor": "saved-page"}))
    with pytest.raises(ValueError, match="job_busy"):
        clients[1].run("claim", KnowledgeRequest(id=id))
    with db_engine.begin() as c:
        c.execute(
            text("UPDATE knowledge_jobs SET lease_until=now()-interval '1 second' WHERE id=:id"),
            {"id": id},
        )
    assert clients[1].run("claim", KnowledgeRequest(id=id))["progress"]["cursor"] == "saved-page"
    for client in clients:
        assert client.run("evidence", KnowledgeRequest(id=id))["stale"] is False
        with pytest.raises(PermissionError):
            client.run("publish", KnowledgeRequest(id=id), trusted_review=True)
    clients[1].run(
        "draft", KnowledgeRequest(id=id, body={k: "Synthetic synthesis" for k in SECTIONS})
    )
    service.run("publish", KnowledgeRequest(id=id), trusted_review=True)
    from concurrent.futures import ThreadPoolExecutor

    race = service.run(
        "enqueue", KnowledgeRequest(domain_id=domain, request_id=uuid4(), reason="weekly")
    )

    def claim(client: KnowledgeService) -> str:
        try:
            return str(client.run("claim", KnowledgeRequest(id=UUID(race["id"])))["state"])
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, clients[:2]))
    assert sorted(outcomes) == ["claimed", "knowledge.job_busy"]
    other, other_scope = setup(db_engine, tmp_path)
    with pytest.raises(ValueError, match="job_not_found"):
        KnowledgeService(db_engine, actor_of(other, db_engine), other_scope).run(
            "evidence", KnowledgeRequest(id=id)
        )


@pytest.mark.integration
def test_canonical_music_is_not_copied_or_inferred_from_memory_genre(
    db_engine: Engine, tmp_path: Path
) -> None:
    from shiros.domains.music import MusicRequest, MusicService

    workflow, scope = setup(db_engine, tmp_path)
    owner = actor_of(workflow, db_engine)
    service = KnowledgeService(db_engine, owner, scope)
    root = UUID(create_tag(db_engine, owner, scope, "Synthetic music domain")["id"])
    music = MusicService(db_engine, owner, scope)
    recording = music.run(
        "save",
        MusicRequest.model_validate(
            {"object": {"title": "Synthetic Recording", "kind": "recording"}}
        ),
    )["item"]
    music.run(
        "save",
        MusicRequest.model_validate(
            {
                "object": {
                    "title": "Synthetic Album",
                    "kind": "release",
                    "relations": [{"object_id": str(recording["id"]), "role": "recording"}],
                }
            }
        ),
    )
    service.run("domain-enable", KnowledgeRequest(domain_id=root, music=True), trusted_review=True)
    items = service.run("search", KnowledgeRequest(domain_id=root))["items"]
    assert len(items) == 2
    assert all(item["owner"] == "music" for item in items)
    assert all(item["locator"].startswith("shiros://music/") for item in items)
    release = next(item for item in items if item["kind"] == "release")
    detail = service.run("object", KnowledgeRequest(id=release["id"], domain_id=root))["object"][
        "detail"
    ]
    assert not detail["genres"] and not detail["tags"]
    with db_engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM memories WHERE scope_id=:s"), {"s": scope}) == 0
