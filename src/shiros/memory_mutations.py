"""Delegated, audited mutation of existing memory; never approves new memory."""

import asyncio
import json
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from shiros.adapters.database.records import (
    PostgresMemoryRepository,
    lock_writes,
)
from shiros.auto_review import review_reason
from shiros.core.memory import Memory
from shiros.core.records import PersistenceMetadata
from shiros.core.schemas import Provenance, utc_now
from shiros.identity import DatabasePermissions, audit
from shiros.intake import content_hash
from shiros.memory_metadata import SourceRecord, details
from shiros.shared_memory import build_shared_memory
from shiros.visibility import memory_visible


def _require(connection: Connection, actor: UUID, scope: UUID, action: str) -> None:
    lock_writes(connection)
    permissions = DatabasePermissions(connection)
    permissions.require(actor, scope, "read")
    if not (permissions.has(actor, scope, action) or permissions.has(actor, scope, "admin")):
        raise PermissionError("permission.denied")


def _result(memory: Memory) -> dict[str, Any]:
    return {
        "memory_id": str(memory.memory_id),
        "revision_id": str(memory.id),
        "revision": memory.revision,
        "edited": True,
        "status": "applied",
    }


def sanitize_metadata(
    engine: Engine,
    connection: Connection,
    title: str | None,
    sources: list[dict[str, Any]] | None,
) -> tuple[str, list[dict[str, Any]]]:
    service = build_shared_memory(engine, DatabasePermissions(connection)).memory
    if title is None or sources is None:
        raise ValueError("memory.invalid_metadata")
    clean_title, _ = service.sanitize(title)
    if len(clean_title) > 250:
        raise ValueError("memory.invalid_title")
    if "|" in clean_title:
        parts = [part.strip() for part in clean_title.split("|")]
        if len(parts) != 3 or not all(parts):
            raise ValueError("memory.invalid_title")
        clean_title = "｜".join(parts)
    if len(sources) > 30:
        raise ValueError("memory.too_many_sources")
    safe_sources = []
    for value in sources:
        data = SourceRecord.model_validate(value).model_dump(mode="json")
        for key in ("title", "text", "locator"):
            if data[key]:
                data[key], _ = service.sanitize(data[key])
        safe_sources.append(data)
    identifiers = [record["id"] for record in safe_sources if record["id"]]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("memory.invalid_source_id")
    if len(json.dumps(safe_sources, ensure_ascii=False).encode("utf-8")) > 65536:
        raise ValueError("memory.too_large")
    for record in safe_sources:
        if record["id"] is None:
            record["id"] = str(uuid4())
    return clean_title, safe_sources


def _approved_reuse(value: str, approved: str) -> bool:
    def compact(part: str) -> str:
        return re.sub(r"\s+", "", part)

    if compact(value) == compact(approved):
        return True
    old_lines = {line.strip().lstrip("#>*- ") for line in approved.splitlines()}
    return all(
        review_reason(line) == "auto_approve_ordinary_v1"
        or line.strip().lstrip("#>*- ") in old_lines
        for line in value.splitlines()
        if line.strip()
    )


def _needs_review(
    body: str, title: str, sources: list[dict[str, Any]], current: Memory, old: dict[str, Any]
) -> bool:
    if not _approved_reuse(body, current.text):
        return True
    basis = "\n".join(
        [
            current.text,
            old["title"],
            *[
                "\n".join(str(record.get(key) or "") for key in ("title", "text", "locator"))
                for record in old["source_records"]
            ],
        ]
    )
    for value in [
        title,
        *[str(record.get(key) or "") for record in sources for key in ("title", "text", "locator")],
    ]:
        if value and review_reason(value) != "auto_approve_ordinary_v1" and value not in basis:
            return True
    return False


