"""Manage an optional project-local PostgreSQL runtime on Windows.

Install .local/postgres with micromamba first. No system service or PATH edits.
The data directory, credentials and log remain ignored, inside this repository.
"""

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from shiros.config import Settings

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
BIN = LOCAL / "postgres" / "Library" / "bin"
DATA = LOCAL / "pgdata"


def command(name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [str(BIN / f"{name}.exe"), *args],
        env=env,
        check=check,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def settings() -> Settings:
    result = Settings(_env_file=ROOT / ".env")
    if result.db_host != "127.0.0.1":
        raise ValueError("This helper only manages the project-local loopback database")
    return result


def initialize() -> None:
    if not (BIN / "initdb.exe").exists():
        raise RuntimeError("Install the portable PostgreSQL runtime first; see docs/development.md")
    env_file = ROOT / ".env"
    if not env_file.exists():
        password = secrets.token_urlsafe(32)
        # Generated local credentials are never printed or placed in command arguments.
        with env_file.open("x", encoding="utf-8") as handle:
            handle.write(
                "POSTGRES_DB=shiros\nPOSTGRES_USER=shiros\nPOSTGRES_PORT=55432\n"
                f"POSTGRES_PASSWORD={password}\nSHIROS_DB_HOST=127.0.0.1\n"
                "SHIROS_DB_PORT=55432\nSHIROS_DB_NAME=shiros\nSHIROS_DB_USER=shiros\n"
                f"SHIROS_DB_PASSWORD={password}\n"
            )
    config = settings()
    config.database_url()
    if not (DATA / "PG_VERSION").exists():
        password_file = LOCAL / "init-password.tmp"
        with password_file.open("x", encoding="utf-8") as handle:
            handle.write(config.db_password.get_secret_value())
        try:
            command(
                "initdb",
                "-D",
                str(DATA),
                "-U",
                config.db_user,
                "-A",
                "scram-sha-256",
                "--pwfile",
                str(password_file),
                "--encoding=UTF8",
                "--locale=C",
            )
        finally:
            password_file.unlink(missing_ok=True)
    start(config)
    with psycopg.connect(
        host=config.db_host,
        port=config.db_port,
        user=config.db_user,
        password=config.db_password.get_secret_value(),
        dbname="postgres",
        connect_timeout=3,
        autocommit=True,
    ) as connection:
        for name in (config.db_name, config.db_name + "_test"):
            if not connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
            ).fetchone():
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    print("Local development and test databases ready")


def start(config: Settings) -> None:
    if command("pg_ctl", "-D", str(DATA), "status", check=False).returncode == 0:
        return
    command(
        "pg_ctl",
        "-D",
        str(DATA),
        "-l",
        str(LOCAL / "postgres.log"),
        "-o",
        f"-h 127.0.0.1 -p {config.db_port}",
        "-w",
        "start",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "start", "stop", "status", "test"])
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.action == "init":
        initialize()
    elif args.action == "start":
        start(settings())
    elif args.action == "stop":
        command("pg_ctl", "-D", str(DATA), "-m", "fast", "-w", "stop")
    elif args.action == "status":
        raise SystemExit(command("pg_ctl", "-D", str(DATA), "status", check=False).returncode)
    elif args.action == "test":
        config = settings()
        env = dict(os.environ)
        env["SHIROS_TEST_DATABASE_URL"] = (
            config.database_url()
            .set(
                database=config.db_name + "_test",
            )
            .render_as_string(hide_password=False)
        )
        raise SystemExit(
            subprocess.call([sys.executable, "-m", "pytest", "-m", "integration"], env=env)
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(
            "Local database operation failed; check runtime and local configuration.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
