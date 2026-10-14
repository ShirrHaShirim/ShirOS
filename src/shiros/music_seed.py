"""Explicit music-only snapshots for independently owned desktop test installations.

No general sources, entities, histories, credentials or memory tables are exported.
Imported provenance and event order are newly created by the recipient installation.
The packaging path exports the catalogue only: the owner's reviews, library state and
saved playback links are omitted.
"""

import base64
import gzip
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import UUID as SQLUUID
from sqlalchemy import Date, DateTime, Engine, MetaData, Table, text

from shiros.adapters.database.records import insert_record, lock_writes
from shiros.core.records import EntityRecord
from shiros.domains.music import _event, _provenance
from shiros.domains.music_profiles import identity_id

FORMAT = "shiros-test-music-1"
# Column allowlists intentionally omit exporter identity, scope, source and event IDs.
COLUMNS = {
    "music_object_records": "id kind title revision created_at updated_at image_id archived",
    "music_images": "id content sha256 media_type width height created_at",
    "music_works": "id catalogue parent_id",
    "music_recordings": "id recorded_on location",
    "music_releases": "id description release_year catalogue barcode status douban_id "
    "douban_status douban_rating douban_comment marked_at",
    "music_tracks": "id release_id disc position duration_seconds",
    "music_relations": "subject_id object_id role",
    "music_relation_details": "subject_id object_id role started_on ended_on source_text "
    "performance_role",
    "music_profiles": "id data",
    "music_taxonomy": "id namespace name parent_id revision created_at deleted "
    "derived_only music_class",
    "music_taxonomy_links": "object_id namespace node_id",
    "music_reviews": "id object_id rating comment origin created_at rating_max "
    "original_at import_key",
    "music_memberships": "id object_id library active reason position created_at",
    "music_playback_sources": "id object_id url platform created_at",
}
LOCAL_PATH = re.compile(r"(?i)(?:(?<![a-z0-9])[a-z]:[\\/]|file://|\\\\)[^\s\"<>]*")


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, (date, datetime, UUID)):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, str):
        return LOCAL_PATH.sub("[local file]", value)
    return value


