"""Run an explicit audited plan; production requires a successful isolated result."""

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text

from shiros.adapters.database.connection import make_engine
from shiros.config import Settings
from shiros.domains.music_catalog_migration import apply_plan
from shiros.identity import LocalIdentity


def rows(c: Connection, table: str) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(text(f'SELECT * FROM "{table}"')).mappings()]


def signatures(values: list[dict[str, Any]]) -> set[str]:
    def encode(value: Any) -> Any:
        if isinstance(value, (bytes, memoryview)):
            return {"sha256": hashlib.sha256(value).hexdigest()}
        return str(value)

    return {json.dumps(row, sort_keys=True, default=encode) for row in values}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--plan", type=Path, default=Path("docs/music-catalog-plan-v0717.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--isolated-result", type=Path)
    args = parser.parse_args()
    content = args.plan.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if not args.database.endswith("_test"):
        if not args.isolated_result:
            raise ValueError("A successful isolated verification is required for production")
        isolated = json.loads(args.isolated_result.read_text(encoding="utf-8"))
        assert isolated["verified"] and isolated["plan_sha256"] == digest
    engine = make_engine(Settings().database_url().set(database=args.database))
    with engine.connect() as c:
        tables: Sequence[str] = (
            c.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE' "
                    "AND table_name<>'alembic_version' ORDER BY table_name"
                )
            )
            .scalars()
            .all()
        )
        before = {table: rows(c, table) for table in tables}
        baseline = c.scalar(text("SELECT version_num FROM alembic_version"))
        actor = LocalIdentity(engine).current(c)
        scope = c.scalar(
            text("SELECT scope_id FROM local_grants WHERE actor_id=:id AND role='owner'"),
            {"id": actor},
        )
        assert scope
    with engine.connect() as c:
        config = Config("alembic.ini")
        config.attributes["connection"] = c
        command.upgrade(config, "head")
    with engine.begin() as c:
        result = apply_plan(c, actor, scope, json.loads(content))
        after = {table: rows(c, table) for table in tables}
        # All original rows survive byte-for-byte outside the explicit reference/image changes.
        exceptions = {
            "music_object_records",
            "music_relations",
            "music_relation_details",
            "event_clock",
        }
        for table in set(tables) - exceptions:
            assert signatures(before[table]) <= signatures(after[table]), table
        old_objects = {r["id"]: r for r in before["music_object_records"]}
        new_objects = {r["id"]: r for r in after["music_object_records"]}
        for identifier, old in old_objects.items():
            assert identifier in new_objects
            assert all(
                old[key] == new_objects[identifier][key]
                for key in ("id", "kind", "title", "scope_id", "created_at")
            )
        # Every removed live relationship is retained in the immutable old-ID redirect audit.
        plan = json.loads(content)
        retire_ids = {entry["id"] for entry in plan["retire"]}
        removed = signatures(before["music_relations"]) - signatures(after["music_relations"])
        assert all(str(json.loads(row)["object_id"]) in retire_ids for row in removed)
        second = apply_plan(c, actor, scope, plan)
        assert second.get("idempotent") and not second["changed"]
        for table in tables:
            assert signatures(after[table]) == signatures(rows(c, table)), "Retry changed " + table
        summary = {
            "verified": True,
            "baseline_schema": baseline,
            "schema": c.scalar(text("SELECT version_num FROM alembic_version")),
            "database": args.database,
            "plan_sha256": digest,
            "preserved_original_objects": len(old_objects),
            "preserved_original_tables": len(tables) - len(exceptions),
            "removed_live_relations": len(removed),
            "result": result,
            "retry_no_changes": True,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(summary, default=str, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "result"}))


if __name__ == "__main__":
    main()
