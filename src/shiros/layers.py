"""Incremental per-memory L0/L1 versions and checkpoints commit atomically."""

import asyncio
from uuid import UUID

from sqlalchemy import text

from shiros.adapters.database.records import PostgresMemoryRepository, insert_record, lock_writes
from shiros.adapters.providers import SummaryProvider
from shiros.core.records import Checkpoint, Snapshot, Summary
from shiros.memory_service import MemoryService
from shiros.visibility import memory_visible


class MemoryLayerProcessor:
    def __init__(self, memory: MemoryService, summaries: SummaryProvider) -> None:
        self.memory = memory
        self.summaries = summaries

    async def process(self, actor_id: UUID, scope_id: UUID, limit: int = 100) -> int:
        if not 1 <= limit <= 100:
            raise ValueError("processor.invalid_limit")
        self.memory.require(actor_id, scope_id, "execute")
        self.memory.require(actor_id, scope_id, "read")
        self.memory.require(actor_id, scope_id, "persist")
        return await asyncio.to_thread(lambda: asyncio.run(self._process(scope_id, limit)))

    async def _process(self, scope_id: UUID, limit: int) -> int:
        with self.memory.engine.begin() as connection:
            lock_writes(connection)
            last: int = connection.execute(
                text(
                    "SELECT coalesce(max(through_sequence),0) FROM checkpoints "
                    "WHERE scope_id=:scope AND processor='memory-layers-v1'"
                ),
                {"scope": scope_id},
            ).scalar_one()
            rows = list(
                connection.execute(
                    text(
                        "SELECT sequence,record_id FROM events "
                        "WHERE scope_id=:scope AND sequence>:last "
                        "AND event_type IN ('memory.created','memory.revised') "
                        "ORDER BY sequence LIMIT :limit"
                    ),
                    {"scope": scope_id, "last": last, "limit": limit},
                ).mappings()
            )
            for row in rows:
                if not memory_visible(connection, row["record_id"]):
                    continue
                memory = await PostgresMemoryRepository(connection).revision(row["record_id"])
                if memory is None:
                    raise ValueError("processor.missing_memory")
                clean, _ = self.memory.sanitize(memory.text)
                source_provenance = (memory.provenance,)
                result = await self.summaries.summarize(clean, source_provenance)
                if result.sources != source_provenance:
                    raise ValueError("summary.invalid_provenance")
                summary_text, _ = self.memory.sanitize(result.text)
                summary = Summary(
                    scope_id=scope_id,
                    provenance=memory.provenance,
                    privacy=memory.privacy,
                    memory_revision_id=memory.id,
                    version=memory.revision,
                    text=summary_text,
                    from_sequence=row["sequence"],
                    through_sequence=row["sequence"],
                )
                insert_record(connection, "summaries", summary)
                snapshot_text, _ = self.memory.sanitize(summary_text[:160])
                snapshot = Snapshot(
                    scope_id=scope_id,
                    provenance=memory.provenance,
                    privacy=memory.privacy,
                    memory_revision_id=memory.id,
                    version=memory.revision,
                    text=snapshot_text,
                    from_sequence=row["sequence"],
                    through_sequence=row["sequence"],
                    summary_id=summary.id,
                )
                insert_record(connection, "snapshots", snapshot)
                await self.memory.append_event(connection, memory, "summary.created", summary.id)
                await self.memory.append_event(connection, memory, "snapshot.created", snapshot.id)
            if rows:
                insert_record(
                    connection,
                    "checkpoints",
                    Checkpoint(
                        scope_id=scope_id,
                        through_sequence=rows[-1]["sequence"],
                    ),
                )
            return len(rows)
