"""Memory gateway integration tests using only synthetic database rows."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from shiros.core.permissions import AccessRequest
from shiros.identity import LocalIdentity
from shiros.intake import MemoryCandidate
from shiros.memory_gateway import MemoryGateway, ReaderPermissions
from shiros.review_workflow import ReviewWorkflow


def setup(engine: Engine, root: Path) -> tuple[ReviewWorkflow, UUID, UUID]:
    actor, scope = uuid4(), uuid4()
    principal = "synthetic:" + str(actor)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO local_identities(id,principal) VALUES (:id,:p)"),
            {"id": actor, "p": principal},
        )
        connection.execute(
            text("INSERT INTO local_grants VALUES (:a,:s,'owner')"),
            {"a": actor, "s": scope},
        )
    return ReviewWorkflow(engine, LocalIdentity(engine, lambda: principal), root), actor, scope


async def stage(
    workflow: ReviewWorkflow, root: Path, scope: UUID, name: str, body: str
) -> MemoryCandidate:
    (root / name).write_text(body, encoding="utf-8")
    return await asyncio.to_thread(workflow.stage, scope, workflow.preview(scope, name).id)


@pytest.mark.integration
async def test_gateway_exposes_only_approved_memory_in_its_scope(
    db_engine: Engine, tmp_path: Path
) -> None:
    workflow, actor, scope = setup(db_engine, tmp_path)
    other_scope = uuid4()
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO local_grants VALUES (:a,:s,'owner')"),
            {"a": actor, "s": other_scope},
        )

    pending = await stage(workflow, tmp_path, scope, "pending.txt", "Synthetic health comet fact")
    foreign = await stage(
        workflow, tmp_path, other_scope, "foreign.txt", "Synthetic foreign comet fact"
    )
    foreign_memory = await asyncio.to_thread(workflow.approve, other_scope, foreign.id)
    gateway = MemoryGateway(db_engine, actor, scope, "synthetic-test")

    assert gateway.status()["read_only"] is False
    assert gateway.status()["tag_assignment"] is True
    assert (await gateway.search("comet"))["results"] == []
    assert (await gateway.fetch(foreign_memory.memory_id))["memory"] is None
    pending_search = await gateway.search("pending")
    assert pending_search["results"] == []

    approved = await stage(
        workflow, tmp_path, scope, "approved.txt", "Synthetic approved comet fact"
    )
    memory = await asyncio.to_thread(workflow.approve, scope, approved.id)
    found = await gateway.search("approved comet")
    assert [item["id"] for item in found["results"]] == [str(memory.memory_id)]
    assert found["content_is_untrusted_data"] is True

    fetched = await gateway.fetch(memory.memory_id)
    assert fetched["memory"]["memory_id"] == str(memory.memory_id)
    context = await gateway.context("approved comet fact")
    bundle = context["bundle"]
    assert any("Synthetic approved comet fact" in str(item) for item in bundle["items"])
    assert all(
        str(foreign_memory.memory_id) != str(item.get("memory_id")) for item in bundle["items"]
    )

    assert pending.status == "pending"
    created_tag = await gateway.create_tag("Synthetic classification")
    tag_id = UUID(created_tag["tag"]["id"])
    assigned = await gateway.add_memory_tags(memory.memory_id, [tag_id])
    assert len(assigned["tags"]) == 1
    assert len((await gateway.list_tags())["items"]) == 1


@pytest.mark.integration
async def test_gateway_enforces_persisted_reader_grant_and_live_revocation(
    db_engine: Engine, tmp_path: Path
) -> None:
    workflow, owner, scope = setup(db_engine, tmp_path)
    reader = uuid4()
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO local_identities(id,principal) VALUES (:id,:p)"),
            {"id": reader, "p": "synthetic:" + str(reader)},
        )
        connection.execute(
            text("INSERT INTO local_grants VALUES (:a,:s,'reader')"),
            {"a": reader, "s": scope},
        )
    permissions = ReaderPermissions(db_engine, reader, scope)
    assert permissions.allows(AccessRequest(actor_id=reader, resource_id=scope, action="read"))
    assert not permissions.allows(
        AccessRequest(actor_id=reader, resource_id=scope, action="persist")
    )
    assert not permissions.allows(
        AccessRequest(actor_id=reader, resource_id=scope, action="review")
    )
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT role FROM local_grants WHERE actor_id=:a AND scope_id=:s"),
                {"a": reader, "s": scope},
            )
            == "reader"
        )

    gateway = MemoryGateway(db_engine, reader, scope, "synthetic-reader")
    hidden_candidate = await stage(
        workflow, tmp_path, scope, "hidden.txt", "Synthetic hidden comet fact"
    )
    hidden = await asyncio.to_thread(workflow.approve, scope, hidden_candidate.id)
    revoked_candidate = await stage(
        workflow, tmp_path, scope, "revoked.txt", "Synthetic revoked comet fact"
    )
    revoked = await asyncio.to_thread(workflow.approve, scope, revoked_candidate.id)

    assert [item["id"] for item in (await gateway.search("hidden"))["results"]] == [
        str(hidden.memory_id)
    ]
    workflow.hide(scope, hidden.memory_id, True)
    assert (await gateway.fetch(hidden.memory_id))["memory"] is None
    assert (await gateway.search("hidden"))["results"] == []

    workflow.revoke(scope, revoked.provenance.source_id, "source")
    assert (await gateway.fetch(revoked.memory_id))["memory"] is None
    assert (await gateway.search("revoked"))["results"] == []
    assert (await gateway.context("revoked comet fact"))["bundle"]["items"] == []

    with db_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM local_grants WHERE actor_id=:a AND scope_id=:s"),
            {"a": reader, "s": scope},
        )
    assert not permissions.allows(AccessRequest(actor_id=reader, resource_id=scope, action="read"))
    with pytest.raises(PermissionError, match="permission.denied"):
        await gateway.search("comet")
    with pytest.raises(PermissionError, match="permission.denied"):
        await gateway.fetch(hidden.memory_id)
    with pytest.raises(PermissionError, match="permission.denied"):
        await gateway.context("comet fact")


@pytest.mark.integration
async def test_gateway_proposal_review_and_live_downgrade(
    db_engine: Engine, tmp_path: Path
) -> None:
    workflow, _, scope = setup(db_engine, tmp_path)
    proposer, request = uuid4(), uuid4()
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO local_identities(id,principal) VALUES (:a,:p)"),
            {"a": proposer, "p": "synthetic:" + str(proposer)},
        )
        connection.execute(
            text("INSERT INTO local_grants VALUES (:a,:s,'proposer')"),
            {"a": proposer, "s": scope},
        )
    gateway = MemoryGateway(db_engine, proposer, scope, "synthetic-proposer")
    status = gateway.status()
    assert status["candidate_submission"] is True and status["read_only"] is False
    assert status["human_approval_required"] is False
    assert status["sensitive_memory_human_approval_required"] is True
    assert status["ordinary_memory_auto_approval"] is True
    assert status["direct_memory_write"] is False
    proposed = await gateway.propose_memory(
        "Synthetic proposal", "Synthetic health orbital observation", request
    )
    assert proposed["status"] == "pending"
    assert await gateway.proposal_status(request) == proposed
    assert (await gateway.search("orbital"))["results"] == []
    memory = await asyncio.to_thread(workflow.approve, scope, UUID(proposed["candidate_id"]))
    assert (await gateway.proposal_status(request))["status"] == "approved"
    results = (await gateway.search("orbital"))["results"]
    assert [item["id"] for item in results] == [str(memory.memory_id)]
    assert results[0]["provenance"]["level"] == "model_guess"
    assert results[0]["provenance"]["verified"] is False
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE local_grants SET role='reader' WHERE actor_id=:a AND scope_id=:s"),
            {"a": proposer, "s": scope},
        )
    status = gateway.status()
    assert status["candidate_submission"] is False and status["read_only"] is True
    with pytest.raises(PermissionError, match="permission.denied"):
        await gateway.propose_memory("Synthetic", "Another synthetic proposal", uuid4())
    with pytest.raises(PermissionError, match="permission.denied"):
        await gateway.proposal_status(request)
    assert [item["id"] for item in (await gateway.search("orbital"))["results"]] == [
        str(memory.memory_id)
    ]


@pytest.mark.integration
async def test_gateway_editor_revises_deletes_and_removes_tags(
    db_engine: Engine, tmp_path: Path
) -> None:
    workflow, _, scope = setup(db_engine, tmp_path)
    editor, request = uuid4(), uuid4()
    with db_engine.begin() as connection:
        connection.execute(
            text("INSERT INTO local_identities(id,principal) VALUES (:id,:p)"),
            {"id": editor, "p": "synthetic:" + str(editor)},
        )
        connection.execute(
            text("INSERT INTO local_grants VALUES (:a,:s,'editor')"),
            {"a": editor, "s": scope},
        )
    gateway = MemoryGateway(db_engine, editor, scope, "synthetic-editor")
    status = gateway.status()
    assert status["memory_edit"] is True and status["memory_delete"] is True
    assert status["tag_deletion"] is True and status["tag_removal"] is True
    assert status["direct_memory_write"] is True
    candidate = await stage(workflow, tmp_path, scope, "editor.txt", "Synthetic editor comet fact")
    memory = await asyncio.to_thread(workflow.approve, scope, candidate.id)
    edited = await gateway.edit_memory(
        memory.memory_id, 1, "Synthetic editor revised fact", request
    )
    assert edited["revision"] == 2
    fetched = await gateway.fetch(memory.memory_id)
    assert fetched["memory"]["text"] == "Synthetic editor revised fact"
    tag_id = UUID((await gateway.create_tag("Synthetic editor tag"))["tag"]["id"])
    assigned = await gateway.add_memory_tags(memory.memory_id, [tag_id])
    assert len(assigned["tags"]) == 1
    remaining = await gateway.remove_memory_tags(memory.memory_id, [tag_id])
    assert remaining["tags"] == []
    deleted = await gateway.delete_memory(memory.memory_id, 2)
    assert deleted["deleted"] is True and deleted["already_deleted"] is False
    assert (await gateway.fetch(memory.memory_id))["memory"] is None
    assert (await gateway.search("revised"))["results"] == []
    assert (await gateway.delete_tag(tag_id))["deleted"] is True
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE local_grants SET role='reader' WHERE actor_id=:a"), {"a": editor}
        )
    status = gateway.status()
    assert status["read_only"] is True
    with pytest.raises(PermissionError, match="permission.denied"):
        await gateway.edit_memory(memory.memory_id, 2, "Synthetic note", uuid4())
    with pytest.raises(PermissionError, match="permission.denied"):
        await gateway.delete_tag(tag_id)


@pytest.mark.integration
async def test_gateway_lists_visible_memories_with_pagination(
    db_engine: Engine, tmp_path: Path
) -> None:
    workflow, owner, scope = setup(db_engine, tmp_path)
    gateway = MemoryGateway(db_engine, owner, scope, "synthetic-owner")
    first_candidate = await stage(workflow, tmp_path, scope, "first.txt", "Synthetic first entry")
    first = await asyncio.to_thread(workflow.approve, scope, first_candidate.id)
    second_candidate = await stage(
        workflow, tmp_path, scope, "second.txt", "Synthetic second entry"
    )
    second = await asyncio.to_thread(workflow.approve, scope, second_candidate.id)
    page = await gateway.list_memories(limit=1)
    assert page["total"] == 2 and len(page["items"]) == 1
    assert page["offset"] == 0 and page["limit"] == 1
    items = (await gateway.list_memories(limit=100))["items"]
    assert {item["id"] for item in items} == {str(first.memory_id), str(second.memory_id)}
    listed = next(item for item in items if item["id"] == str(second.memory_id))
    assert listed["title"] == "second.txt"
    assert listed["text"].startswith("Synthetic second entry")
    assert listed["truncated"] is False and listed["tags"] == []
    tag_id = UUID((await gateway.create_tag("Synthetic archive"))["tag"]["id"])
    await gateway.add_memory_tags(second.memory_id, [tag_id])
    filtered = await gateway.list_memories(limit=100, tag_id=tag_id)
    assert [item["id"] for item in filtered["items"]] == [str(second.memory_id)]
    workflow.hide(scope, first.memory_id, True)
    visible = await gateway.list_memories(limit=100)
    assert [item["id"] for item in visible["items"]] == [str(second.memory_id)]
    assert visible["total"] == 1
