"""Database tests use only the explicitly configured dedicated synthetic test database."""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, make_url

from shiros.adapters.database.connection import make_engine


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    raw = os.environ.get("SHIROS_TEST_DATABASE_URL")
    assert raw, "Set SHIROS_TEST_DATABASE_URL to a dedicated _test database"
    url = make_url(raw)
    assert url.database and url.database.endswith("_test")
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
