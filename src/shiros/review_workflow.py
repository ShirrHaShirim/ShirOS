"""Local human review boundary. Staging and approval are separate explicit actions."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from shiros.adapters.database.records import lock_writes, row_data
from shiros.auto_review import approve_new_candidate, review_reason
from shiros.core.ingestion import MemoryWrite
from shiros.core.memory import Memory
from shiros.identity import DatabasePermissions, LocalIdentity, audit
from shiros.intake import (
    MemoryCandidate,
    SourcePreview,
    content_hash,
    preview_content,
    preview_file,
)
from shiros.shared_memory import build_shared_memory
from shiros.visibility import memory_visible, visible_memory


class ReviewWorkflow:
    def __init__(self, engine: Engine, identity: LocalIdentity, import_root: Path) -> None:
        self.engine, self.identity, self.import_root = engine, identity, import_root
        self._previews: dict[UUID, tuple[UUID, UUID, SourcePreview]] = {}

    def _actor(self, connection: Connection, scope: UUID, *actions: str) -> UUID:
        actor = self.identity.current(connection)
        permissions = DatabasePermissions(connection)
        for action in actions:
            permissions.require(actor, scope, action)
        return actor

    def require_admin(self, scope: UUID) -> None:
        with self.engine.connect() as connection:
            self._actor(connection, scope, "admin")

    def require_backup_admin(self, scope: UUID) -> None:
        # Full-database exports must not be granted by ownership of one unrelated scope.
        with self.engine.connect() as connection:
            actor = self._actor(connection, scope, "admin")
            if not connection.scalar(
                text(
                    "SELECT 1 FROM review_audit WHERE action='bootstrap' "
                    "AND actor_id=:actor AND scope_id=:scope"
                ),
                {"actor": actor, "scope": scope},
            ):
                raise PermissionError("permission.denied")

    def preview(self, scope: UUID, filename: str) -> SourcePreview:
        with self.engine.connect() as connection:
            actor = self._actor(connection, scope, "read", "review")
        result = preview_file(self.import_root, filename)
        # Raw previews are transient, bounded, and never logged or persisted.
        if len(self._previews) >= 8:
            self._previews.pop(next(iter(self._previews)))
        self._previews[result.id] = actor, scope, result
        return result

    def preview_text(self, scope: UUID, filename: str, content: str) -> SourcePreview:
        with self.engine.connect() as connection:
            actor = self._actor(connection, scope, "read", "review")
        result = preview_content(filename, content)
        if len(self._previews) >= 8:
            self._previews.pop(next(iter(self._previews)))
        self._previews[result.id] = actor, scope, result
        return result

    def _candidate(self, connection: Connection, scope: UUID, candidate: UUID) -> MemoryCandidate:
        row = (
            connection.execute(
                text(
                    "SELECT c.*, r.observed_at, r.privacy_state, r.sensitivity, "
                    "s.text, s.status, s.revision, s.actor_id AS reviewer_id, "
                    "s.reason_code AS review_reason, "
                    "s.proposed_title,s.proposed_sources, "
                    "s.memory_revision_id FROM memory_candidates c "
                    "JOIN review_sources r ON r.id=c.source_id JOIN LATERAL "
                    "(SELECT * FROM candidate_states WHERE candidate_id=c.id "
                    "ORDER BY revision DESC LIMIT 1) s ON true WHERE c.id=:id AND c.scope_id=:scope"
                ),
                {"id": candidate, "scope": scope},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("candidate.not_found")
        keys = MemoryCandidate.model_fields
        return MemoryCandidate.model_validate(
            {
                **{key: value for key, value in row.items() if key in keys},
                "suggested_fact_level": row["fact_level"],
            }
        )

    def _active(self, connection: Connection, candidate: MemoryCandidate) -> None:
        if candidate.target_memory_id is not None:
            target = connection.scalar(
                text(
                    "SELECT id FROM memories WHERE memory_id=:id AND scope_id=:s "
                    "ORDER BY revision DESC LIMIT 1"
                ),
                {"id": candidate.target_memory_id, "s": candidate.scope_id},
            )
            if target is None or not memory_visible(connection, target):
                raise PermissionError("memory.not_visible")
        if connection.scalar(
            text("SELECT 1 FROM revocations WHERE kind='intake_source' AND target_id=:id"),
            {"id": candidate.source_id},
        ):
            raise PermissionError("source.revoked")
        if candidate.memory_revision_id and not memory_visible(
            connection, candidate.memory_revision_id
        ):
            raise PermissionError("source.revoked")

    def _state(
        self,
        connection: Connection,
        candidate: MemoryCandidate,
        actor: UUID,
        status: str,
        reason: str,
        value: str,
        memory: UUID | None = None,
        title: str | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> MemoryCandidate:
        connection.execute(
            text(
                "INSERT INTO candidate_states(id,candidate_id,revision,status,text,actor_id,"
                "reason_code,memory_revision_id,proposed_title,proposed_sources) "
                "VALUES (:id,:candidate,:revision,:status,:text,:actor,:reason,:memory,"
                ":title,CAST(:sources AS jsonb))"
            ),
            {
                "id": uuid4(),
                "candidate": candidate.id,
                "revision": candidate.revision + 1,
                "status": status,
                "text": value,
                "actor": actor,
                "reason": reason,
                "memory": memory,
                "title": candidate.proposed_title if title is None else title,
                "sources": json.dumps(candidate.proposed_sources if sources is None else sources),
            },
        )
        return self._candidate(connection, candidate.scope_id, candidate.id)

    def stage(self, scope: UUID, preview_id: UUID) -> MemoryCandidate:
        with self.engine.begin() as connection:
            lock_writes(connection)
            actor = self._actor(connection, scope, "write", "review")
            cached = self._previews.get(preview_id)
            if cached is None or cached[:2] != (actor, scope):
                raise PermissionError("intake.preview_required")
            preview = cached[2]
            if not preview.privacy.persistence_allowed:
                raise PermissionError("privacy.persistence_denied")
            service = build_shared_memory(self.engine, DatabasePermissions(connection)).memory
            safe, sensitivity = service.sanitize(preview.normalized_text)
            title, _ = service.sanitize(preview.title)
            if len(safe) > 20000:
                raise ValueError("intake.too_large")
            digest = content_hash(safe)
            existing = connection.scalar(
                text(
                    "SELECT c.id FROM memory_candidates c "
                    "JOIN review_sources s ON s.id=c.source_id "
                    "WHERE s.scope_id=:scope AND s.content_hash=:hash"
                ),
                {"scope": scope, "hash": digest},
            )
            if existing:
                result = self._candidate(connection, scope, existing)
                self._active(connection, result)
                return result
            source_id, candidate_id = uuid4(), uuid4()
            connection.execute(
                text(
                    "INSERT INTO review_sources(id,scope_id,kind,origin,title,text,content_hash,"
                    "observed_at,created_by,privacy_state,sensitivity) "
                    "VALUES (:id,:scope,:kind,:origin,:title,:text,:hash,:observed,"
                    ":actor,'staging_allowed',:sensitivity)"
                ),
                {
                    "id": source_id,
                    "scope": scope,
                    "kind": preview.kind,
                    "origin": preview.origin,
                    "title": title,
                    "text": safe,
                    "hash": digest,
                    "observed": preview.observed_at,
                    "actor": actor,
                    "sensitivity": sensitivity.value,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO memory_candidates(id,scope_id,source_id,original_text,fact_level,"
                    "confidence,created_by) "
                    "VALUES (:id,:scope,:source,:text,'explicit_statement',1,:actor)"
                ),
                {
                    "id": candidate_id,
                    "scope": scope,
                    "source": source_id,
                    "text": safe,
                    "actor": actor,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO candidate_states(id,candidate_id,revision,status,text,"
                    "actor_id,reason_code) "
                    "VALUES (:id,:candidate,1,'pending',:text,:actor,:reason)"
                ),
                {
                    "id": uuid4(),
                    "candidate": candidate_id,
                    "text": safe,
                    "actor": actor,
                    "reason": review_reason(
                        preview.original_text, preview.normalized_text, preview.title
                    ),
                },
            )
            audit(connection, actor, scope, candidate_id, "stage")
            approve_new_candidate(
                self.engine,
                connection,
                actor,
                scope,
                candidate_id,
                originals=(preview.original_text, preview.normalized_text, preview.title),
            )
            return self._candidate(connection, scope, candidate_id)

    def queue(self, scope: UUID) -> list[MemoryCandidate]:
        with self.engine.connect() as connection:
            self._actor(connection, scope, "read")
            ids = connection.scalars(
                text(
                    "SELECT c.id FROM memory_candidates c JOIN LATERAL "
                    "(SELECT status FROM candidate_states WHERE candidate_id=c.id "
                    "ORDER BY revision DESC LIMIT 1) s ON true WHERE c.scope_id=:scope "
                    "AND s.status IN ('pending','edited') AND (c.target_memory_id IS NULL "
                    "OR EXISTS (SELECT 1 FROM memories m WHERE m.memory_id=c.target_memory_id "
                    "AND m.scope_id=c.scope_id AND NOT EXISTS "
                    "(SELECT 1 FROM memories n WHERE n.supersedes_id=m.id) AND "
                    + visible_memory()
                    + ")) ORDER BY c.created_at LIMIT 100"
                ),
                {"scope": scope},
            ).all()
            return [self._candidate(connection, scope, item) for item in ids]

    def inspect(self, scope: UUID, candidate_id: UUID) -> dict[str, Any]:
        with self.engine.connect() as connection:
            self._actor(connection, scope, "read")
            candidate = self._candidate(connection, scope, candidate_id)
            self._active(connection, candidate)
            source = (
                connection.execute(
                    text("SELECT * FROM review_sources WHERE id=:id"), {"id": candidate.source_id}
                )
                .mappings()
                .one()
            )
            history = (
                connection.execute(
                    text("SELECT * FROM candidate_states WHERE candidate_id=:id ORDER BY revision"),
                    {"id": candidate_id},
                )
                .mappings()
                .all()
            )
            return {
                "candidate": candidate.model_dump(mode="json"),
                "source": dict(source),
                "history": [dict(row) for row in history],
            }

    def edit(
        self,
        scope: UUID,
        candidate_id: UUID,
        value: str,
        title: str | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> MemoryCandidate:
        return self._review(scope, candidate_id, "edit", value, title, sources)

    def reject(self, scope: UUID, candidate_id: UUID) -> MemoryCandidate:
        return self._review(scope, candidate_id, "reject")

    def _review(
        self,
        scope: UUID,
        candidate_id: UUID,
        action: str,
        value: str | None = None,
        title: str | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> MemoryCandidate:
        with self.engine.begin() as connection:
            lock_writes(connection)
            actor = self._actor(connection, scope, "review", "write")
            candidate = self._candidate(connection, scope, candidate_id)
            self._active(connection, candidate)
            if candidate.status not in ("pending", "edited"):
                raise ValueError("candidate.closed")
            clean = candidate.text
            if value is not None:
                clean, _ = build_shared_memory(
                    self.engine, DatabasePermissions(connection)
                ).memory.sanitize(value)
                if len(clean) > 20000:
                    raise ValueError("intake.too_large")
            if title is not None or sources is not None:
                if candidate.target_memory_id is None:
                    raise ValueError("request.invalid")
                from shiros.memory_mutations import sanitize_metadata

                title, sources = sanitize_metadata(
                    self.engine,
                    connection,
                    candidate.proposed_title if title is None else title,
                    candidate.proposed_sources if sources is None else sources,
                )
            result = self._state(
                connection,
                candidate,
                actor,
                "edited" if action == "edit" else "rejected",
                "manual_" + action,
                clean,
                title=title,
                sources=sources,
            )
            audit(connection, actor, scope, candidate_id, action)
            return result

    def approve(
        self, scope: UUID, candidate_id: UUID, expected_revision: int | None = None
    ) -> Memory:
        with self.engine.begin() as connection:
            lock_writes(connection)
            actor = self._actor(connection, scope, "review", "approve", "write")
            candidate = self._candidate(connection, scope, candidate_id)
            self._active(connection, candidate)
            if expected_revision is not None and candidate.revision != expected_revision:
                raise ValueError("candidate.revision_conflict")
            if candidate.status == "approved":
                row = (
                    connection.execute(
                        text("SELECT m.* FROM memories m WHERE m.id=:id AND " + visible_memory()),
                        {"id": candidate.memory_revision_id},
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    raise PermissionError("source.revoked")
                return Memory.model_validate(row_data(dict(row)))
            if candidate.status not in ("pending", "edited"):
                raise ValueError("candidate.closed")
            if candidate.target_memory_id is not None:
                from shiros.adapters.database.records import PostgresMemoryRepository
                from shiros.memory_mutations import apply_revision, sanitize_metadata

                current = asyncio.run(
                    PostgresMemoryRepository(connection).get(candidate.target_memory_id)
                )
                if current is None or current.scope_id != scope:
                    raise ValueError("memory.not_found")
                if not memory_visible(connection, current.id):
                    raise PermissionError("memory.not_visible")
                if current.revision != candidate.expected_memory_revision:
                    raise ValueError("memory.revision_conflict")
                clean_title, clean_sources = sanitize_metadata(
                    self.engine, connection, candidate.proposed_title, candidate.proposed_sources
                )
                memory = apply_revision(
                    self.engine,
                    connection,
                    candidate.created_by,
                    current,
                    candidate.text,
                    clean_title,
                    clean_sources,
                    candidate.id,
                    content_hash(str(candidate.id)),
                    actor,
                )
                self._state(
                    connection,
                    candidate,
                    actor,
                    "approved",
                    "manual_approve",
                    candidate.text,
                    memory.id,
                )
                audit(connection, actor, scope, candidate.id, "approve")
                return memory
            source = (
                connection.execute(
                    text("SELECT * FROM review_sources WHERE id=:id"), {"id": candidate.source_id}
                )
                .mappings()
                .one()
            )
            request = MemoryWrite(
                scope_id=scope,
                idempotency_key=candidate.id,
                source_kind="note",
                source_title=source["title"],
                source_text=source["text"],
                text=candidate.text,
                entity_title=source["title"],
                observed_at=source["observed_at"],
                confidence=candidate.confidence,
                level=candidate.suggested_fact_level,
            )
            services = build_shared_memory(self.engine, DatabasePermissions(connection))
            approval = services.reviews.approve(actor, actor, request)
            memory = asyncio.run(services.memory._ingest(actor, request, approval, connection))
            connection.execute(
                text(
                    "INSERT INTO review_source_links VALUES (:intake,:source) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"intake": candidate.source_id, "source": memory.provenance.source_id},
            )
            self._state(
                connection,
                candidate,
                actor,
                "approved",
                "manual_approve",
                candidate.text,
                memory.id,
            )
            audit(connection, actor, scope, candidate_id, "approve")
            return memory

    def memory(self, scope: UUID, memory_id: UUID) -> Memory | None:
        with self.engine.connect() as connection:
            self._actor(connection, scope, "read")
            row = (
                connection.execute(
                    text(
                        "SELECT m.* FROM memories m "
                        "WHERE m.scope_id=:scope AND m.memory_id=:id AND "
                        + visible_memory()
                        + " ORDER BY m.revision DESC LIMIT 1"
                    ),
                    {"scope": scope, "id": memory_id},
                )
                .mappings()
                .first()
            )
            return Memory.model_validate(row_data(dict(row))) if row else None

    def hide(self, scope: UUID, memory_id: UUID, hidden: bool) -> None:
        with self.engine.begin() as connection:
            lock_writes(connection)
            actor = self._actor(connection, scope, "revoke")
            if not connection.scalar(
                text("SELECT 1 FROM memories WHERE scope_id=:scope AND memory_id=:id LIMIT 1"),
                {"scope": scope, "id": memory_id},
            ):
                raise ValueError("memory.not_found")
            connection.execute(
                text(
                    "INSERT INTO memory_visibility(scope_id,memory_id,hidden,actor_id) "
                    "VALUES (:s,:m,:h,:a)"
                ),
                {"s": scope, "m": memory_id, "h": hidden, "a": actor},
            )
            audit(connection, actor, scope, memory_id, "hide" if hidden else "show")

    def revoke(self, scope: UUID, target: UUID, kind: str = "intake_source") -> None:
        with self.engine.begin() as connection:
            lock_writes(connection)
            actor = self._actor(connection, scope, "revoke")
            table, column = {
                "intake_source": ("review_sources", "id"),
                "source": ("sources", "id"),
                "memory": ("memories", "memory_id"),
            }.get(kind, ("", ""))
            if not table:
                raise ValueError("source.invalid_kind")
            if not connection.scalar(
                text(f"SELECT 1 FROM {table} WHERE {column}=:id AND scope_id=:scope LIMIT 1"),
                {"id": target, "scope": scope},
            ):
                raise ValueError("source.not_found")
            targets = {(kind, target)}
            intake_ids = [target] if kind == "intake_source" else []
            if kind == "source":
                intake_ids = list(
                    connection.scalars(
                        text(
                            "SELECT intake_source_id FROM review_source_links WHERE source_id=:id"
                        ),
                        {"id": target},
                    )
                )
            for intake_id in intake_ids:
                targets.add(("intake_source", intake_id))
                targets.update(
                    ("source", item)
                    for item in connection.scalars(
                        text(
                            "SELECT source_id FROM review_source_links WHERE intake_source_id=:id"
                        ),
                        {"id": intake_id},
                    )
                )
                candidate_id = connection.scalar(
                    text("SELECT id FROM memory_candidates WHERE source_id=:id"), {"id": intake_id}
                )
                if candidate_id:
                    candidate = self._candidate(connection, scope, candidate_id)
                    if candidate.status != "invalidated":
                        self._state(
                            connection,
                            candidate,
                            actor,
                            "invalidated",
                            "source_revoked",
                            candidate.text,
                        )
            # Revoke the stable memory root as well, so later revisions cannot resurrect it.
            for target_kind, target_id in tuple(targets):
                if target_kind == "source":
                    targets.update(
                        ("memory", item)
                        for item in connection.scalars(
                            text(
                                "SELECT DISTINCT memory_id FROM memories "
                                "WHERE source_id=:id AND scope_id=:scope"
                            ),
                            {"id": target_id, "scope": scope},
                        )
                    )
            for target_kind, target_id in targets:
                connection.execute(
                    text(
                        "INSERT INTO revocations(id,scope_id,target_id,kind,actor_id) "
                        "VALUES (:id,:scope,:target,:kind,:actor) "
                        "ON CONFLICT(kind,target_id) DO NOTHING"
                    ),
                    {
                        "id": uuid4(),
                        "scope": scope,
                        "target": target_id,
                        "kind": target_kind,
                        "actor": actor,
                    },
                )
            audit(connection, actor, scope, target, "revoke")
