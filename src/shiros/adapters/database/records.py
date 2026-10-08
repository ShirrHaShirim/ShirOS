"""Transaction-bound SQL repositories. Only application services own write access."""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from shiros.core.events import Event
from shiros.core.memory import Memory
from shiros.core.privacy import PrivacyInput, RuleBasedPrivacyPolicy
from shiros.core.records import EntityRecord, PersistentRecord
from shiros.core.schemas import Schema

TABLES = frozenset(
    {
        "sources",
        "entities",
        "artifacts",
        "relations",
        "memories",
        "events",
        "summaries",
        "snapshots",
        "inferences",
        "checkpoints",
    }
)
METADATA_KEYS = (
    "source_id",
    "observed_at",
    "created_by",
    "fact_level",
    "verified",
    "confidence",
    "sensitivity",
    "confidentiality",
    "persistence_allowed",
    "reviewed_by",
    "policy_version",
)


def lock_writes(connection: Connection) -> None:
    # Transaction-scoped global ordering prevents commit inversion and skipped checkpoints.
    connection.execute(text("SELECT pg_advisory_xact_lock(73194021)"))


def next_sequence(connection: Connection) -> int:
    return int(
        connection.execute(
            text("UPDATE event_clock SET value = value + 1 WHERE id = 1 RETURNING value")
        ).scalar_one()
    )


def row_data(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    if "source_id" not in data:
        return data
    data["provenance"] = {
        "source_id": data["source_id"],
        "observed_at": data["observed_at"],
        "created_by": str(data["created_by"]),
        "level": data["fact_level"],
        "verified": data["verified"],
        "confidence": data["confidence"],
    }
    data["privacy"] = {
        key: data[key]
        for key in (
            "sensitivity",
            "confidentiality",
            "persistence_allowed",
            "reviewed_by",
            "policy_version",
        )
    }
    for key in METADATA_KEYS:
        del data[key]
    return data


def get_record(connection: Connection, table: str, record_id: UUID) -> dict[str, Any] | None:
    if table not in TABLES:
        raise ValueError("storage.invalid_table")
    row = (
        connection.execute(text(f"SELECT * FROM {table} WHERE id=:id"), {"id": record_id})
        .mappings()
        .first()
    )
    return row_data(dict(row)) if row else None


def insert_record(connection: Connection, table: str, record: Schema) -> None:
    if table not in TABLES:
        raise ValueError("storage.invalid_table")
    data = record.model_dump(mode="python")
    data.pop("evidence_revision_ids", None)
    if isinstance(record, (PersistentRecord, EntityRecord)):
        # Defense in depth: never accept credential text even through an internal repository.
        policy = RuleBasedPrivacyPolicy()
        for key in ("text", "title", "kind", "domain"):
            if key in data:
                value = str(data[key])
                decision = policy.evaluate(PrivacyInput(text=value, reviewed=True))
                if not decision.persistence_allowed or decision.safe_text != value:
                    raise PermissionError("privacy.unsafe_repository_write")
        source_id = record.provenance.source_id
        if table != "sources":
            source = connection.execute(
                text(
                    "SELECT 1 FROM sources WHERE id=:id AND scope_id=:scope "
                    "AND persistence_allowed AND confidentiality='standard'"
                ),
                {"id": source_id, "scope": record.scope_id},
            ).first()
            if source is None:
                raise PermissionError("privacy.unapproved_source")
        provenance = data.pop("provenance")
        data.update(provenance)
        data.pop("schema_version", None)
        data["schema_version"] = record.schema_version
        data["fact_level"] = data.pop("level")
        data["created_by"] = UUID(data["created_by"])
        privacy = data.pop("privacy")
        privacy.pop("schema_version", None)
        data.update(privacy)
    columns = list(data)
    connection.execute(
        text(
            f"INSERT INTO {table} ({','.join(columns)}) "
            f"VALUES ({','.join(':' + c for c in columns)})"
        ),
        data,
    )


class PostgresEntityRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    async def get(self, entity_id: UUID) -> EntityRecord | None:
        row = get_record(self.connection, "entities", entity_id)
        return EntityRecord.model_validate(row) if row else None

    async def save(self, entity: EntityRecord) -> None:
        insert_record(self.connection, "entities", entity)


class PostgresMemoryRepository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    async def get(self, memory_id: UUID) -> Memory | None:
        row = (
            self.connection.execute(
                text("SELECT * FROM memories WHERE memory_id=:id ORDER BY revision DESC LIMIT 1"),
                {"id": memory_id},
            )
            .mappings()
            .first()
        )
        return Memory.model_validate(row_data(dict(row))) if row else None

    async def revision(self, revision_id: UUID) -> Memory | None:
        row = get_record(self.connection, "memories", revision_id)
        return Memory.model_validate(row) if row else None

    async def save(self, memory: Memory) -> None:
        insert_record(self.connection, "memories", memory)

    async def search(self, query: str, limit: int = 10) -> list[Memory]:
        """Internal bounded keyword query. Public retrieval additionally enforces permissions."""
        rows = self.connection.execute(
            text(
                "SELECT m.* FROM memories m WHERE position(lower(:query) in lower(m.text)) > 0 "
                "AND NOT EXISTS (SELECT 1 FROM memories n WHERE n.supersedes_id=m.id) "
                "ORDER BY m.created_at DESC, m.id LIMIT :limit"
            ),
            {"query": query, "limit": min(max(limit, 1), 100)},
        ).mappings()
        return [Memory.model_validate(row_data(dict(row))) for row in rows]


class PostgresEventStore:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    async def append(self, event: Event) -> None:
        insert_record(self.connection, "events", event)

    async def read_after(self, sequence: int, limit: int = 100) -> list[Event]:
        rows = self.connection.execute(
            text("SELECT * FROM events WHERE sequence>:sequence ORDER BY sequence LIMIT :limit"),
            {"sequence": sequence, "limit": min(max(limit, 1), 100)},
        ).mappings()
        return [Event.model_validate(row_data(dict(row))) for row in rows]
