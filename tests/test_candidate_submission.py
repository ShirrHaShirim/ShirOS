"""Model proposals are privacy-gated drafts, not approved memories."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from shiros.candidate_submission import proposal_status, submit
from shiros.core.retrieval import SearchRequest
from shiros.identity import RIGHTS, DatabasePermissions, LocalIdentity, Role
from shiros.review_workflow import ReviewWorkflow
from shiros.shared_memory import build_shared_memory


def actor(engine: Engine, scope: UUID, role: str) -> UUID:
    identifier = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO local_identities(id,principal) VALUES (:a,:p)"),
            {"a": identifier, "p": "synthetic:" + str(identifier)},
        )
        connection.execute(
            text("INSERT INTO local_grants VALUES (:a,:s,:r)"),
            {"a": identifier, "s": scope, "r": role},
        )
    return identifier


def workflow(engine: Engine, scope: UUID, root: Path) -> ReviewWorkflow:
    owner = actor(engine, scope, "owner")
    return ReviewWorkflow(engine, LocalIdentity(engine, lambda: "synthetic:" + str(owner)), root)


def test_proposer_is_not_writer_or_reviewer() -> None:
    assert RIGHTS[Role.PROPOSER] == {"read", "propose"}


@pytest.mark.integration
def test_submission_idempotency_and_human_approval(db_engine: Engine, tmp_path: Path) -> None:
    scope, request = uuid4(), uuid4()
    proposer = actor(db_engine, scope, "proposer")
    review = workflow(db_engine, scope, tmp_path)
    result = submit(
        db_engine, proposer, scope, "Synthetic draft", "Synthetic health proposal note", request
    )
    assert result["status"] == "pending"
    assert (
        submit(
            db_engine, proposer, scope, "Synthetic draft", "Synthetic health proposal note", request
        )
        == result
    )
    with pytest.raises(ValueError, match="request_conflict"):
        submit(db_engine, proposer, scope, "Changed title", "Synthetic proposal note", request)
    candidate = review.queue(scope)[0]
    assert candidate.suggested_fact_level == "model_guess"
    with db_engine.connect() as connection:
        services = build_shared_memory(db_engine, DatabasePermissions(connection))
        query = SearchRequest(scope_ids=(scope,), query="Synthetic", mode="keyword")
        assert asyncio.run(services.retrieval.search(proposer, query)) == []
    memory = review.approve(scope, candidate.id)
    assert memory.provenance.level == "model_guess" and not memory.provenance.verified
    assert proposal_status(db_engine, proposer, scope, request)["status"] == "approved"
    with db_engine.connect() as connection:
        services = build_shared_memory(db_engine, DatabasePermissions(connection))
        assert len(asyncio.run(services.retrieval.search(proposer, query))) == 1
    review.revoke(scope, candidate.source_id)
    assert proposal_status(db_engine, proposer, scope, request)["status"] == "invalidated"
    with pytest.raises(PermissionError, match="revoked"):
        submit(
            db_engine, proposer, scope, "Synthetic draft", "Synthetic health proposal note", uuid4()
        )


@pytest.mark.integration
def test_privacy_permission_and_receipt_isolation(db_engine: Engine) -> None:
    scope, request = uuid4(), uuid4()
    proposer = actor(db_engine, scope, "proposer")
    reader = actor(db_engine, scope, "reader")
    for title, value in [
        ("Synthetic", "password=synthetic-secret"),
        ("password=synthetic-secret", "Synthetic"),
    ]:
        with pytest.raises(PermissionError):
            submit(db_engine, proposer, scope, title, value, request)
    with db_engine.connect() as connection:
        for table in ("review_sources", "memory_candidates", "proposal_receipts", "review_audit"):
            assert (
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"), {"s": scope}
                )
                == 0
            )
    with pytest.raises(PermissionError):
        submit(db_engine, reader, scope, "Synthetic", "Synthetic note", request)
    submit(db_engine, proposer, scope, "Synthetic", "Synthetic note", request)
    other = actor(db_engine, scope, "proposer")
    with pytest.raises(ValueError, match="not_found"):
        proposal_status(db_engine, other, scope, request)
    with pytest.raises(PermissionError):
        proposal_status(db_engine, proposer, uuid4(), request)
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE local_grants SET role='reader' WHERE actor_id=:a"), {"a": proposer}
        )
    with pytest.raises(PermissionError):
        proposal_status(db_engine, proposer, scope, request)


@pytest.mark.integration
def test_rejected_duplicates_stay_closed(db_engine: Engine, tmp_path: Path) -> None:
    scope, request = uuid4(), uuid4()
    proposer = actor(db_engine, scope, "proposer")
    review = workflow(db_engine, scope, tmp_path)
    result = submit(
        db_engine, proposer, scope, "Synthetic", "Synthetic health rejected note", request
    )
    review.reject(scope, UUID(result["candidate_id"]))
    assert (
        submit(db_engine, proposer, scope, "Synthetic", "Synthetic health rejected note", request)[
            "status"
        ]
        == "rejected"
    )
    with pytest.raises(ValueError, match="closed"):
        submit(db_engine, proposer, scope, "Synthetic", "Synthetic health rejected note", uuid4())


@pytest.mark.integration
def test_submission_atomicity_and_limits(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, request = uuid4(), uuid4()
    proposer = actor(db_engine, scope, "proposer")
    for title, value in [("", "note"), ("title", " "), ("x" * 251, "note"), ("title", "x" * 20001)]:
        with pytest.raises(ValueError, match="invalid_input"):
            submit(db_engine, proposer, scope, title, value, request)

    def fail(*args: object) -> None:
        raise RuntimeError("synthetic.audit_failure")

    with monkeypatch.context() as patch:
        patch.setattr("shiros.candidate_submission.audit", fail)
        with pytest.raises(RuntimeError, match="audit_failure"):
            submit(db_engine, proposer, scope, "Synthetic", "Synthetic atomic note", request)
    with db_engine.connect() as connection:
        for table in ("review_sources", "memory_candidates", "proposal_receipts"):
            assert (
                connection.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"), {"s": scope}
                )
                == 0
            )
    assert (
        submit(db_engine, proposer, scope, "Synthetic", "Synthetic atomic note", request)["status"]
        == "approved"
    )
