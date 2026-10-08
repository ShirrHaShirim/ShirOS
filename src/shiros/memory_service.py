"""Shared Memory write boundary: review, privacy, lineage, atomic state and events."""

import asyncio
import hashlib
import math
import unicodedata
from contextlib import nullcontext
from uuid import UUID, uuid4

from pgvector import Vector
from sqlalchemy import Connection, Engine, text

from shiros.adapters.database.records import (
    PostgresEntityRepository,
    PostgresEventStore,
    PostgresMemoryRepository,
    get_record,
    insert_record,
    lock_writes,
    next_sequence,
)
from shiros.adapters.providers import EmbeddingProvider
from shiros.adapters.providers.memory_mock import DIMENSIONS, MODEL
from shiros.core.events import Event
from shiros.core.ingestion import InferenceWrite, MemoryWrite
from shiros.core.memory import Memory
from shiros.core.permissions import AccessRequest, PermissionService
from shiros.core.privacy import PrivacyInput, PrivacyPolicy, Sensitivity
from shiros.core.records import (
    Artifact,
    EntityRecord,
    Inference,
    PersistenceMetadata,
    Relation,
    Source,
)
from shiros.core.schemas import EvidenceLevel, Provenance, utc_now
from shiros.review import ReviewAuthority, fingerprint
from shiros.visibility import memory_visible


def content_fingerprint(request: MemoryWrite | InferenceWrite) -> str:
    """Only sanitized input reaches this persistent digest; transport keys are excluded."""
    return fingerprint(request.model_copy(update={"idempotency_key": UUID(int=0)}))


