"""Bounded L0 -> L1 -> search -> explicit exact source selection."""

from shiros.core.context import ContextBundle, ContextItem, ContextRequest
from shiros.core.retrieval import SearchRequest
from shiros.retrieval import RetrievalService


def estimated_size(item: ContextItem) -> int:
    """Conservative UTF-8 byte estimate of content AND provenance/selection metadata.

    This is not a model-specific tokenizer; bundle envelope metadata is excluded.
    """
    return len(item.model_dump_json().encode("utf-8"))


class SharedContextCompiler:
    def __init__(self, retrieval: RetrievalService) -> None:
        self.retrieval = retrieval

    async def compile(self, request: ContextRequest) -> ContextBundle:
        scopes = tuple(
            dict.fromkeys(
                (*request.scope_ids, *((request.project_id,) if request.project_id else ()))
            )
        )
        scopes = tuple(self.retrieval.allowed_scopes(request.actor_id, scopes))
        if not scopes:
            return ContextBundle(token_count=0, token_budget=request.token_budget)
        search = SearchRequest(
            scope_ids=scopes,
            query=request.task,
            entity_ids=request.related_entities,
            domain=request.domain,
            limit=16,
        )
        candidates: list[ContextItem] = []
        stages = []
        for layer in ("snapshot", "summary"):
            stages.append(layer)
            for index, hit in enumerate(
                await self.retrieval.layers(request.actor_id, search, layer), 1
            ):
                candidates.append(
                    ContextItem(
                        text=hit.record.text,
                        provenance=hit.record.provenance,
                        record_id=hit.record.id,
                        memory_id=hit.memory_id,
                        layer=layer,
                        rank=index,
                        score=hit.score,
                    )
                )
        stages.append("search")
        for search_hit in await self.retrieval.search(request.actor_id, search):
            candidates.append(
                ContextItem(
                    text=search_hit.memory.text,
                    provenance=search_hit.memory.provenance,
                    record_id=search_hit.memory.id,
                    memory_id=search_hit.memory.memory_id,
                    layer="memory",
                    rank=search_hit.rank,
                    score=search_hit.score,
                )
            )
        stages.append("exact")
        for source_id in request.exact_source_ids[:16]:
            for scope in scopes:
                source = await self.retrieval.source(request.actor_id, scope, source_id)
                if source:
                    candidates.append(
                        ContextItem(
                            text=source.text,
                            provenance=source.provenance,
                            record_id=source.id,
                            layer="source",
                            score=1,
                        )
                    )
                    break
        selected = []
        seen_ids = set()
        seen_text = set()
        used, duplicates = 0, 0
        trimmed = False
        for item in candidates:
            identity = item.memory_id or item.record_id
            canonical = " ".join(item.text.split())
            if identity in seen_ids or canonical in seen_text:
                duplicates += 1
                continue
            remaining = request.token_budget - used
            if estimated_size(item) > remaining:
                trimmed = True
                low, high = 0, len(item.text)
                while low < high:
                    middle = (low + high + 1) // 2
                    trial = item.model_copy(update={"text": item.text[:middle], "truncated": True})
                    if estimated_size(trial) <= remaining:
                        low = middle
                    else:
                        high = middle - 1
                if low == 0:
                    continue
                item = item.model_copy(update={"text": item.text[:low], "truncated": True})
            selected.append(item)
            used += estimated_size(item)
            seen_ids.add(identity)
            seen_text.add(canonical)
        return ContextBundle(
            items=tuple(selected),
            token_count=used,
            token_budget=request.token_budget,
            trimmed=trimmed,
            deduplicated=duplicates,
            stages=tuple(stages),
        )