def export_music_seed(
    engine: Engine, scope: UUID, path: Path, include_personal: bool = True
) -> dict[str, Any]:
    """Snapshot the scope's catalogue. include_personal=False drops the owner's
    reviews, library/wishlist state and saved playback links, keeping only the
    objective catalogue."""
    tables: dict[str, list[dict[str, Any]]] = {}
    personal_only = {"music_reviews", "music_memberships", "music_playback_sources"}
    # One consistent, read-only snapshot of the live catalogue.
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        for name, columns in COLUMNS.items():
            if not include_personal and name in personal_only:
                tables[name] = []
                continue
            projection = ",".join("t." + col for col in columns.split())
            if name == "music_object_records":
                where = "t.scope_id=:s AND NOT t.archived"
            elif name == "music_images":
                where = "t.scope_id=:s AND t.id IN "
                where += "(SELECT image_id FROM music_objects WHERE scope_id=:s)"
            elif name == "music_taxonomy":
                where = "t.scope_id=:s"
            elif name in ("music_relations", "music_relation_details"):
                where = "t.scope_id=:s AND t.subject_id IN "
                where += "(SELECT id FROM music_objects WHERE scope_id=:s) AND t.object_id IN "
                where += "(SELECT id FROM music_objects WHERE scope_id=:s)"
            elif name in (
                "music_works",
                "music_recordings",
                "music_releases",
                "music_tracks",
                "music_profiles",
            ):
                where = "t.id IN (SELECT id FROM music_objects WHERE scope_id=:s)"
            else:
                where = "t.scope_id=:s AND t.object_id IN "
                where += "(SELECT id FROM music_objects WHERE scope_id=:s)"
                if name == "music_reviews":
                    where += " AND NOT EXISTS (SELECT 1 FROM music_review_deletions d "
                    where += "WHERE d.review_id=t.id)"
            order = " ORDER BY t.sequence" if name in ("music_reviews", "music_memberships") else ""
            rows = [
                dict(r)
                for r in c.execute(
                    text(f"SELECT {projection} FROM {name} t WHERE {where}{order}"), {"s": scope}
                ).mappings()
            ]
            if name == "music_taxonomy":
                for row in rows:
                    if row["namespace"] == "identity" and row["id"] == identity_id(
                        scope, row["name"]
                    ):
                        row["preset_identity"] = True
            tables[name] = clean(rows)
    payload = {"format": FORMAT, "tables": tables, "counts": {k: len(v) for k, v in tables.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    return payload["counts"]


def import_music_seed(engine: Engine, actor: UUID, scope: UUID, path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    if payload.get("format") != FORMAT or set(payload.get("tables", {})) != set(COLUMNS):
        raise ValueError("desktop.invalid_music_seed")
    tables = payload["tables"]
    for name, rows in tables.items():
        allowed = set(COLUMNS[name].split()) | (
            {"preset_identity"} if name == "music_taxonomy" else set()
        )
        if any(set(row) - allowed for row in rows):
            raise ValueError("desktop.invalid_music_seed")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with engine.begin() as c:
        lock_writes(c)
        c.execute(
            text(
                "CREATE TABLE IF NOT EXISTS desktop_seed_receipts (scope_id uuid PRIMARY KEY, "
                "sha256 char(64) NOT NULL, imported_at timestamptz NOT NULL DEFAULT now())"
            )
        )
        receipt = c.scalar(
            text("SELECT sha256 FROM desktop_seed_receipts WHERE scope_id=:s"), {"s": scope}
        )
        if receipt:
            return {"status": "already_initialized", "sha256": receipt}
        if c.scalar(
            text("SELECT count(*) FROM music_object_records WHERE scope_id=:s"), {"s": scope}
        ):
            return {"status": "existing_catalogue_preserved"}
        p, privacy = _provenance(
            c,
            actor,
            scope,
            "Bundled test music catalogue",
            "Music catalogue supplied by the owner for testing. No private memory, original "
            "account identity, reviews, listening state or saved playback links were imported.",
        )
        metadata = MetaData()
        definitions = {name: Table(name, metadata, autoload_with=c) for name in COLUMNS}
        mapping = {
            row["id"]: str(uuid5(scope, "test-seed:" + row["id"]))
            for rows in tables.values()
            for row in rows
            if "id" in row
        }
        mapping.update(
            {
                row["id"]: str(identity_id(scope, row["name"]))
                for row in tables["music_taxonomy"]
                if row.get("preset_identity")
            }
        )

        def remap(value: Any) -> Any:
            if isinstance(value, dict):
                return {mapping.get(k, k): remap(v) for k, v in value.items()}
            if isinstance(value, list):
                return [remap(v) for v in value]
            return mapping.get(value, value) if isinstance(value, str) else value

        def rows_for(name: str) -> list[dict[str, Any]]:
            result = []
            for original in tables[name]:
                row = {k: remap(v) for k, v in original.items() if k != "preset_identity"}
                definition = definitions[name]
                if "scope_id" in definition.c:
                    row["scope_id"] = scope
                if "created_by" in definition.c:
                    row["created_by"] = actor
                if "source_id" in definition.c:
                    row["source_id"] = p.source_id
                if name == "music_images":
                    row["filename"] = row["sha256"] + ".webp"
                    row["content"] = base64.b64decode(row["content"], validate=True)
                    if hashlib.sha256(row["content"]).hexdigest() != row["sha256"]:
                        raise ValueError("desktop.invalid_seed_image")
                if "sequence" in definition.c:
                    row["sequence"] = _event(c, actor, scope, UUID(row["object_id"]), p, privacy)
                # Reflected SQLAlchemy types retain proper dates, UUIDs and JSON values.
                for column in definition.c:
                    value = row.get(column.name)
                    if value is None or not isinstance(value, str):
                        continue
                    if isinstance(column.type, SQLUUID):
                        row[column.name] = UUID(value)
                    elif isinstance(column.type, DateTime):
                        row[column.name] = datetime.fromisoformat(value)
                    elif isinstance(column.type, Date):
                        row[column.name] = date.fromisoformat(value)
                result.append(row)
            return result

        for row in tables["music_object_records"]:
            insert_record(
                c,
                "entities",
                EntityRecord(
                    id=UUID(mapping[row["id"]]),
                    scope_id=scope,
                    kind="music." + row["kind"],
                    title=row["title"],
                    provenance=p,
                    privacy=privacy,
                ),
            )
        order = [
            "music_images",
            "music_object_records",
            "music_taxonomy",
            "music_works",
            "music_recordings",
            "music_releases",
            "music_tracks",
            "music_profiles",
            "music_relations",
            "music_relation_details",
            "music_taxonomy_links",
            "music_reviews",
            "music_memberships",
            "music_playback_sources",
        ]
        for name in order:
            rows = rows_for(name)
            if name in ("music_taxonomy", "music_works"):
                inserted: set[UUID] = set()
                while rows:
                    ready = [
                        r for r in rows if r.get("parent_id") is None or r["parent_id"] in inserted
                    ]
                    if not ready:
                        raise ValueError("desktop.invalid_seed_hierarchy")
                    c.execute(definitions[name].insert(), ready)
                    inserted.update(r["id"] for r in ready)
                    rows = [r for r in rows if r not in ready]
            elif rows:
                c.execute(definitions[name].insert(), rows)
        # A bulk-loaded catalogue has no planner statistics yet. Populate them before
        # its first relational search, rather than waiting for background autovacuum.
        for name in (*COLUMNS, "entities", "events", "sources"):
            c.execute(text("ANALYZE " + name))
        c.execute(
            text("INSERT INTO music_taxonomy_bootstrap VALUES (:s) ON CONFLICT DO NOTHING"),
            {"s": scope},
        )
        c.execute(
            text("INSERT INTO desktop_seed_receipts(scope_id,sha256) VALUES (:s,:h)"),
            {"s": scope, "h": digest},
        )
    return {"status": "imported", "counts": payload["counts"], "sha256": digest}