def apply_revision(
    engine: Engine,
    connection: Connection,
    actor: UUID,
    current: Memory,
    body: str,
    title: str,
    sources: list[dict[str, Any]],
    request_id: UUID,
    digest: str,
    reviewer: UUID,
) -> Memory:
    """Internal transaction-bound apply after editor policy or human approval."""
    services = build_shared_memory(engine, DatabasePermissions(connection))
    safe, sensitivity = services.memory.sanitize(body)
    revision = Memory(
        memory_id=current.memory_id,
        entity_id=current.entity_id,
        revision=current.revision + 1,
        supersedes_id=current.id,
        scope_id=current.scope_id,
        provenance=Provenance(
            source_id=current.provenance.source_id,
            observed_at=current.provenance.observed_at,
            created_by=str(actor),
            level=current.provenance.level,
            confidence=current.provenance.confidence,
            verified=False,
        ),
        privacy=PersistenceMetadata(sensitivity=sensitivity, reviewed_by=reviewer),
        domain=current.domain,
        text=safe,
    )
    asyncio.run(PostgresMemoryRepository(connection).save(revision))
    connection.execute(
        text(
            "INSERT INTO memory_revision_metadata(revision_id,scope_id,title,source_records) "
            "VALUES (:id,:s,:t,CAST(:records AS jsonb))"
        ),
        {
            "id": revision.id,
            "s": current.scope_id,
            "t": title,
            "records": json.dumps(sources, ensure_ascii=True),
        },
    )
    asyncio.run(services.memory.persist_embedding(connection, revision))
    asyncio.run(services.memory.append_event(connection, revision, "memory.revised"))
    connection.execute(
        text(
            "INSERT INTO memory_receipts(scope_id,actor_id,idempotency_key,input_hash,result_id) "
            "VALUES (:s,:a,:r,:h,:id)"
        ),
        {"s": current.scope_id, "a": actor, "r": request_id, "h": digest, "id": revision.id},
    )
    audit(connection, actor, current.scope_id, current.memory_id, "edit")
    return revision


def _stage_edit(
    connection: Connection,
    actor: UUID,
    current: Memory,
    body: str,
    title: str,
    sources: list[dict[str, Any]],
    request_id: UUID,
    digest: str,
) -> dict[str, Any]:
    source, candidate = uuid4(), uuid4()
    connection.execute(
        text(
            "INSERT INTO review_sources(id,scope_id,kind,origin,title,text,content_hash,"
            "observed_at,"
            "created_by,privacy_state,sensitivity) VALUES (:id,:s,'text','model-proposal',"
            ":t,:body,:h,:observed,:a,'staging_allowed','personal')"
        ),
        {
            "id": source,
            "s": current.scope_id,
            "t": title,
            "body": body,
            "h": content_hash(str(actor) + str(request_id) + digest),
            "observed": utc_now(),
            "a": actor,
        },
    )
    connection.execute(
        text(
            "INSERT INTO memory_candidates(id,scope_id,source_id,original_text,fact_level,"
            "confidence,"
            "created_by,target_memory_id,expected_memory_revision) "
            "VALUES (:id,:s,:source,:body,:level,:confidence,:a,:memory,:revision)"
        ),
        {
            "id": candidate,
            "s": current.scope_id,
            "source": source,
            "body": body,
            "level": current.provenance.level.value,
            "confidence": current.provenance.confidence,
            "a": actor,
            "memory": current.memory_id,
            "revision": current.revision,
        },
    )
    connection.execute(
        text(
            "INSERT INTO candidate_states(id,candidate_id,revision,status,text,actor_id,"
            "reason_code,"
            "proposed_title,proposed_sources) VALUES (:id,:c,1,'pending',:body,:a,"
            "'requires_sensitive_review',:t,CAST(:records AS jsonb))"
        ),
        {
            "id": uuid4(),
            "c": candidate,
            "body": body,
            "a": actor,
            "t": title,
            "records": json.dumps(sources, ensure_ascii=True),
        },
    )
    connection.execute(
        text(
            "INSERT INTO proposal_receipts(actor_id,scope_id,request_id,candidate_id,payload_hash) "
            "VALUES (:a,:s,:r,:c,:h)"
        ),
        {"a": actor, "s": current.scope_id, "r": request_id, "c": candidate, "h": digest},
    )
    audit(connection, actor, current.scope_id, candidate, "stage")
    return {
        "memory_id": str(current.memory_id),
        "candidate_id": str(candidate),
        "status": "pending",
        "edited": False,
        "review_required": True,
        "review_reason": "requires_sensitive_review",
    }


