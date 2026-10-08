"""Integration coverage for custom-format backup and isolated restore."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import Engine, make_url, text

from shiros.adapters.database.connection import EXPECTED_REVISION
from shiros.backup import BackupManager
from shiros.config import Settings

pytestmark = pytest.mark.integration


def test_backup_restore_preserves_synthetic_history(db_engine: Engine, tmp_path: Path) -> None:
    raw = os.environ["SHIROS_TEST_DATABASE_URL"]
    url = make_url(raw)
    assert url.database and url.database.endswith("_test")
    settings = Settings(
        _env_file=None,
        db_host=url.host or "127.0.0.1",
        db_port=url.port or 5432,
        db_name=url.database,
        db_user=url.username or "shiros",
        db_password=url.password or "",
    )
    manager = BackupManager(settings, tmp_path)
    suffix = uuid4().hex
    restore_name = f"shiros_restore_{suffix}_test"
    source_id, scope_id, actor_id, entity_id, memory_id, event_id = (uuid4() for _ in range(6))
    second_id = uuid4()
    now = datetime.now(UTC)
    base = {
        "id": source_id,
        "scope_id": scope_id,
        "schema_version": 1,
        "created_at": now,
        "source_id": source_id,
        "observed_at": now,
        "created_by": actor_id,
        "fact_level": "fact",
        "verified": True,
        "confidence": 1.0,
        "sensitivity": "ordinary",
        "confidentiality": "standard",
        "persistence_allowed": True,
        "reviewed_by": actor_id,
        "policy_version": "rules-v1",
    }
    with db_engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO sources
                (id, scope_id, schema_version, created_at, source_id, observed_at, created_by,
                 fact_level, verified, confidence, sensitivity, confidentiality,
                 persistence_allowed, reviewed_by, policy_version, kind, title, text)
                VALUES (:id,:scope_id,:schema_version,:created_at,:source_id,:observed_at,
                 :created_by,:fact_level,:verified,:confidence,:sensitivity,:confidentiality,
                 :persistence_allowed,:reviewed_by,:policy_version,'synthetic',:title,:body)"""),
            {**base, "title": f"Backup fixture {suffix}", "body": f"Synthetic {suffix}"},
        )
        connection.execute(
            text("""INSERT INTO entities
                (id, scope_id, schema_version, created_at, source_id, observed_at, created_by,
                 fact_level, verified, confidence, sensitivity, confidentiality,
                 persistence_allowed, reviewed_by, policy_version, kind, title)
                VALUES (:id,:scope_id,:schema_version,:created_at,:source_id,:observed_at,
                 :created_by,:fact_level,:verified,:confidence,:sensitivity,:confidentiality,
                 :persistence_allowed,:reviewed_by,:policy_version,'synthetic',:title)"""),
            {**base, "id": entity_id, "title": f"Entity {suffix}"},
        )
        connection.execute(
            text("""INSERT INTO memories
                (id, scope_id, schema_version, created_at, source_id, observed_at, created_by,
                 fact_level, verified, confidence, sensitivity, confidentiality,
                 persistence_allowed, reviewed_by, policy_version, memory_id, entity_id,
                 revision, supersedes_id, domain, text)
                VALUES (:id,:scope_id,:schema_version,:created_at,:source_id,:observed_at,
                 :created_by,:fact_level,:verified,:confidence,:sensitivity,:confidentiality,
                 :persistence_allowed,:reviewed_by,:policy_version,:memory_id,:entity_id,
                 1,NULL,'backup-test',:body)"""),
            {
                **base,
                "id": memory_id,
                "memory_id": memory_id,
                "entity_id": entity_id,
                "body": f"History {suffix}",
            },
        )
        connection.execute(
            text("""INSERT INTO events
                (id, scope_id, schema_version, created_at, source_id, observed_at, created_by,
                 fact_level, verified, confidence, sensitivity, confidentiality,
                 persistence_allowed, reviewed_by, policy_version, entity_id, record_id,
                 event_type, sequence)
                VALUES (:id,:scope_id,:schema_version,:created_at,:source_id,:observed_at,
                 :created_by,:fact_level,:verified,:confidence,:sensitivity,:confidentiality,
                 :persistence_allowed,:reviewed_by,:policy_version,:entity_id,:record_id,
                 'memory.created', (SELECT value + 1 FROM event_clock WHERE id=1))"""),
            {**base, "id": event_id, "entity_id": entity_id, "record_id": memory_id},
        )
        connection.execute(text("UPDATE event_clock SET value=value+1 WHERE id=1"))
        connection.execute(
            text("""INSERT INTO memories
            (id,scope_id,schema_version,created_at,source_id,observed_at,created_by,
             fact_level,verified,confidence,sensitivity,confidentiality,persistence_allowed,
             reviewed_by,policy_version,memory_id,entity_id,revision,supersedes_id,domain,text)
            SELECT :next,scope_id,schema_version,created_at,source_id,observed_at,created_by,
             fact_level,verified,confidence,sensitivity,confidentiality,persistence_allowed,
             reviewed_by,policy_version,memory_id,entity_id,2,id,domain,'Synthetic second revision'
            FROM memories WHERE id=:id"""),
            {"next": second_id, "id": memory_id},
        )
        step_three_counts = {
            table: connection.scalar(text(f"SELECT count(*) FROM {table}"))
            for table in ("candidate_states", "review_audit", "revocations", "local_grants")
        }

    created = False
    try:
        archive = manager.create_backup()
        manifest = json.loads(archive.with_suffix(".json").read_text(encoding="utf-8"))
        assert manifest["migration_revision"] == EXPECTED_REVISION
        assert "password" not in json.dumps(manifest).lower()
        manager.restore(archive, restore_name)
        created = True
        with pytest.raises(ValueError, match="already exists"):
            manager.restore(archive, restore_name)
        target_url = settings.database_url().set(database=restore_name)
        with psycopg.connect(
            host=target_url.host,
            port=target_url.port,
            user=target_url.username,
            password=target_url.password,
            dbname=restore_name,
        ) as restored:
            revision = restored.execute("SELECT version_num FROM alembic_version").fetchone()
            memory = restored.execute(
                "SELECT text FROM memories WHERE id=%s", (memory_id,)
            ).fetchone()
            event = restored.execute(
                "SELECT sequence FROM events WHERE id=%s", (event_id,)
            ).fetchone()
            source = restored.execute(
                "SELECT title FROM sources WHERE id=%s", (source_id,)
            ).fetchone()
            assert revision is not None and revision[0] == EXPECTED_REVISION
            assert memory is not None and memory[0] == f"History {suffix}"
            assert event is not None and event[0] > 0
            assert source is not None and source[0] == f"Backup fixture {suffix}"
            history = restored.execute(
                "SELECT id,revision,supersedes_id,source_id,created_by,reviewed_by,observed_at "
                "FROM memories WHERE memory_id=%s ORDER BY revision",
                (memory_id,),
            ).fetchall()
            assert history == [
                (memory_id, 1, None, source_id, actor_id, actor_id, now),
                (second_id, 2, memory_id, source_id, actor_id, actor_id, now),
            ]
            for table, expected in step_three_counts.items():
                assert restored.execute(
                    sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                ).fetchone() == (expected,)
        invalid_name = f"shiros_restore_invalid_{suffix}_test"
        for replacement, message in (
            ({"sha256": "0" * 64}, "checksum mismatch"),
            ({"server_version": "99.0"}, "major version"),
            ({"migration_revision": "wrong"}, "schema"),
        ):
            archive.with_suffix(".json").write_text(
                json.dumps({**manifest, **replacement}), encoding="utf-8"
            )
            with pytest.raises(ValueError, match=message):
                manager.restore(archive, invalid_name)
        archive.with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
        with db_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": invalid_name}
                )
                is None
            )
        with pytest.raises(ValueError):
            manager.restore(archive, settings.db_name)
        outside = tmp_path / "outside"
        outside.mkdir()
        with pytest.raises(ValueError):
            manager.restore(outside / archive.name, invalid_name)
    finally:
        if created:
            admin_url = settings.database_url().set(database="postgres")
            with psycopg.connect(
                host=admin_url.host,
                port=admin_url.port,
                user=admin_url.username,
                password=admin_url.password,
                dbname="postgres",
                autocommit=True,
            ) as admin:
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(restore_name)))
