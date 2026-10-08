"""Internal PostgreSQL backup and restore operations for an administrator boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import URL

from shiros.adapters.database.connection import EXPECTED_REVISION
from shiros.config import Settings

_FORMAT = 1
_RESTORE_NAME = re.compile(r"shiros_restore_[a-zA-Z0-9_]+_test\Z")


class BackupManager:
    """Create verified custom-format backups and restore to new, dedicated test DBs."""

    def __init__(self, settings: Settings, output_root: Path) -> None:
        self.settings = settings
        self.output_root = output_root.resolve()
        self.url = settings.database_url()

    def create_backup(self) -> Path:
        self.output_root.mkdir(parents=True, exist_ok=True)
        engine_url = self.url
        with psycopg.connect(
            host=engine_url.host,
            port=engine_url.port,
            user=engine_url.username,
            password=engine_url.password,
            dbname=engine_url.database,
            connect_timeout=3,
        ) as connection:
            revision_cursor = connection.execute("SELECT version_num FROM alembic_version")
            revision_row = revision_cursor.fetchone()
            if revision_row is None:
                raise ValueError("Database schema is not compatible with this application")
            revision_value = cast(tuple[str, ...], revision_row)[0]
            if revision_value != EXPECTED_REVISION:
                raise ValueError("Database schema is not compatible with this application")
            server_row = connection.execute("SHOW server_version").fetchone()
            if server_row is None:
                raise RuntimeError("PostgreSQL server version unavailable")
            server_version = cast(tuple[str, ...], server_row)[0]

        stem = f"shiros-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
        archive = self.output_root / f"{stem}.dump"
        manifest_path = self.output_root / f"{stem}.json"
        env = self._password_env()
        try:
            self._run(
                "pg_dump",
                [
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--file",
                    str(archive),
                    *self._connection_args(self.url),
                ],
                env,
            )
            digest = _sha256(archive)
            manifest = {
                "format": _FORMAT,
                "migration_revision": revision_value,
                "server_version": server_version,
                "sha256": digest,
            }
            with manifest_path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest, sort_keys=True) + "\n")
        except Exception:
            archive.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            raise
        return archive

    def restore(self, archive: Path, database_name: str) -> None:
        if len(database_name) > 63 or not _RESTORE_NAME.fullmatch(database_name):
            raise ValueError("Restore database must be a new shiros_restore_*_test database")
        archive = _checked_backup_path(archive, self.output_root, ".dump")
        if not archive.is_file():
            raise ValueError("Invalid backup archive")
        manifest_path = _checked_backup_path(
            archive.with_suffix(".json"), self.output_root, ".json"
        )
        if not manifest_path.is_file():
            raise ValueError("Invalid backup manifest")
        manifest = _read_manifest(manifest_path)
        if manifest.get("format") != _FORMAT:
            raise ValueError("Unsupported backup manifest format")
        if manifest.get("migration_revision") != EXPECTED_REVISION:
            raise ValueError("Backup schema is not compatible with this application")
        backup_version = manifest.get("server_version")
        backup_major = _server_major(backup_version)
        if backup_major is None:
            raise ValueError("Invalid backup server version")
        expected_hash = manifest.get("sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError("Invalid backup checksum")
        if _sha256(archive) != expected_hash:
            raise ValueError("Backup checksum mismatch")

        admin_url = self.url.set(database="postgres")
        with psycopg.connect(
            host=admin_url.host,
            port=admin_url.port,
            user=admin_url.username,
            password=admin_url.password,
            dbname="postgres",
            connect_timeout=3,
            autocommit=True,
        ) as connection:
            server_row = connection.execute("SHOW server_version").fetchone()
            if server_row is None or _server_major(server_row[0]) != backup_major:
                raise ValueError("Backup PostgreSQL major version is incompatible")
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (database_name,)
            ).fetchone()
            if exists:
                raise ValueError("Restore database already exists")
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))

        target_url = self.url.set(database=database_name)
        try:
            self._run(
                "pg_restore",
                [
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    "--dbname",
                    _url_without_password(target_url),
                    str(archive),
                ],
                self._password_env(),
            )
            with psycopg.connect(
                host=target_url.host,
                port=target_url.port,
                user=target_url.username,
                password=target_url.password,
                dbname=database_name,
                connect_timeout=3,
            ) as connection:
                revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
                if revision is None or revision[0] != EXPECTED_REVISION:
                    raise ValueError("Restored database schema is not compatible")
        except Exception:
            self._drop_owned_restore(database_name)
            raise

    def _password_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PGPASSWORD"] = self.settings.db_password.get_secret_value()
        return env

    def _run(self, binary: str, args: list[str], env: dict[str, str]) -> None:
        executable = _postgres_binary(binary)
        result = subprocess.run(
            [str(executable), *args],
            env=env,
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode:
            raise RuntimeError(f"{binary} failed with exit code {result.returncode}")

    def _connection_args(self, url: URL) -> list[str]:
        if not url.host or not url.database or not url.username:
            raise ValueError("A complete PostgreSQL URL is required")
        return [
            "--host",
            url.host,
            "--port",
            str(url.port or 5432),
            "--username",
            url.username,
            "--dbname",
            url.database,
        ]

    def _drop_owned_restore(self, database_name: str) -> None:
        if not _RESTORE_NAME.fullmatch(database_name):
            return
        admin_url = self.url.set(database="postgres")
        with psycopg.connect(
            host=admin_url.host,
            port=admin_url.port,
            user=admin_url.username,
            password=admin_url.password,
            dbname="postgres",
            connect_timeout=3,
            autocommit=True,
        ) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def _postgres_binary(name: str) -> Path:
    if os.name == "nt":
        bundled = (
            Path(__file__).resolve().parents[2]
            / ".local"
            / "postgres"
            / "Library"
            / "bin"
            / f"{name}.exe"
        )
        if bundled.is_file():
            return bundled
    found = shutil.which(name)
    if found:
        return Path(found)
    raise FileNotFoundError(f"PostgreSQL utility {name} was not found")


def _checked_backup_path(path: Path, output_root: Path, suffix: str) -> Path:
    candidate = path.absolute()
    if candidate.suffix != suffix or candidate.parent.resolve() != output_root:
        raise ValueError("Backup files must be direct children of the configured output root")
    if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
        raise ValueError("Backup files cannot be symlinks or junctions")
    resolved = candidate.resolve(strict=True)
    if resolved.parent != output_root or resolved != candidate:
        raise ValueError("Backup files must be regular files in the configured output root")
    return resolved


def _server_major(version: object) -> str | None:
    if not isinstance(version, str):
        return None
    match = re.match(r"^(\d+)(?:\.(\d+))?(?=[.\s]|$)", version)
    if match is None:
        return None
    major, minor = int(match[1]), match[2]
    if major < 10:
        return f"{major}.{minor}" if minor is not None else None
    return str(major)


def _url_without_password(url: URL) -> str:
    return url.set(drivername="postgresql", password=None).render_as_string(hide_password=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid backup manifest") from exc
    if not isinstance(value, dict):
        raise ValueError("Invalid backup manifest")
    return value