def _mutate(
    engine: Engine,
    actor: UUID,
    scope: UUID,
    memory_id: UUID,
    expected_revision: int,
    request_id: UUID,
    *,
    operation: str,
    value: str | None = None,
    title: str | None = None,
    sources: list[dict[str, Any]] | None = None,
    source_text: str | None = None,
    source_title: str | None = None,
) -> dict[str, Any]:
    if not 1 <= expected_revision <= 2147483647:
        raise ValueError("memory.invalid_input")
    with engine.begin() as connection:
        _require(connection, actor, scope, "memory_edit")
        repository = PostgresMemoryRepository(connection)
        current = asyncio.run(repository.get(memory_id))
        if current is None or current.scope_id != scope:
            raise ValueError("memory.not_found")
        if not memory_visible(connection, current.id):
            raise PermissionError("memory.not_visible")
        latest = current
        base = connection.execute(
            text(
                "SELECT id FROM memories WHERE memory_id=:id AND scope_id=:s AND revision=:revision"
            ),
            {"id": memory_id, "s": scope, "revision": expected_revision},
        ).scalar_one_or_none()
        if base is None:
            raise ValueError("memory.revision_conflict")
        stored = asyncio.run(repository.revision(base))
        assert stored is not None
        current = stored
        services = build_shared_memory(engine, DatabasePermissions(connection))
        old = details(connection, current)
        safe_text, _ = services.memory.sanitize(current.text if value is None else value)
        if len(safe_text) > 20000:
            raise ValueError("memory.too_large")
        safe_title, _ = services.memory.sanitize(old["title"] if title is None else title)
        if len(safe_title) > 250:
            raise ValueError("memory.invalid_title")
        # Keep the user-facing convention while normalizing its individual fields.
        if "|" in safe_title:
            parts = [part.strip() for part in safe_title.split("|")]
            if title is not None and (len(parts) != 3 or not all(parts)):
                raise ValueError("memory.invalid_title")
            if len(parts) == 3 and all(parts):
                safe_title = "｜".join(parts)
        selected = old["source_records"] if sources is None else sources
        supplied = [SourceRecord.model_validate(record) for record in selected]
        if len(supplied) > 30:
            raise ValueError("memory.too_many_sources")
        safe_sources = []
        for record in supplied:
            data = record.model_dump(mode="json")
            for key in ("title", "text", "locator"):
                if data[key]:
                    data[key], _ = services.memory.sanitize(data[key])
            safe_sources.append(data)
        if source_text is not None:
            snippet, _ = services.memory.sanitize(source_text)
            record_title, _ = services.memory.sanitize(source_title or "Source")
            # Exact original span avoids guessing which source paragraph should move.
            if source_text not in current.text or current.text.count(source_text) != 1:
                raise ValueError("memory.source_span_not_unique")
            safe_text, _ = services.memory.sanitize(current.text.replace(source_text, "", 1))
            safe_sources.append(
                SourceRecord(
                    kind="note",
                    title=record_title,
                    text=snippet,
                ).model_dump(mode="json")
            )
            if len(safe_sources) > 30:
                raise ValueError("memory.too_many_sources")
        if len(json.dumps(safe_sources, ensure_ascii=False).encode("utf-8")) > 65536:
            raise ValueError("memory.too_large")
        # Fingerprint before generating IDs so retries of newly-created references are stable.
        digest = content_hash(
            json.dumps(
                [operation, str(memory_id), expected_revision, safe_text, safe_title, safe_sources],
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        receipt = (
            connection.execute(
                text(
                    "SELECT input_hash, result_id FROM memory_receipts "
                    "WHERE scope_id=:scope AND actor_id=:actor AND idempotency_key=:key"
                ),
                {"scope": scope, "actor": actor, "key": request_id},
            )
            .mappings()
            .first()
        )
        if receipt:
            if receipt["input_hash"] != digest:
                raise ValueError("memory.idempotency_conflict")
            result = asyncio.run(repository.revision(receipt["result_id"]))
            if result is None or not memory_visible(connection, result.id):
                raise PermissionError("memory.not_visible")
            return _result(result)
        pending = (
            connection.execute(
                text(
                    "SELECT c.id,c.target_memory_id,p.payload_hash,s.status,s.memory_revision_id "
                    "FROM proposal_receipts p JOIN memory_candidates c ON c.id=p.candidate_id "
                    "JOIN LATERAL (SELECT status,memory_revision_id FROM candidate_states "
                    "WHERE candidate_id=c.id ORDER BY revision DESC LIMIT 1) s ON true "
                    "WHERE p.actor_id=:a AND p.scope_id=:s AND p.request_id=:r"
                ),
                {"a": actor, "s": scope, "r": request_id},
            )
            .mappings()
            .first()
        )
        if pending:
            if pending["payload_hash"] != digest or pending["target_memory_id"] != memory_id:
                raise ValueError("memory.idempotency_conflict")
            return {
                "memory_id": str(memory_id),
                "candidate_id": str(pending["id"]),
                "status": pending["status"],
                "edited": pending["status"] == "approved",
                "review_required": pending["status"] in ("pending", "edited"),
            }
        if latest.revision != expected_revision:
            raise ValueError("memory.revision_conflict")
        existing_ids = {record["id"] for record in old["source_records"]}
        identifiers = [record["id"] for record in safe_sources if record["id"]]
        if len(set(identifiers)) != len(identifiers) or any(
            identifier not in existing_ids for identifier in identifiers
        ):
            raise ValueError("memory.invalid_source_id")
        for record in safe_sources:
            if record["id"] is None:
                record["id"] = str(uuid4())
        if not DatabasePermissions(connection).has(actor, scope, "review") and _needs_review(
            safe_text, safe_title, safe_sources, current, old
        ):
            return _stage_edit(
                connection, actor, current, safe_text, safe_title, safe_sources, request_id, digest
            )
        revision = apply_revision(
            engine,
            connection,
            actor,
            current,
            safe_text,
            safe_title,
            safe_sources,
            request_id,
            digest,
            actor,
        )
        return _result(revision)


def edit_memory(
    engine: Engine,
    actor: UUID,
    scope: UUID,
    memory_id: UUID,
    expected_revision: int,
    value: str,
    request_id: UUID,
) -> dict[str, Any]:
    return _mutate(
        engine,
        actor,
        scope,
        memory_id,
        expected_revision,
        request_id,
        operation="edit",
        value=value,
    )


def rename_memory(
    engine: Engine,
    actor: UUID,
    scope: UUID,
    memory_id: UUID,
    expected_revision: int,
    title: str,
    request_id: UUID,
) -> dict[str, Any]:
    return _mutate(
        engine,
        actor,
        scope,
        memory_id,
        expected_revision,
        request_id,
        operation="rename",
        title=title,
    )


def set_memory_sources(
    engine: Engine,
    actor: UUID,
    scope: UUID,
    memory_id: UUID,
    expected_revision: int,
    sources: list[dict[str, Any]],
    request_id: UUID,
) -> dict[str, Any]:
    return _mutate(
        engine,
        actor,
        scope,
        memory_id,
        expected_revision,
        request_id,
        operation="sources",
        sources=sources,
    )


def migrate_memory_sources(
    engine: Engine,
    actor: UUID,
    scope: UUID,
    memory_id: UUID,
    expected_revision: int,
    source_text: str,
    source_title: str,
    request_id: UUID,
) -> dict[str, Any]:
    return _mutate(
        engine,
        actor,
        scope,
        memory_id,
        expected_revision,
        request_id,
        operation="migrate_sources",
        source_text=source_text,
        source_title=source_title,
    )


def delete_memory(
    engine: Engine, actor: UUID, scope: UUID, memory_id: UUID, expected_revision: int
) -> dict[str, Any]:
    """Soft-delete one visible memory by revocation; retained history stays auditable."""
    if not 1 <= expected_revision <= 2147483647:
        raise ValueError("memory.invalid_input")
    with engine.begin() as connection:
        _require(connection, actor, scope, "memory_delete")
        row = (
            connection.execute(
                text(
                    "SELECT id, revision FROM memories WHERE scope_id=:s AND memory_id=:id "
                    "ORDER BY revision DESC LIMIT 1"
                ),
                {"s": scope, "id": memory_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("memory.not_found")
        if int(row["revision"]) != expected_revision:
            raise ValueError("memory.revision_conflict")
        revoked = connection.scalar(
            text("SELECT 1 FROM revocations WHERE kind='memory' AND target_id=:id"),
            {"id": memory_id},
        )
        if revoked:
            return {
                "memory_id": str(memory_id),
                "deleted": True,
                "already_deleted": True,
                "permanent": False,
            }
        if not memory_visible(connection, row["id"]):
            raise PermissionError("memory.not_visible")
        connection.execute(
            text(
                "INSERT INTO revocations(id,scope_id,target_id,kind,actor_id) "
                "VALUES (:id,:s,:t,'memory',:a) ON CONFLICT(kind,target_id) DO NOTHING"
            ),
            {"id": uuid4(), "s": scope, "t": memory_id, "a": actor},
        )
        audit(connection, actor, scope, memory_id, "revoke")
        return {
            "memory_id": str(memory_id),
            "deleted": True,
            "already_deleted": False,
            "permanent": False,
        }
