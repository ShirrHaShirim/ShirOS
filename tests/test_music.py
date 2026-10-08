"""Synthetic music integration: persistence, identity, import and permission boundaries."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_review_workflow import setup

from shiros.apps.workbench import create_workbench
from shiros.domains.music import MusicRequest, MusicService, parse_douban
from shiros.memory_gateway import MemoryGateway


def service(engine: Engine, root: Path, role: str = "owner") -> MusicService:
    workflow, scope = setup(engine, root, role)
    with engine.connect() as c:
        actor = workflow.identity.current(c)
    return MusicService(engine, actor, scope)


def run(s: MusicService, action: str, **data: object) -> dict[str, object]:
    return s.run(action, MusicRequest.model_validate(data))


def test_douban_parser_requires_identity_and_preserves_unknown_rating() -> None:
    rows = parse_douban("douban_id,title,status,rating\n12345678,合成专辑,想听,\n", "csv")
    assert rows[0]["rating"] is None
    with pytest.raises(ValueError, match="music.import_row_1"):
        parse_douban('[{"title":"同名不代表同版","status":"听过"}]', "json")
    with pytest.raises(ValueError, match="music.import_row_2"):
        parse_douban("douban_id,title,status\n12,A,想听\n12,A,听过", "csv")
    with pytest.raises(ValueError, match="music.import_row_1"):
        parse_douban('[{"id":"12345678","title":"合成","status":"想听","rating":4.5}]', "json")


@pytest.mark.integration
def test_music_relations_reviews_memberships_and_history(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    person = s.run("save", MusicRequest(object={"kind": "person", "title": "合成作曲家"}))["item"]
    work = s.run(
        "save",
        MusicRequest(
            object={
                "kind": "work",
                "title": "合成交响曲",
                "relations": [{"object_id": person["id"], "role": "composer"}],
            }
        ),
    )["item"]
    recording = s.run(
        "save",
        MusicRequest(
            object={
                "kind": "recording",
                "title": "合成录音",
                "relations": [{"object_id": work["id"], "role": "work"}],
            }
        ),
    )["item"]
    child = s.run(
        "save",
        MusicRequest(
            object={
                "kind": "work",
                "title": "合成子作品",
                "relations": [{"object_id": work["id"], "role": "parent_work"}],
            }
        ),
    )["item"]
    with pytest.raises(ValueError, match="music.work_cycle"):
        s.run(
            "save",
            MusicRequest(
                id=work["id"],
                revision=work["revision"],
                object={
                    "kind": "work",
                    "title": work["title"],
                    "relations": [{"object_id": child["id"], "role": "parent_work"}],
                },
            ),
        )
    tag = s.run("taxonomy-save", MusicRequest(name="合成古典",namespace="tag"))["node"]
    item = s.run(
        "save",
        MusicRequest(
            object={
                "title": "合成发行",
                "tag_ids": [tag["id"]],
                "relations": [
                    {"object_id": work["id"], "role": "work"},
                    {"object_id": person["id"], "role": "composer"},
                ],
                "tracks": [
                    {
                        "title": "合成乐章",
                        "position": 1,
                        "relations": [
                            {"object_id": recording["id"], "role": "recording"},
                            {"object_id": work["id"], "role": "work"},
                        ],
                    }
                ],
            }
        ),
    )["item"]
    assert item["tracks"][0]["duration_seconds"] is None
    assert s.run("list", MusicRequest(query="合成作曲家"))["total"] == 1
    assert s.run("list", MusicRequest(tag_id=tag["id"]))["total"] == 1
    identifier = item["id"]
    item = s.run(
        "review",
        MusicRequest(id=identifier, revision=item["revision"], rating=5, comment="合成五星"),
    )["item"]
    item = s.run(
        "membership",
        MusicRequest(
            id=identifier, revision=item["revision"], library="featured", reason="合成精选"
        ),
    )["item"]
    item = s.run(
        "membership", MusicRequest(id=identifier, revision=item["revision"], library="frequent")
    )["item"]
    assert s.run("list", MusicRequest(view="featured"))["total"] == 1
    previous = item["revision"]
    item = s.run("review", MusicRequest(id=identifier, revision=previous, rating=3))["item"]
    assert s.run("list", MusicRequest(view="featured"))["total"] == 0
    assert s.run("list", MusicRequest(view="five-star"))["total"] == 0
    assert s.run("list", MusicRequest(view="frequent"))["total"] == 1
    assert len(item["reviews"]) == 2
    assert all(m["active"] for m in item["memberships"])
    with pytest.raises(ValueError, match="revision_conflict"):
        s.run("review", MusicRequest(id=identifier, revision=previous, rating=4))
    exported = s.run("export", MusicRequest())["tables"]
    assert len(exported["music_objects"]) == 6
    assert len(exported["music_reviews"]) == 2
    assert exported["sources"]
    with db_engine.begin() as c:
        assert (
            c.scalar(text("SELECT count(*) FROM memories WHERE scope_id=:s"), {"s": s.scope}) == 0
        )
    with pytest.raises(DBAPIError), db_engine.begin() as c:
        c.execute(text("DELETE FROM music_reviews WHERE scope_id=:s"), {"s": s.scope})


@pytest.mark.integration
def test_music_import_all_states_idempotence_and_manual_override(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    rows = [
        {
            "douban_id": str(12345678 + n),
            "title": "同名合成发行",
            "status": status,
            "rating": None if n == 0 else 5,
            "comment": "合成短评",
        }
        for n, status in enumerate(["想听", "在听", "听过"])
    ]
    r = MusicRequest(content=json.dumps(rows, ensure_ascii=False), format="json")
    assert s.run("import-preview", r)["count"] == 3
    assert s.run("list", MusicRequest())["total"] == 0
    assert s.run("import", r) == {"created": 3, "updated": 0, "unchanged": 0}
    assert s.run("import", r) == {"created": 0, "updated": 0, "unchanged": 3}
    assert s.run("list", MusicRequest())["total"] == 3
    assert s.run("stats", MusicRequest())["listening"]["events"] == 0
    item = s.run("list", MusicRequest(status="listened"))["items"][0]
    item = s.run(
        "review",
        MusicRequest(id=item["id"], revision=item["revision"], rating=2, comment="手动判断"),
    )["item"]
    rows[2]["rating"] = 4
    assert s.run("import", MusicRequest(content=json.dumps(rows), format="json"))["updated"] == 1
    item = s.run("detail", MusicRequest(id=item["id"]))["item"]
    assert item["rating"] == 2 and item["douban_rating"] == 4
    with pytest.raises(ValueError):
        s.run("import", MusicRequest(content=json.dumps(rows + [{"title": "坏行"}]), format="json"))
    assert s.run("list", MusicRequest())["total"] == 3


@pytest.mark.integration
def test_music_permission_scope_and_transaction_rollback(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    foreign = service(db_engine, tmp_path)
    obj = foreign.run("save", MusicRequest(object={"title": "外部合成"}))["item"]
    with pytest.raises(ValueError, match="music.not_found"):
        s.run("detail", MusicRequest(id=obj["id"]))
    with pytest.raises(ValueError, match="music.not_found"):
        s.run(
            "save",
            MusicRequest(
                object={"title": "应回滚", "relations": [{"object_id": obj["id"], "role": "work"}]}
            ),
        )
    assert s.run("list", MusicRequest())["total"] == 0
    for role in ["reader", "proposer", "editor", "worker"]:
        denied = service(db_engine, tmp_path, role)
        with pytest.raises(PermissionError):
            denied.run("save", MusicRequest(object={"title": "不应写入"}))
    with pytest.raises(PermissionError):
        s.run("save", MusicRequest(object={"title": "secret=synthetic-secret"}))
    assert s.run("list", MusicRequest())["total"] == 0


@pytest.mark.integration
async def test_music_listening_sources_and_mcp(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    item = s.run("save", MusicRequest(object={"title": "合成聆听专辑"}))["item"]
    identifier = item["id"]
    with pytest.raises(ValueError, match="invalid_url"):
        s.run(
            "source", MusicRequest(id=identifier, revision=item["revision"], url="javascript:bad")
        )
    item = s.run(
        "source",
        MusicRequest(
            id=identifier,
            revision=item["revision"],
            url="https://example.org/music?id=123456789",
            platform="external",
        ),
    )["item"]
    assert s.run("stats", MusicRequest())["listening"]["events"] == 0
    start = datetime(2026, 10, 8, tzinfo=UTC)
    item = s.run(
        "listen",
        MusicRequest(
            id=identifier,
            revision=item["revision"],
            started_at=start,
            source_event_id="synthetic-play",
        ),
    )["item"]
    assert s.run(
        "listen",
        MusicRequest(
            id=identifier,
            revision=item["revision"],
            started_at=start,
            source_event_id="synthetic-play",
        ),
    )["duplicate"]
    item = s.run(
        "listen",
        MusicRequest(id=identifier, revision=item["revision"], started_at=start, actual_seconds=90),
    )["item"]
    stat = s.run("stats", MusicRequest())["listening"]
    assert stat == {"events": 2, "known_duration_events": 1, "actual_seconds": 90}
    gateway = MemoryGateway(db_engine, s.actor, s.scope, "synthetic")
    assert (await gateway.music_query("list", {}))["total"] == 1
    with pytest.raises(ValueError):
        await gateway.music_query("save", {"object": {"title": "绕过只读"}})
    assert (
        await gateway.music_mutate(
            "review", {"id": str(identifier), "revision": item["revision"], "rating": 5}
        )
    )["item"]["rating"] == 5


@pytest.mark.integration
def test_music_browser_guard_and_real_api(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    app = create_workbench(db_engine, workflow.identity, scope, token="synthetic-token")
    with TestClient(app, base_url="http://127.0.0.1:8001") as client:
        assert client.post("/ui-api/music/list", json={}).status_code == 403
        client.headers["X-Shiros-Token"] = "synthetic-token"
        assert client.post("/ui-api/music/list", json={"scope": str(uuid4())}).status_code == 422
        assert (
            client.post(
                "/ui-api/music/save", json={"object": {"title": "合成网页专辑"}}
            ).status_code
            == 200
        )
        response = client.post("/ui-api/music/list", json={})
        assert response.status_code == 200 and response.json()["total"] == 1
        item = response.json()["items"][0]
        assert UUID(item["id"])
        assert (
            client.post(
                "/ui-api/music/list", json={}, headers={"Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
