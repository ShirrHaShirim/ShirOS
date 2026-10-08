"""Short connection timeout, UTC session and sanitized readiness result."""

from sqlalchemy import URL, Engine, create_engine, text

from shiros.core.schemas import Schema

EXPECTED_REVISION = "0013_file_audit"


class DatabaseHealth(Schema):
    ready: bool
    vector_version: str | None = None
    migration: str | None = None


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
        return DatabaseHealth(
            ready=bool(vector and revision == EXPECTED_REVISION),
            vector_version=vector,
            migration=revision,
        )