class MemoryService:
    def __init__(
        self,
        engine: Engine,
        permissions: PermissionService,
        privacy: PrivacyPolicy,
        reviews: ReviewAuthority,
        embeddings: EmbeddingProvider,
    ) -> None:
        self.engine = engine
        self.permissions = permissions
        self.privacy = privacy
        self.reviews = reviews
        self.embeddings = embeddings

    def require(self, actor_id: UUID, scope_id: UUID, action: str = "persist") -> None:
        request = AccessRequest.model_validate(
            {
                "actor_id": actor_id,
                "resource_id": scope_id,
                "action": action,
            }
        )
        if not self.permissions.allows(request):
            raise PermissionError("permission.denied")

    def sanitize(self, value: str) -> tuple[str, Sensitivity]:
        # Check original as well as normalized text, before any persistent fingerprint.
        original = self.privacy.evaluate(PrivacyInput(text=value, reviewed=True))
        normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").strip()
        decision = self.privacy.evaluate(PrivacyInput(text=normalized, reviewed=True))
        if (
            not original.persistence_allowed
            or not decision.persistence_allowed
            or decision.safe_text is None
        ):
            raise PermissionError("privacy.persistence_denied")
        if not decision.safe_text:
            raise ValueError("memory.empty_content")
        return decision.safe_text, decision.sensitivity

    async def ingest(self, actor_id: UUID, request: MemoryWrite, approval_id: UUID) -> Memory:
        # Synchronous SQLAlchemy transactions run off the caller's event loop.
        return await asyncio.to_thread(
            lambda: asyncio.run(self._ingest(actor_id, request, approval_id))
        )

    async def _ingest(
        self,
        actor_id: UUID,
        request: MemoryWrite,
        approval_id: UUID,
        connection: Connection | None = None,
    ) -> Memory:
        self.require(actor_id, request.scope_id)
        approval = self.reviews.validate(approval_id, actor_id, request)
        updates: dict[str, str] = {}
        sensitivities = []
        for field in ("source_title", "source_text", "text", "entity_title"):
            updates[field], sensitivity = self.sanitize(getattr(request, field))
            sensitivities.append(sensitivity)
        safe = MemoryWrite.model_validate({**request.model_dump(), **updates})
        metadata = PersistenceMetadata(
            sensitivity=Sensitivity.PERSONAL
            if Sensitivity.PERSONAL in sensitivities
            else Sensitivity.ORDINARY,
            reviewed_by=approval.reviewer_id,
        )
        digest = content_fingerprint(safe)
        transaction = nullcontext(connection) if connection is not None else self.engine.begin()
        with transaction as active_connection:
            assert active_connection is not None
            connection = active_connection
            lock_writes(connection)
            receipt = (
                connection.execute(
                    text(
                        "SELECT input_hash, result_id FROM memory_receipts "
                        "WHERE scope_id=:scope AND actor_id=:actor AND idempotency_key=:key"
                    ),
                    {"scope": safe.scope_id, "actor": actor_id, "key": safe.idempotency_key},
                )
                .mappings()
                .first()
            )
            repository = PostgresMemoryRepository(connection)
            if receipt:
                if receipt["input_hash"] != digest:
                    raise ValueError("memory.idempotency_conflict")
                if not memory_visible(connection, receipt["result_id"]):
                    raise PermissionError("memory.not_visible")
                result = await repository.revision(receipt["result_id"])
                assert result is not None
                return result
            duplicate = connection.execute(
                text(
                    "SELECT result_id FROM memory_receipts WHERE scope_id=:scope "
                    "AND actor_id=:actor AND input_hash=:digest LIMIT 1"
                ),
                {"scope": safe.scope_id, "actor": actor_id, "digest": digest},
            ).scalar_one_or_none()
            if duplicate:
                if not memory_visible(connection, duplicate):
                    raise PermissionError("memory.not_visible")
                connection.execute(
                    text(
                        "INSERT INTO memory_receipts "
                        "(scope_id,actor_id,idempotency_key,input_hash,result_id) "
                        "VALUES (:scope,:actor,:key,:digest,:result)"
                    ),
                    {
                        "scope": safe.scope_id,
                        "actor": actor_id,
                        "key": safe.idempotency_key,
                        "digest": digest,
                        "result": duplicate,
                    },
                )
                result = await repository.revision(duplicate)
                assert result is not None
                return result
            previous = await repository.get(safe.memory_id) if safe.memory_id else None
            if safe.memory_id:
                if previous is None or previous.scope_id != safe.scope_id:
                    raise ValueError("memory.not_found")
                if not memory_visible(connection, previous.id):
                    raise PermissionError("memory.not_visible")
                if previous.revision != safe.expected_revision:
                    raise ValueError("memory.revision_conflict")
                if (
                    safe.entity_id not in (None, previous.entity_id)
                    or safe.domain != previous.domain
                ):
                    raise ValueError("memory.identity_conflict")
                # Existing inference-like evidence cannot silently become a verified fact.
                if (
                    previous.provenance.level
                    in (
                        EvidenceLevel.INFERENCE,
                        EvidenceLevel.HYPOTHESIS,
                        EvidenceLevel.MODEL_GUESS,
                    )
                    and safe.level != previous.provenance.level
                ):
                    raise ValueError("memory.evidence_promotion_requires_new_reviewed_record")
            source_id = uuid4()
            provenance = Provenance(
                source_id=source_id,
                observed_at=safe.observed_at,
                created_by=str(actor_id),
                level=safe.level,
                confidence=safe.confidence,
                verified=False,
            )
            source = Source(
                id=source_id,
                scope_id=safe.scope_id,
                provenance=provenance,
                privacy=metadata,
                kind=safe.source_kind,
                title=safe.source_title,
                text=safe.source_text,
            )
            insert_record(connection, "sources", source)
            artifact = Artifact(
                scope_id=safe.scope_id,
                provenance=provenance,
                privacy=metadata,
                text=safe.source_text,
                sha256=hashlib.sha256(safe.source_text.encode("utf-8")).hexdigest(),
            )
            insert_record(connection, "artifacts", artifact)
            entities = PostgresEntityRepository(connection)
            entity_id = previous.entity_id if previous else safe.entity_id
            if entity_id:
                entity = await entities.get(entity_id)
                if entity is None or entity.scope_id != safe.scope_id:
                    raise ValueError("entity.not_found")
                if not previous and (
                    entity.title != safe.entity_title or entity.kind != safe.entity_kind
                ):
                    raise ValueError("entity.identity_conflict")
            else:
                entity = EntityRecord(
                    scope_id=safe.scope_id,
                    provenance=provenance,
                    privacy=metadata,
                    title=safe.entity_title,
                    kind=safe.entity_kind,
                )
                await entities.save(entity)
            for related_id in set(safe.related_entity_ids):
                related = await entities.get(related_id)
                if related is None or related.scope_id != safe.scope_id:
                    raise ValueError("entity.related_not_found")
                insert_record(
                    connection,
                    "relations",
                    Relation(
                        scope_id=safe.scope_id,
                        provenance=provenance,
                        privacy=metadata,
                        from_entity_id=entity.id,
                        to_entity_id=related_id,
                        predicate="related_to",
                    ),
                )
            memory = Memory(
                memory_id=previous.memory_id if previous else uuid4(),
                entity_id=entity.id,
                revision=previous.revision + 1 if previous else 1,
                supersedes_id=previous.id if previous else None,
                scope_id=safe.scope_id,
                provenance=provenance,
                privacy=metadata,
                domain=safe.domain,
                text=safe.text,
            )
            await repository.save(memory)
            await self.persist_embedding(connection, memory)
            await self.append_event(
                connection, memory, "memory.revised" if previous else "memory.created"
            )
            connection.execute(
                text(
                    "INSERT INTO memory_receipts "
                    "(scope_id,actor_id,idempotency_key,input_hash,result_id) "
                    "VALUES (:scope,:actor,:key,:digest,:result)"
                ),
                {
                    "scope": safe.scope_id,
                    "actor": actor_id,
                    "key": safe.idempotency_key,
                    "digest": digest,
                    "result": memory.id,
                },
            )
            return memory

    async def persist_embedding(self, connection: Connection, memory: Memory) -> None:
        clean, _ = self.sanitize(memory.text)
        if clean != memory.text:
            raise PermissionError("privacy.unsanitized_embedding")
        results = await self.embeddings.embed([clean])
        if len(results) != 1:
            raise ValueError("embedding.invalid_count")
        result = results[0]
        if (
            result.model != MODEL
            or result.dimensions != DIMENSIONS
            or len(result.values) != DIMENSIONS
            or not all(math.isfinite(v) for v in result.values)
            or sum(v * v for v in result.values) == 0
        ):
            raise ValueError("embedding.invalid_vector")
        connection.execute(
            text(
                "INSERT INTO memory_embeddings (memory_revision_id,model,dimensions,embedding) "
                "VALUES (:id,:model,:dimensions,CAST(:vector AS vector))"
            ),
            {
                "id": memory.id,
                "model": result.model,
                "dimensions": result.dimensions,
                "vector": Vector(list(result.values)).to_text(),
            },
        )

    async def append_event(
        self,
        connection: Connection,
        memory: Memory,
        event_type: str,
        record_id: UUID | None = None,
        provenance: Provenance | None = None,
    ) -> None:
        event = Event(
            scope_id=memory.scope_id,
            provenance=provenance or memory.provenance,
            privacy=memory.privacy,
            entity_id=memory.entity_id,
            record_id=record_id or memory.id,
            event_type=event_type,
            sequence=next_sequence(connection),
        )
        await PostgresEventStore(connection).append(event)

    async def infer(self, actor_id: UUID, request: InferenceWrite, approval_id: UUID) -> Inference:
        return await asyncio.to_thread(
            lambda: asyncio.run(self._infer(actor_id, request, approval_id))
        )

    async def _infer(self, actor_id: UUID, request: InferenceWrite, approval_id: UUID) -> Inference:
        self.require(actor_id, request.scope_id)
        self.require(actor_id, request.scope_id, "read")
        approval = self.reviews.validate(approval_id, actor_id, request)
        clean, sensitivity = self.sanitize(request.text)
        safe = request.model_copy(
            update={
                "text": clean,
                "evidence_revision_ids": tuple(sorted(set(request.evidence_revision_ids), key=str)),
            }
        )
        with self.engine.begin() as connection:
            lock_writes(connection)
            receipt = (
                connection.execute(
                    text(
                        "SELECT input_hash,result_id FROM inference_receipts "
                        "WHERE scope_id=:scope AND actor_id=:actor AND idempotency_key=:key"
                    ),
                    {"scope": safe.scope_id, "actor": actor_id, "key": safe.idempotency_key},
                )
                .mappings()
                .first()
            )
            if receipt:
                if receipt["input_hash"] != fingerprint(safe):
                    raise ValueError("memory.idempotency_conflict")
                evidence_ids: list[UUID] = list(connection.execute(
                    text(
                        "SELECT memory_revision_id FROM inference_evidence "
                        "WHERE inference_id=:id ORDER BY memory_revision_id"
                    ),
                    {"id": receipt["result_id"]},
                ).scalars().all())
                if not evidence_ids or any(
                    not memory_visible(connection, item_id) for item_id in evidence_ids
                ):
                    raise PermissionError("inference.evidence_denied")
                data = get_record(connection, "inferences", receipt["result_id"])
                assert data is not None
                return Inference.model_validate(
                    {**data, "evidence_revision_ids": safe.evidence_revision_ids}
                )
            evidence = []
            for revision_id in dict.fromkeys(safe.evidence_revision_ids):
                item = await PostgresMemoryRepository(connection).revision(revision_id)
                if (
                    item is None
                    or item.scope_id != safe.scope_id
                    or not memory_visible(connection, revision_id)
                ):
                    raise PermissionError("inference.evidence_denied")
                self.sanitize(item.text)
                evidence.append(item)
            first = evidence[0]
            provenance = Provenance(
                source_id=first.provenance.source_id,
                observed_at=utc_now(),
                created_by=str(actor_id),
                level=EvidenceLevel.INFERENCE,
                confidence=safe.confidence,
                verified=False,
            )
            result = Inference(
                scope_id=safe.scope_id,
                provenance=provenance,
                privacy=PersistenceMetadata(
                    sensitivity=sensitivity, reviewed_by=approval.reviewer_id
                ),
                text=clean,
                evidence_revision_ids=tuple(item.id for item in evidence),
            )
            insert_record(connection, "inferences", result)
            for item in evidence:
                connection.execute(
                    text(
                        "INSERT INTO inference_evidence (inference_id,memory_revision_id,scope_id) "
                        "VALUES (:id,:memory,:scope)"
                    ),
                    {"id": result.id, "memory": item.id, "scope": result.scope_id},
                )
            await self.append_event(connection, first, "inference.created", result.id, provenance)
            connection.execute(
                text(
                    "INSERT INTO inference_receipts "
                    "(scope_id,actor_id,idempotency_key,input_hash,result_id) "
                    "VALUES (:scope,:actor,:key,:digest,:result)"
                ),
                {
                    "scope": safe.scope_id,
                    "actor": actor_id,
                    "key": safe.idempotency_key,
                    "digest": fingerprint(safe),
                    "result": result.id,
                },
            )
            return result
