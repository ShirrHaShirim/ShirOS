"""Short connection timeout, UTC session and sanitized readiness result."""

from sqlalchemy import URL, Engine, create_engine, text

from shiros.core.schemas import Schema

EXPECTED_REVISION = "0031_music_catalog_sort"


class DatabaseHealth(Schema):
    ready: bool
    vector_version: str | None = None
    migration: str | None = None
    capture_schema_ready: bool = False


def make_engine(url: str | URL) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "options": "-c timezone=UTC"},
    )


def check_database(engine: Engine) -> DatabaseHealth:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        vector = connection.scalar(
            text("SELECT extversion FROM pg_extension WHERE extname='vector'")
        )
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        capture_ready = bool(
            connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' "
                    "AND table_name='capture_entries' AND column_name='archived') "
                    "AND EXISTS(SELECT 1 FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename='capture_entries' "
                    "AND indexname='capture_entries_active_message') "
                    "AND to_regclass('public.client_capability_grants') IS NOT NULL"
                )
            )
        )
        return DatabaseHealth(
            ready=bool(vector and revision == EXPECTED_REVISION and capture_ready),
            vector_version=vector,
            migration=revision,
            capture_schema_ready=capture_ready,
        )
