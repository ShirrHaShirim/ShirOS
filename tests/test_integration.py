"""Explicit integration command fails without a configured disposable test DB."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text

from shiros.adapters.database.connection import check_database, make_engine
from shiros.core.context import ContextBundle
from shiros.core.conversations import SendMessageRequest
from shiros.core.jobs import RunRequest
from shiros.services import build_services

pytestmark = pytest.mark.integration


async def test_postgres_migrate_vector_and_services() -> None:
    raw_url = os.environ.get("SHIROS_TEST_DATABASE_URL")
    assert raw_url, "Set SHIROS_TEST_DATABASE_URL to a dedicated database ending in _test"
    url = make_url(raw_url)
    assert url.database and url.database.endswith("_test"), "Integration requires a _test database"
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            config = Config("alembic.ini")
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.upgrade(config, "head")
        assert check_database(engine).ready
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT '[1,2,3]'::vector <-> '[1,2,3]'::vector")) == 0
            assert connection.scalar(text("SHOW timezone")) == "UTC"
            transaction = connection.begin_nested()
            connection.execute(
                text(
                    "INSERT INTO system_metadata (id, schema_version, created_at) "
                    "VALUES (:id, 1, now())"
                ),
                {"id": uuid4()},
            )
            transaction.rollback()
        services = build_services()
        conversation = await services.conversations.create_conversation("mock-echo-v1")
        reply = await services.conversations.send_message(
            SendMessageRequest(
                conversation_id=conversation.id,
                request_id=uuid4(),
                content="integration",
            )
        )
        assert reply.content == "Mock: integration"
        result = await services.worker.run(
            RunRequest(
                job_id=uuid4(),
                task="integration",
                context=ContextBundle(token_count=0),
            )
        )
        assert result.status == "completed"
    finally:
        engine.dispose()
