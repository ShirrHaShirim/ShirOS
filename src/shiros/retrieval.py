"""Permission-scoped PostgreSQL keyword, vector and hybrid retrieval."""

import asyncio
import math
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from pgvector import Vector
from sqlalchemy import text

from shiros.adapters.database.records import row_data
from shiros.adapters.providers.memory_mock import DIMENSIONS, MODEL
from shiros.core.memory import Memory
from shiros.core.permissions import AccessRequest
from shiros.core.privacy import PrivacyInput
from shiros.core.records import Snapshot, Source, Summary
from shiros.core.retrieval import SearchHit, SearchRequest
from shiros.memory_service import MemoryService
from shiros.visibility import visible_memory


@dataclass(frozen=True)
class LayerHit:
    record: Summary | Snapshot
    memory_id: UUID
    score: float


class RetrievalService:
    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory

    def allowed_scopes(self, actor_id: UUID, scope_ids: tuple[UUID, ...]) -> list[UUID]:
        return [
            scope
            for scope in dict.fromkeys(scope_ids)
            if self.memory.permissions.allows(
                AccessRequest(actor_id=actor_id, resource_id=scope, action="read")
            )
        ]

    def safe(self, value: str) -> bool:
        decision = self.memory.privacy.evaluate(PrivacyInput(text=value, reviewed=True))
        return decision.persistence_allowed and decision.safe_text == value

    async def exact(
        self,
        actor_id: UUID,
        scope_id: UUID,
        memory_id: UUID,
        *,
        revision: bool = False,
    ) -> Memory | None:
        if not self.allowed_scopes(actor_id, (scope_id,)):
            return None

        def read() -> Memory | None:
            field = "id" if revision else "memory_id"
            with self.memory.engine.connect() as connection:
                row = (
                    connection.execute(
                        text(
                            f"SELECT * FROM memories WHERE {field}=:id AND scope_id=:scope "
                            "AND persistence_allowed AND confidentiality='standard' "
                            "AND " + visible_memory("memories") + " "
                            "ORDER BY revision DESC LIMIT 1"
                        ),
                        {"id": memory_id, "scope": scope_id},
                    )
                    .mappings()
                    .first()
                )
                if row is None or not self.safe(row["text"]):
                    return None
                return Memory.model_validate(row_data(dict(row)))

        return await asyncio.to_thread(read)

    async def source(self, actor_id: UUID, scope_id: UUID, source_id: UUID) -> Source | None:
        if not self.allowed_scopes(actor_id, (scope_id,)):
            return None

        def read() -> Source | None:
            with self.memory.engine.connect() as connection:
                row = (
                    connection.execute(
                        text(
                            "SELECT * FROM sources WHERE id=:id AND scope_id=:scope "
                            "AND persistence_allowed AND confidentiality='standard' "
                            "AND NOT EXISTS (SELECT 1 FROM revocations rv WHERE "
                            "rv.scope_id=sources.scope_id AND rv.kind='source' "
                            "AND rv.target_id=sources.id) "
                            "AND EXISTS (SELECT 1 FROM memories m WHERE "
                            "m.source_id=sources.id AND m.scope_id=sources.scope_id AND "
                            + visible_memory()
                            + ")"
                        ),
                        {"id": source_id, "scope": scope_id},
                    )
                    .mappings()
                    .first()
                )
                if row is None or not self.safe(row["text"]) or not self.safe(row["title"]):
                    return None
                return Source.model_validate(row_data(dict(row)))

        return await asyncio.to_thread(read)

    async def _rows(
        self,
        actor_id: UUID,
        request: SearchRequest,
        layer: Literal["snapshot", "summary"] | None = None,
    ) -> list[dict[str, Any]]:
        scopes = self.allowed_scopes(actor_id, request.scope_ids)
        if not scopes:
            return []
        query = request.query.strip()
        if query:
            # Query embeddings are transient, but still never pass obvious secrets to providers.
            query, _ = self.memory.sanitize(query)
        mode = request.mode if query else "structured"
        params: dict[str, Any] = {
            "scopes": scopes,
            "query": query,
            "limit": request.limit,
        }
        terms = [term for term in query.split() if term][:8]
        for index, term in enumerate(terms):
            params[f"term{index}"] = term
        coverage = (
            "0.0"
            if not terms
            else "("
            + " + ".join(
                f"CASE WHEN position(lower(:term{index}) in "
                "lower(m.text || ' ' || coalesce(md.title,s.title,''))) > 0 THEN 1 ELSE 0 END"
                for index in range(len(terms))
            )
            + f")::float / {len(terms)}"
        )
        semantic = "0.0"
        if mode in ("semantic", "hybrid"):
            vectors = await self.memory.embeddings.embed([query])
            if len(vectors) != 1:
                raise ValueError("embedding.invalid_count")
            vector = vectors[0]
            if (
                vector.model != MODEL
                or vector.dimensions != DIMENSIONS
                or len(vector.values) != DIMENSIONS
                or not all(math.isfinite(v) for v in vector.values)
                or sum(v * v for v in vector.values) == 0
            ):
                raise ValueError("embedding.invalid_vector")
            params["vector"] = Vector(list(vector.values)).to_text()
            semantic = "GREATEST(0.0,1.0 - (e.embedding <=> CAST(:vector AS vector)))"
        where = [
            "m.scope_id = ANY(CAST(:scopes AS uuid[]))",
            "m.persistence_allowed",
            "m.confidentiality='standard'",
            "s.persistence_allowed",
            "s.confidentiality='standard'",
            "NOT EXISTS (SELECT 1 FROM memories n WHERE n.supersedes_id=m.id)",
            visible_memory(),
        ]
        if request.entity_ids:
            where.append("m.entity_id=ANY(CAST(:entities AS uuid[]))")
            params["entities"] = list(request.entity_ids)
        if request.source_ids:
            where.append("m.source_id=ANY(CAST(:sources AS uuid[]))")
            params["sources"] = list(request.source_ids)
        if request.domain is not None:
            where.append("m.domain=:domain")
            params["domain"] = request.domain
        if request.since:
            where.append("m.observed_at>=:since")
            params["since"] = request.since
        if request.until:
            where.append("m.observed_at<=:until")
            params["until"] = request.until
        keyword = (
            "CASE WHEN :query = '' THEN 0.0 "
            "WHEN position(lower(:query) in lower(m.text || ' ' || "
            "coalesce(md.title,s.title,''))) > 0 THEN 1.0 "
            "ELSE GREATEST(LEAST(1.0,ts_rank_cd(to_tsvector('simple',"
            "m.text || ' ' || coalesce(md.title,s.title,'')),"
            f"plainto_tsquery('simple',:query))), {coverage}) END"
        )
        score = {
            "keyword": "keyword_score",
            "semantic": "semantic_score",
            "hybrid": "0.4 * keyword_score + 0.6 * semantic_score",
            "structured": "0.0",
        }[mode]
        mode_filter = "WHERE keyword_score>0" if mode == "keyword" else ""
        selection = "SELECT * FROM ranked ORDER BY score DESC, created_at DESC, id"
        if layer is not None:
            table = "snapshots" if layer == "snapshot" else "summaries"
            selection = (
                f"SELECT l.*, r.memory_id AS stable_memory_id, r.score FROM {table} l "
                "JOIN ranked r ON r.id=l.memory_revision_id "
                "WHERE l.persistence_allowed AND l.confidentiality='standard' "
                "ORDER BY r.score DESC, r.created_at DESC, r.id"
            )
        statement = text(
            f"WITH candidates AS (SELECT m.*, {keyword} AS keyword_score, "
            f"{semantic} AS semantic_score "
            "FROM memories m JOIN sources s ON s.id=m.source_id "
            "LEFT JOIN memory_revision_metadata md ON md.revision_id=m.id "
            "AND md.scope_id=m.scope_id "
            "JOIN memory_embeddings e ON e.memory_revision_id=m.id WHERE "
            + " AND ".join(where)
            + "), ranked AS ("
            f"SELECT *, {score} AS score FROM candidates {mode_filter} "
            "ORDER BY score DESC, created_at DESC, id LIMIT :limit) " + selection
        )

        def read() -> list[dict[str, Any]]:
            with self.memory.engine.connect() as connection:
                return [dict(row) for row in connection.execute(statement, params).mappings()]

        return await asyncio.to_thread(read)

    async def search(self, actor_id: UUID, request: SearchRequest) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for values in await self._rows(actor_id, request):
            scores = {
                key: float(values.pop(key))
                for key in (
                    "score",
                    "keyword_score",
                    "semantic_score",
                )
            }
            memory = Memory.model_validate(row_data(values))
            if self.safe(memory.text):
                hits.append(
                    SearchHit(
                        memory=memory,
                        rank=len(hits) + 1,
                        method=request.mode if request.query.strip() else "structured",
                        **scores,
                    )
                )
        return hits

    async def layers(
        self,
        actor_id: UUID,
        request: SearchRequest,
        kind: Literal["snapshot", "summary"],
    ) -> list[LayerHit]:
        # Ranking runs in SQL; L0/L1 selection never returns raw memory bodies to Python.
        result = []
        for row in await self._rows(actor_id, request, kind):
            memory_id, score = row.pop("stable_memory_id"), row.pop("score")
            if self.safe(row["text"]):
                model = Snapshot if kind == "snapshot" else Summary
                result.append(
                    LayerHit(model.model_validate(row_data(row)), memory_id, float(score))
                )
        return result
