"""Untrusted models may propose sanitized candidates, never approve memories."""

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine
from sqlalchemy import text as sql

from shiros.adapters.database.records import lock_writes
from shiros.auto_review import approve_new_candidate, review_reason
from shiros.identity import DatabasePermissions, audit
from shiros.intake import content_hash
from shiros.shared_memory import build_shared_memory
from shiros.visibility import memory_visible


def _result(connection: Connection, candidate: UUID, request: UUID) -> dict[str, str]:
    row = (
        connection.execute(
            sql(
                "SELECT c.source_id,c.scope_id,c.target_memory_id,"
                "s.status,s.memory_revision_id,s.reason_code "
                "FROM memory_candidates c "
                "JOIN LATERAL (SELECT status,memory_revision_id,reason_code FROM candidate_states "
                "WHERE candidate_id=c.id ORDER BY revision DESC LIMIT 1) s ON true WHERE c.id=:id"
            ),
            {"id": candidate},
        )
        .mappings()
        .one()
    )
    revoked = connection.scalar(
        sql("SELECT 1 FROM revocations WHERE kind='intake_source' AND target_id=:id"),
        {"id": row["source_id"]},
    )
    invisible = row["memory_revision_id"] and not memory_visible(
        connection, row["memory_revision_id"]
    )
    if row["target_memory_id"]:
        target = connection.scalar(
            sql(
                "SELECT id FROM memories WHERE memory_id=:id AND scope_id=:s "
                "ORDER BY revision DESC LIMIT 1"
            ),
            {"id": row["target_memory_id"], "s": row["scope_id"]},
        )
        invisible = invisible or target is None or not memory_visible(connection, target)
    return {
        "request_id": str(request),
        "candidate_id": str(candidate),
        "source_id": str(row["source_id"]),
        "status": "invalidated" if revoked or invisible else str(row["status"]),
        "review_reason": str(row["reason_code"]),
    }


def proposal_status(engine: Engine, actor: UUID, scope: UUID, request_id: UUID) -> dict[str, str]:
    with engine.begin() as connection:
        lock_writes(connection)
        DatabasePermissions(connection).require(actor, scope, "propose")
        candidate = connection.scalar(
            sql(
                "SELECT candidate_id FROM proposal_receipts WHERE actor_id=:a "
                "AND scope_id=:s AND request_id=:r"
            ),
            {"a": actor, "s": scope, "r": request_id},
        )
        if candidate is None:
            raise ValueError("proposal.not_found")
        return _result(connection, candidate, request_id)


def submit(
    engine: Engine, actor: UUID, scope: UUID, title: str, text: str, request_id: UUID
) -> dict[str, str]:
    if not title.strip() or not text.strip() or len(title) > 250 or len(text) > 20000:
        raise ValueError("proposal.invalid_input")
    with engine.begin() as connection:
        lock_writes(connection)
        permissions = DatabasePermissions(connection)
        permissions.require(actor, scope, "propose")
        memory = build_shared_memory(engine, permissions).memory
        safe, sensitivity = memory.sanitize(text)
        safe_title, _ = memory.sanitize(title)
        if len(safe) > 20000 or len(safe_title) > 250:
            raise ValueError("proposal.invalid_input")
        # Fingerprints are calculated only after the privacy gate, never from raw input.
        fingerprint = content_hash(json.dumps([safe_title, safe], ensure_ascii=True))
        params = {"a": actor, "s": scope, "r": request_id}
        receipt = (
            connection.execute(
                sql(
                    "SELECT candidate_id,payload_hash FROM proposal_receipts WHERE actor_id=:a "
                    "AND scope_id=:s AND request_id=:r"
                ),
                params,
            )
            .mappings()
            .first()
        )
        if receipt:
            if receipt["payload_hash"] != fingerprint:
                raise ValueError("proposal.request_conflict")
            return _result(connection, receipt["candidate_id"], request_id)
        digest = content_hash(safe)
        candidate = connection.scalar(
            sql(
                "SELECT c.id FROM memory_candidates c JOIN review_sources r ON r.id=c.source_id "
                "WHERE r.scope_id=:s AND r.content_hash=:h"
            ),
            {"s": scope, "h": digest},
        )
        if candidate:
            result = _result(connection, candidate, request_id)
            if result["status"] == "invalidated":
                raise PermissionError("source.revoked")
            if result["status"] == "rejected":
                raise ValueError("candidate.closed")
        else:
            source, candidate = uuid4(), uuid4()
            connection.execute(
                sql(
                    "INSERT INTO review_sources(id,scope_id,kind,origin,title,text,content_hash,"
                    "observed_at,created_by,privacy_state,sensitivity) VALUES "
                    "(:id,:s,'text','model-proposal',:title,:text,:hash,:observed,:a,"
                    "'staging_allowed',:sensitivity)"
                ),
                {
                    "id": source,
                    "s": scope,
                    "title": safe_title,
                    "text": safe,
                    "hash": digest,
                    "observed": datetime.now(UTC),
                    "a": actor,
                    "sensitivity": sensitivity.value,
                },
            )
            connection.execute(
                sql(
                    "INSERT INTO memory_candidates(id,scope_id,source_id,original_text,fact_level,"
                    "confidence,created_by) VALUES (:id,:s,:source,:text,'model_guess',0,:a)"
                ),
                {"id": candidate, "s": scope, "source": source, "text": safe, "a": actor},
            )
            connection.execute(
                sql(
                    "INSERT INTO candidate_states(id,candidate_id,revision,status,text,actor_id,"
                    "reason_code) VALUES (:id,:c,1,'pending',:text,:a,:reason)"
                ),
                {
                    "id": uuid4(),
                    "c": candidate,
                    "text": safe,
                    "a": actor,
                    "reason": review_reason(title, text),
                },
            )
            audit(connection, actor, scope, candidate, "stage")
            approve_new_candidate(
                engine, connection, actor, scope, candidate, originals=(title, text)
            )
        connection.execute(
            sql(
                "INSERT INTO proposal_receipts(actor_id,scope_id,request_id,candidate_id,"
                "payload_hash) VALUES (:a,:s,:r,:c,:h)"
            ),
            {**params, "c": candidate, "h": fingerprint},
        )
        return _result(connection, candidate, request_id)
