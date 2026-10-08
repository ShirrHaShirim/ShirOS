"""Environment URL or caller-owned transaction; no credentials in ini files."""

from alembic import context
from sqlalchemy import Connection

from shiros.adapters.database.connection import make_engine
from shiros.config import Settings

config = context.config


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        literal_binds=True,
        target_metadata=None,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    provided = config.attributes.get("connection")
    if provided is not None:
        run(provided)
    else:
        engine = make_engine(Settings().database_url())
        try:
            with engine.connect() as connection:
                run(connection)
        finally:
            engine.dispose()
