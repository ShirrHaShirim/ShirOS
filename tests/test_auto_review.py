"""Synthetic policy routing, provenance and atomicity checks."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from test_candidate_submission import actor, workflow

from shiros.auto_review import POLICY_PRINCIPAL, review_reason
from shiros.candidate_submission import submit
from shiros.memory_mutations import edit_memory


@pytest.mark.parametrize(
    "value",
    [
        "Contact test@example.test",
        "My diagnosis is synthetic",
        "工资 5000 元",
        "My partner prefers this",
        "Unknown origin",
        "x" * 6001,
        "Synthetic\u200bnote",
        "[REDACTED_EMAIL]",
        "Ｓｙｎｔｈｅｔｉｃ ｈｅａｌｔｈ",
        "I am Christian",
        "我是基督徒",
        "我住在示例街",
        "我母亲的记录",
        "HIV",
        "cancer",
        "My friend told me this",
    ],
)
def test_sensitive_or_uncertain_needs_review(value: str) -> None:
    assert review_reason(value) != "auto_approve_ordinary_v1"


def test_ordinary_and_blocked_policy() -> None:
    assert review_reason("I prefer concise answers") == "auto_approve_ordinary_v1"
    assert review_reason("Reading notes separate evidence and open questions.") == (
        "auto_approve_ordinary_v1"
    )
    assert review_reason("我喜欢简洁的界面和嵌套标签。") == "auto_approve_ordinary_v1"
    assert review_reason("password=synthetic-secret") == "blocked_privacy"


@pytest.mark.integration
def test_ordinary_autoapproval_is_atomic_idempotent_and_truthful(
    db_engine: Engine,
    tmp_path: Path,
) -> None:
    scope, request = uuid4(), uuid4()
    proposer = actor(db_engine, scope, "proposer")
    review = workflow(db_engine, scope, tmp_path)
    result = submit(db_engine, proposer, scope, "Reading", "Synthetic reading note", request)
    assert result["status"] == "approved"
    assert result["review_reason"] == "auto_approve_ordinary_v1"
    assert (
        submit(db_engine, proposer, scope, "Reading", "Synthetic reading note", request) == result
    )
    assert review.queue(scope) == []
    memory = review.approve(scope, UUID(result["candidate_id"]))
    assert memory.provenance.level == "model_guess" and not memory.provenance.verified
    assert memory.provenance.created_by == str(proposer)
    with db_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT principal FROM local_identities WHERE id=:a"),
                {"a": memory.privacy.reviewed_by},
            )
            == POLICY_PRINCIPAL
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM local_grants WHERE actor_id=:a"),
                {"a": memory.privacy.reviewed_by},
            )
            == 0
        )
        assert (
            connection.scalar(text("SELECT count(*) FROM memories WHERE scope_id=:s"), {"s": scope})
            == 1
        )


@pytest.mark.integration
def test_title_contacts_and_old_pending_do_not_autoapprove(
    db_engine: Engine, tmp_path: Path
) -> None:
    scope = uuid4()
    proposer = actor(db_engine, scope, "proposer")
    review = workflow(db_engine, scope, tmp_path)
    for title, body in [("Health", "Synthetic note"), ("Contact", "test@example.test")]:
        result = submit(db_engine, proposer, scope, title, body, uuid4())
        assert result["status"] == "pending"
        assert result["review_reason"] == "requires_sensitive_review"
    old = review.queue(scope)[0]
    review.edit(scope, old.id, "Synthetic ordinary correction")
    duplicate = submit(db_engine, proposer, scope, "Health", "Synthetic note", uuid4())
    assert duplicate["status"] == "edited"
    candidate = review.stage(scope, review.preview_text(scope, "note.txt", "Ordinary geometry").id)
    assert candidate.status == "approved"
    assert candidate.review_reason == "auto_approve_ordinary_v1"


@pytest.mark.integration
def test_autoapproval_failure_rolls_back_and_editor_cannot_bypass(
    db_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = uuid4()
    editor = actor(db_engine, scope, "editor")
    review = workflow(db_engine, scope, tmp_path)

    def fail(*args: object) -> None:
        raise RuntimeError("synthetic.failure")

    with monkeypatch.context() as patch:
        patch.setattr("shiros.auto_review.audit", fail)
        with pytest.raises(RuntimeError, match="synthetic.failure"):
            submit(db_engine, editor, scope, "Reading", "Synthetic reading note", uuid4())
    with db_engine.connect() as connection:
        for table in ("memories", "memory_candidates", "review_sources", "proposal_receipts"):
            assert (
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"), {"s": scope}
                )
                == 0
            )
    result = submit(db_engine, editor, scope, "Reading", "Synthetic reading note", uuid4())
    memory = review.approve(scope, UUID(result["candidate_id"]))
    proposed = edit_memory(
        db_engine, editor, scope, memory.memory_id, 1, "Synthetic medical diagnosis", uuid4()
    )
    assert proposed["status"] == "pending" and proposed["review_required"] is True
    assert review.memory(scope, memory.memory_id) == memory
