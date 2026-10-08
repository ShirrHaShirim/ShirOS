"""Synthetic local review workflow and transaction/privacy boundary tests."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from shiros.identity import LocalIdentity
from shiros.intake import preview_file
from shiros.review_workflow import ReviewWorkflow


def setup(engine: Engine, root: Path, role: str = "owner") -> tuple[ReviewWorkflow, UUID]:
    actor, scope = uuid4(), uuid4()
    principal = "synthetic:" + str(actor)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO local_identities(id,principal) VALUES (:id,:p)"),
            {"id": actor, "p": principal},
        )
        connection.execute(
            text("INSERT INTO local_grants VALUES (:a,:s,:r)"), {"a": actor, "s": scope, "r": role}
        )
    return ReviewWorkflow(engine, LocalIdentity(engine, lambda: principal), root), scope


def count(engine: Engine, scope: UUID, table: str = "memories") -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"), {"s": scope})
            or 0
        )


@pytest.mark.integration
def test_stage_edit_approve_and_audit(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    with pytest.raises(PermissionError):
        workflow.require_backup_admin(scope)
    (tmp_path / "synthetic.md").write_text("Synthetic health observation", encoding="utf-8")
    preview = workflow.preview(scope, "synthetic.md")
    assert count(db_engine, scope, "review_sources") == 0
    candidate = workflow.stage(scope, preview.id)
    assert candidate.status == "pending" and count(db_engine, scope) == 0
    assert workflow.stage(scope, workflow.preview(scope, "synthetic.md").id).id == candidate.id
    with pytest.raises(PermissionError):
        workflow.edit(scope, candidate.id, "password=synthetic-do-not-store")
    edited = workflow.edit(scope, candidate.id, "Reviewed synthetic geometry")
    assert edited.original_text == candidate.text and edited.revision == 2
    memory = workflow.approve(scope, candidate.id)
    assert memory.text == edited.text
    assert workflow.approve(scope, candidate.id).id == memory.id
    assert count(db_engine, scope) == 1 and count(db_engine, scope, "events") == 1
    assert workflow.queue(scope) == []
    assert len(workflow.inspect(scope, candidate.id)["history"]) == 3
    assert workflow.memory(scope, memory.memory_id) == memory
    workflow.hide(scope, memory.memory_id, True)
    assert workflow.memory(scope, memory.memory_id) is None
    workflow.hide(scope, memory.memory_id, False)
    assert workflow.memory(scope, memory.memory_id) == memory
    workflow.revoke(scope, candidate.source_id)
    assert workflow.memory(scope, memory.memory_id) is None
    with pytest.raises(PermissionError):
        workflow.approve(scope, candidate.id)
    with pytest.raises(PermissionError):
        workflow.inspect(scope, candidate.id)
    with pytest.raises(PermissionError):
        workflow.stage(scope, preview.id)


@pytest.mark.integration
def test_reject_permissions_and_private_staging(db_engine: Engine, tmp_path: Path) -> None:
    workflow, scope = setup(db_engine, tmp_path, "reviewer")
    (tmp_path / "note.txt").write_text("Synthetic health note", encoding="utf-8")
    candidate = workflow.stage(scope, workflow.preview(scope, "note.txt").id)
    assert workflow.reject(scope, candidate.id).status == "rejected"
    with pytest.raises(ValueError):
        workflow.approve(scope, candidate.id)
    assert count(db_engine, scope) == 0
    (tmp_path / "allowed.txt").write_text("Reviewer approved synthetic note", encoding="utf-8")
    allowed = workflow.stage(scope, workflow.preview(scope, "allowed.txt").id)
    assert workflow.approve(scope, allowed.id).text == "Reviewer approved synthetic note"
    with pytest.raises(PermissionError):
        workflow.require_admin(scope)
    with pytest.raises(PermissionError):
        workflow.revoke(scope, candidate.source_id)
    (tmp_path / "secret.txt").write_text("password=synthetic-secret", encoding="utf-8")
    preview = workflow.preview(scope, "secret.txt")
    assert not preview.privacy.persistence_allowed
    with pytest.raises(PermissionError):
        workflow.stage(scope, preview.id)
    assert count(db_engine, scope, "review_sources") == 2
    reader, reader_scope = setup(db_engine, tmp_path, "reader")
    assert reader.queue(reader_scope) == []
    for action in (
        lambda: reader.approve(scope, candidate.id),
        lambda: reader.approve(reader_scope, candidate.id),
        lambda: reader.preview(reader_scope, "note.txt"),
        lambda: workflow.queue(uuid4()),
    ):
        with pytest.raises(PermissionError):
            action()
    unknown = ReviewWorkflow(db_engine, LocalIdentity(db_engine, lambda: "unregistered"), tmp_path)
    with pytest.raises(PermissionError):
        unknown.queue(scope)


@pytest.mark.integration
def test_approval_audit_failure_rolls_back(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    (tmp_path / "note.txt").write_text("Atomic synthetic health approval", encoding="utf-8")
    candidate = workflow.stage(scope, workflow.preview(scope, "note.txt").id)

    def fail(*args: object) -> None:
        raise RuntimeError("synthetic.audit_failure")

    with monkeypatch.context() as context:
        context.setattr("shiros.review_workflow.audit", fail)
        with pytest.raises(RuntimeError, match="synthetic.audit_failure"):
            workflow.approve(scope, candidate.id)
    for table in ("memories", "sources", "events", "memory_receipts"):
        assert count(db_engine, scope, table) == 0
    assert workflow.queue(scope)[0].status == "pending"
    workflow.approve(scope, candidate.id)
    assert count(db_engine, scope) == 1


@pytest.mark.parametrize("filename", ["../note.txt", "C:\\note.txt", "sub/note.txt", "x.exe"])
def test_preview_restricts_paths(tmp_path: Path, filename: str) -> None:
    (tmp_path / "x.exe").write_text("synthetic", encoding="utf-8")
    with pytest.raises(ValueError):
        preview_file(tmp_path, filename)


@pytest.mark.parametrize(
    "name,content,kind",
    [
        ("a.txt", "Synthetic text", "text"),
        ("a.md", "# Synthetic", "markdown"),
        ("a.json", '{"synthetic": true}', "json"),
        ("a.csv", "a,b\n1,2", "csv"),
    ],
)
def test_supported_formats(tmp_path: Path, name: str, content: str, kind: str) -> None:
    (tmp_path / name).write_text(content, encoding="utf-8")
    preview = preview_file(tmp_path, name)
    assert preview.kind == kind and preview.privacy.persistence_allowed


def test_preview_limits_and_hardlinks(tmp_path: Path) -> None:
    source = tmp_path / "large.txt"
    source.write_text("x" * 65537, encoding="utf-8")
    with pytest.raises(ValueError, match="too_large"):
        preview_file(tmp_path, source.name)
    source.write_text("synthetic", encoding="utf-8")
    (tmp_path / "linked.txt").hardlink_to(source)
    with pytest.raises(ValueError, match="invalid_path"):
        preview_file(tmp_path, "linked.txt")
    (tmp_path / "bad.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_content"):
        preview_file(tmp_path, "bad.json")
