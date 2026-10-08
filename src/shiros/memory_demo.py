"""Repeatable synthetic end-to-end demonstration. Never imports personal documents."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine

from shiros.core.context import ContextRequest
from shiros.core.ingestion import MemoryWrite
from shiros.core.permissions import Action, ExplicitGrantPermissions
from shiros.core.retrieval import SearchRequest
from shiros.i18n import Locale, UserLocalePreference, default_catalog, format_number
from shiros.shared_memory import build_shared_memory


async def run_memory_demo(engine: Engine, locale: Locale) -> None:
    actor = UUID("2f45c146-85c4-4cda-82ea-354473b1ee28")
    scope = UUID("b36a1f97-cf87-4efd-9b23-c2f9b302564c")
    actions: tuple[Action, ...] = ("read", "persist", "review", "execute")
    services = build_shared_memory(
        engine, ExplicitGrantPermissions(frozenset((actor, action, scope) for action in actions))
    )
    request = MemoryWrite(
        scope_id=scope,
        idempotency_key=UUID("6b419634-845b-447d-a941-4e11e6e8bf56"),
        source_kind="synthetic",
        source_title="Synthetic ShirOS architecture",
        source_text="ShirOS stores approved memories with provenance and isolated conversations.",
        text="ShirOS stores approved memories with provenance and isolated conversations.",
        entity_title="ShirOS synthetic demo",
        observed_at=datetime(2026, 10, 6, tzinfo=UTC),
    )
    token = services.reviews.approve(actor, actor, request)
    memory = await services.memory.ingest(actor, request, token)
    await services.layers.process(actor, scope)
    results = await services.retrieval.search(
        actor, SearchRequest(scope_ids=(scope,), query="ShirOS")
    )
    bundle = await services.context.compile(
        ContextRequest(
            actor_id=actor,
            project_id=scope,
            task="ShirOS",
            model="mock",
            token_budget=1500,
        )
    )
    catalog, preference = default_catalog(), UserLocalePreference(locale=locale)
    print(catalog.translate("core", "memory.demo_complete", locale))
    print(f"memory_id: {memory.memory_id}")
    print(
        f"{catalog.translate('core', 'memory.results', locale)}: "
        f"{format_number(len(results), preference)}"
    )
    print(
        f"{catalog.translate('core', 'memory.context_size', locale)}: "
        f"{format_number(bundle.token_count, preference)} / 1500"
    )
    for item in bundle.items:
        print(f"{item.layer}: {item.text}")
