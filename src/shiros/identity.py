"""Trusted local OS identity; never take actor or review state from a client."""

import csv
import ctypes
import io
import os
import subprocess
from collections.abc import Callable
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from shiros.adapters.database.records import lock_writes
from shiros.core.permissions import AccessRequest


class Role(StrEnum):
    OWNER = "owner"
    REVIEWER = "reviewer"
    READER = "reader"
    WORKER = "worker"
    PROPOSER = "proposer"
    EDITOR = "editor"


RIGHTS = {
    Role.OWNER: frozenset(
        {
            "read",
            "review",
            "approve",
            "write",
            "revoke",
            "admin",
            "execute",
            "memory_edit",
            "memory_delete",
            "tag_manage",
            "music_write",
        }
    ),
    Role.REVIEWER: frozenset({"read", "review", "approve", "write"}),
    Role.READER: frozenset({"read"}),
    Role.WORKER: frozenset({"read", "write", "execute"}),
    Role.PROPOSER: frozenset({"read", "propose"}),
    Role.EDITOR: frozenset({"read", "propose", "memory_edit", "memory_delete", "tag_manage"}),
}


def os_principal() -> str:
    if os.name == "nt":
        # whoami reads the process token, unlike spoofable USERNAME environment variables.
        buffer = ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer)):
            raise PermissionError("identity.invalid_principal")
        result = subprocess.run(
            [buffer.value + r"\whoami.exe", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            check=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        sid = next(csv.reader(io.StringIO(result.stdout)))[-1]
        if not sid.startswith("S-1-"):
            raise PermissionError("identity.invalid_principal")
        return "windows:" + sid
    return f"unix:{os.getuid()}"  # type: ignore[attr-defined]


class DatabasePermissions:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def has(self, actor: UUID, scope: UUID, action: str) -> bool:
        role = self.connection.execute(
            text("SELECT role FROM local_grants WHERE actor_id=:actor AND scope_id=:scope"),
            {"actor": actor, "scope": scope},
        ).scalar_one_or_none()
        return role is not None and action in RIGHTS[Role(role)]

    def require(self, actor: UUID, scope: UUID, action: str) -> None:
        if not self.has(actor, scope, action):
            raise PermissionError("permission.denied")

    def allows(self, request: AccessRequest) -> bool:
        action = {"persist": "write", "project": "read"}.get(request.action, request.action)
        return self.has(request.actor_id, request.resource_id, action)


def audit(connection: Connection, actor: UUID, scope: UUID, subject: UUID, action: str) -> None:
    connection.execute(
        text(
            "INSERT INTO review_audit(id,scope_id,actor_id,subject_id,action) "
            "VALUES (:id,:scope,:actor,:subject,:action)"
        ),
        {"id": uuid4(), "scope": scope, "actor": actor, "subject": subject, "action": action},
    )


class LocalIdentity:
    """Resolver injection is for trusted composition/tests only, never a CLI option."""

    def __init__(self, engine: Engine, resolver: Callable[[], str] = os_principal) -> None:
        self.engine = engine
        self.resolver = resolver

    def current(self, connection: Connection) -> UUID:
        result = connection.execute(
            text("SELECT id FROM local_identities WHERE principal=:principal"),
            {"principal": self.resolver()},
        ).scalar_one_or_none()
        if result is None:
            raise PermissionError("identity.not_initialized")
        return UUID(str(result))

    def bootstrap(self) -> UUID:
        with self.engine.begin() as connection:
            lock_writes(connection)
            count = connection.scalar(text("SELECT count(*) FROM local_identities"))
            if count:
                actor = self.current(connection)
                scope = connection.execute(
                    text(
                        "SELECT scope_id FROM local_grants "
                        "WHERE actor_id=:actor AND role='owner' LIMIT 1"
                    ),
                    {"actor": actor},
                ).scalar_one_or_none()
                if scope is None:
                    raise PermissionError("permission.denied")
                return UUID(str(scope))
            actor, scope = uuid4(), uuid4()
            connection.execute(
                text("INSERT INTO local_identities(id,principal) VALUES (:id,:p)"),
                {"id": actor, "p": self.resolver()},
            )
            connection.execute(
                text("INSERT INTO local_grants VALUES (:a,:s,'owner')"), {"a": actor, "s": scope}
            )
            audit(connection, actor, scope, actor, "bootstrap")
            return scope

    def grant(self, scope: UUID, principal: str, role: Role) -> UUID:
        # Only operating-system identity handles are accepted, never names or credentials.
        if not (principal.startswith("windows:S-1-") or principal.startswith("unix:")):
            raise ValueError("identity.invalid_principal")
        with self.engine.begin() as connection:
            lock_writes(connection)
            owner = self.current(connection)
            DatabasePermissions(connection).require(owner, scope, "admin")
            actor: UUID = connection.execute(
                text(
                    "INSERT INTO local_identities(id,principal) VALUES (:id,:p) "
                    "ON CONFLICT(principal) DO UPDATE SET principal=EXCLUDED.principal RETURNING id"
                ),
                {"id": uuid4(), "p": principal},
            ).scalar_one()
            if actor == owner and role != Role.OWNER:
                raise PermissionError("identity.cannot_demote_self")
            connection.execute(
                text(
                    "INSERT INTO local_grants VALUES (:a,:s,:r) ON CONFLICT(actor_id,scope_id) "
                    "DO UPDATE SET role=EXCLUDED.role"
                ),
                {"a": actor, "s": scope, "r": role.value},
            )
            audit(connection, owner, scope, actor, "grant")
            return UUID(str(actor))
