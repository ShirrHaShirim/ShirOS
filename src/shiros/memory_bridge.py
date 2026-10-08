"""Owner-managed MCP client profiles; secrets never go to stdout."""

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import uvicorn
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import Engine, text

from shiros.adapters.database.connection import make_engine
from shiros.adapters.database.records import lock_writes
from shiros.config import Settings
from shiros.identity import DatabasePermissions, LocalIdentity, audit, os_principal
from shiros.memory_gateway import MemoryGateway

ROOT = Path(__file__).resolve().parents[2]
PROFILE_ROOT = ROOT / ".local" / "memory-clients"
CLIENTS = ("codex", "chatgpt", "work", "deepseek", "opencode", "deepseek-harness", "antigravity")


class ClientProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: Literal[
        "codex", "chatgpt", "work", "deepseek", "opencode", "deepseek-harness", "antigravity"
    ]
    actor: UUID
    scope: UUID
    token: SecretStr


class ContextQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=4000)
    budget: int = Field(default=4000, ge=1, le=16000)


def profile_path(client: str) -> Path:
    if client not in CLIENTS:
        raise ValueError("client.invalid")
    for path in (PROFILE_ROOT, PROFILE_ROOT / f"{client}.json"):
        if path.is_symlink() or path.is_junction():
            raise PermissionError("profile.invalid")
    return PROFILE_ROOT / f"{client}.json"


def _detach_from_console() -> None:
    """Survive the operator terminal closing; console control events would kill us."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.FreeConsole()
    except (AttributeError, OSError):
        pass


def init_client(engine: Engine, identity: LocalIdentity, scope: UUID, client: str) -> ClientProfile:
    path = profile_path(client)
    PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(
            [
                "icacls.exe",
                str(PROFILE_ROOT),
                "/inheritance:r",
                "/grant:r",
                f"*{os_principal().removeprefix('windows:')}:(OI)(CI)F",
            ],
            check=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        PROFILE_ROOT.chmod(0o700)
    with engine.begin() as connection:
        lock_writes(connection)
        owner = identity.current(connection)
        DatabasePermissions(connection).require(owner, scope, "admin")
        if path.exists():
            existing = ClientProfile.model_validate_json(path.read_text(encoding="utf-8"))
            if existing.scope != scope or existing.client != client:
                raise ValueError("profile.scope_conflict")
            actor = existing.actor
        else:
            actor = uuid4()
            connection.execute(
                text("INSERT INTO local_identities(id,principal) VALUES (:a,:p)"),
                {"a": actor, "p": f"mcp:{client}:{actor}"},
            )
        principal = connection.scalar(
            text("SELECT principal FROM local_identities WHERE id=:a"), {"a": actor}
        )
        if principal != f"mcp:{client}:{actor}":
            raise PermissionError("profile.invalid_identity")
        connection.execute(
            text(
                "INSERT INTO local_grants VALUES (:a,:s,'reader') "
                "ON CONFLICT(actor_id,scope_id) DO UPDATE SET role='reader'"
            ),
            {"a": actor, "s": scope},
        )
        audit(connection, owner, scope, actor, "grant")
    token = secrets.token_urlsafe(32)
    profile = ClientProfile(client=client, actor=actor, scope=scope, token=SecretStr(token))
    payload = profile.model_dump(mode="json")
    payload["token"] = token
    path.write_text(json.dumps(payload), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description="ShirOS shared memory bridge")
    parser.add_argument("command", choices=("init", "serve", "status", "revoke", "query", "access"))
    parser.add_argument("--mode", choices=("reader", "proposer", "editor"))
    parser.add_argument("--client", choices=CLIENTS, required=True)
    parser.add_argument("--scope", type=UUID)
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    os.chdir(ROOT)
    engine = make_engine(Settings().database_url())
    try:
        identity = LocalIdentity(engine)
        if args.command == "init":
            init_client(engine, identity, args.scope or identity.bootstrap(), args.client)
            print("Client initialized as read-only. Private profile saved; token not displayed.")
            return
        profile = ClientProfile.model_validate_json(
            profile_path(args.client).read_text(encoding="utf-8")
        )
        if profile.client != args.client or len(profile.token.get_secret_value()) != 43:
            raise ValueError("profile.invalid")
        if args.command == "access":
            if args.mode is None:
                raise ValueError("access.mode_required")
            with engine.begin() as connection:
                lock_writes(connection)
                owner = identity.current(connection)
                DatabasePermissions(connection).require(owner, profile.scope, "admin")
                principal = connection.scalar(
                    text("SELECT principal FROM local_identities WHERE id=:a"),
                    {"a": profile.actor},
                )
                if principal != f"mcp:{profile.client}:{profile.actor}":
                    raise PermissionError("profile.invalid_identity")
                connection.execute(
                    text(
                        "INSERT INTO local_grants VALUES (:a,:s,:r) "
                        "ON CONFLICT(actor_id,scope_id) DO UPDATE SET role=:r"
                    ),
                    {"a": profile.actor, "s": profile.scope, "r": args.mode},
                )
                audit(connection, owner, profile.scope, profile.actor, "grant")
            print(
                f"Client access: {args.mode}. New memories require approval; "
                "editor may revise or revoke existing memories."
            )
            return
        if args.command == "revoke":
            with engine.begin() as connection:
                owner = identity.current(connection)
                DatabasePermissions(connection).require(owner, profile.scope, "admin")
                connection.execute(
                    text("DELETE FROM local_grants WHERE actor_id=:a AND scope_id=:s"),
                    {"a": profile.actor, "s": profile.scope},
                )
                audit(connection, owner, profile.scope, profile.actor, "grant")
            print("Client access revoked. Existing processes fail subsequent reads.")
            return
        gateway = MemoryGateway(engine, profile.actor, profile.scope, args.client)
        if args.command == "query":
            raw = sys.stdin.buffer.read(32769)
            if len(raw) > 32768:
                raise ValueError("request.too_large")
            query = ContextQuery.model_validate_json(raw)
            result = asyncio.run(gateway.context(query.task, query.budget))
            print(json.dumps(result, ensure_ascii=True))
            return
        if args.command == "status":
            print(json.dumps(gateway.status()))
            return
        if not 1024 <= args.port <= 65535:
            raise ValueError("port.invalid")
        from shiros.adapters.mcp.server import create_http_app, create_server

        server = create_server(gateway)
        if args.transport == "stdio":
            _detach_from_console()
            server.run(transport="stdio")
        else:
            app = create_http_app(server, profile.token.get_secret_value(), args.port)
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    except Exception:
        parser.exit(1, "ShirOS bridge failed. Check private profile, database and permissions.\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
