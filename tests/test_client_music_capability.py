import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from test_memory_gateway import setup

from shiros.identity import DatabasePermissions
from shiros.memory_bridge import ClientProfile, music_access
from shiros.memory_gateway import MemoryGateway


def client(engine, scope, name="chatgpt"):
    actor = uuid4()
    with engine.begin() as c:
        c.execute(
            text("INSERT INTO local_identities(id,principal) VALUES (:a,:p)"),
            {"a": actor, "p": f"mcp:{name}:{actor}"},
        )
        c.execute(
            text("INSERT INTO local_grants VALUES (:a,:s,'editor')"), {"a": actor, "s": scope}
        )
    return ClientProfile(client=name, actor=actor, scope=scope, token=SecretStr("x" * 43))


@pytest.mark.integration
async def test_explicit_music_access_imports_without_owner_or_review_rights(
    db_engine: Engine, tmp_path: Path
):
    workflow, _, scope = setup(db_engine, tmp_path)
    profile = client(db_engine, scope)
    gateway = MemoryGateway(db_engine, profile.actor, scope, "chatgpt")
    payload = {
        "content": json.dumps(
            [{"douban_id": "12345678", "title": "Synthetic release", "status": "听过", "rating": 5}]
        ),
        "format": "json",
    }
    assert gateway.status()["music_read"] and not gateway.status()["music_write"]
    with pytest.raises(PermissionError):
        await gateway.music_mutate("import-preview", payload)
    music_access(db_engine, workflow.identity, profile, enabled=True)
    assert gateway.status()["music_write"]
    with db_engine.connect() as c:
        permissions = DatabasePermissions(c)
        assert not any(
            permissions.has(profile.actor, scope, action)
            for action in ("admin", "review", "approve", "write")
        )
        assert permissions.has(profile.actor, scope, "memory_edit")
    assert (await gateway.music_mutate("import-preview", payload))["count"] == 1
    assert (await gateway.music_query("list", {}))["total"] == 0
    assert (await gateway.music_mutate("import", payload))["created"] == 1
    assert (await gateway.music_mutate("import", payload))["unchanged"] == 1
    item = (await gateway.music_query("list", {}))["items"][0]
    updated = (
        await gateway.music_mutate(
            "review-history-add",
            {
                "id": str(item["id"]),
                "revision": item["revision"],
                "historical_date": "2020-01-01",
                "rating": 4,
            },
        )
    )["item"]
    assert updated["reviews"]
    music_access(db_engine, workflow.identity, profile, enabled=False)
    assert not gateway.status()["music_write"]
    with pytest.raises(PermissionError):
        await gateway.music_mutate("import", payload)


@pytest.mark.integration
def test_music_grant_is_owner_scoped_and_revocation_cascades(db_engine: Engine, tmp_path: Path):
    workflow, _, scope = setup(db_engine, tmp_path)
    other, _, other_scope = setup(db_engine, tmp_path)
    profile = client(db_engine, scope)
    with pytest.raises(PermissionError):
        music_access(db_engine, other.identity, profile, enabled=True)
    forged = profile.model_copy(update={"client": "codex"})
    with pytest.raises(PermissionError, match="profile.invalid_identity"):
        music_access(db_engine, workflow.identity, forged, enabled=True)
    music_access(db_engine, workflow.identity, profile, enabled=True)
    with db_engine.connect() as c:
        assert not DatabasePermissions(c).has(profile.actor, other_scope, "music_write")
    with db_engine.begin() as c:
        c.execute(
            text("DELETE FROM local_grants WHERE actor_id=:a AND scope_id=:s"),
            {"a": profile.actor, "s": scope},
        )
        assert not DatabasePermissions(c).has(profile.actor, scope, "music_write")
        assert (
            c.scalar(
                text("SELECT count(*) FROM client_capability_grants WHERE actor_id=:a"),
                {"a": profile.actor},
            )
            == 0
        )
    with pytest.raises(PermissionError, match="profile.revoked"):
        music_access(db_engine, workflow.identity, profile, enabled=True)
