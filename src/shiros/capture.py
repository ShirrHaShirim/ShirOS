"""Opt-in, conversation-scoped ingestion. Producers cannot approve or select a scope."""

import base64
import hashlib
import json
import re
import secrets
import sys
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit, urlunsplit
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, Engine, text

from shiros.adapters.database.records import lock_writes
from shiros.candidate_submission import submit
from shiros.capture_conversations import (
    PRIVACY_OMITTED,
    assemble,
    conversation_memory_title,
    merge_legacy,
)
from shiros.file_library import MAX_FILE_BYTES, FileLibrary, FileRequest, safe_filename
from shiros.identity import DatabasePermissions, audit
from shiros.intake import content_hash
from shiros.memory_limits import MAX_MEMORY_CHARS
from shiros.memory_mutations import edit_memory
from shiros.shared_memory import build_shared_memory
from shiros.visibility import memory_visible

Provider = Literal["gpt", "codex", "opencode", "deepseek", "deepseek-harness"]


class CaptureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    provider: Provider = "gpt"
    conversation_id: str = Field(default="", max_length=500)
    title: str = Field(default="", max_length=180)
    all_conversations: bool = False
    include_deleted: bool = False
    include_files: bool = False
    source_path: str | None = Field(default=None, max_length=2000)
    attachment_root: str | None = Field(default=None, max_length=2000)
    native_endpoint: str | None = Field(default=None, max_length=100)
    memory_id: UUID | None = None
    file_id: UUID | None = None


class CaptureFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=250)
    data: str | None = Field(default=None, max_length=70_000_000)
    reference: str | None = Field(default=None, max_length=2000)


class CaptureMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    role: Literal["user", "assistant", "system", "tool", "conversation"]
    text: str = Field(default="", max_length=MAX_MEMORY_CHARS)
    observed_at: datetime | None = None
    attachments: list[CaptureFile] = Field(default_factory=list, max_length=20)


class CaptureSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Provider
    conversation_id: str = Field(min_length=1, max_length=500)
    conversation_title: str | None = Field(default=None, max_length=180)
    messages: list[CaptureMessage] = Field(min_length=1, max_length=10000)
    message_order: list[str] = Field(default_factory=list, max_length=10000)


def key(value: str) -> str:
    if value.startswith(("http://", "https://")):
        url = urlsplit(value)
        query = parse_qs(url.query)
        value = (
            query.get("session")
            or query.get("sessionId")
            or [url.path.rstrip("/").rsplit("/", 1)[-1]]
        )[0]
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,200}", value):
        raise ValueError("capture.invalid_conversation")
    return value


def safe_path(value: str) -> Path:
    path = Path(value).expanduser().absolute()
    if any(x.is_symlink() or x.is_junction() for x in (path, *path.parents)):
        raise ValueError("capture.linked_path")
    return path.resolve(strict=True)


def reference_url(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or parts.username or parts.password:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class CaptureService:
    def __init__(self, engine: Engine, actor: UUID, scope: UUID):
        self.engine, self.actor, self.scope = engine, actor, scope

    def manage(
        self, action: str, value: CaptureInput, *, connection: Connection | None = None
    ) -> dict[str, Any]:
        with self.engine.begin() if connection is None else nullcontext(connection) as c:
            DatabasePermissions(c).require(
                self.actor, self.scope, "read" if action in ("list", "links") else "admin"
            )
            p = {
                "scope": self.scope,
                "actor": self.actor,
                "id": value.id,
                "include_deleted": value.include_deleted,
            }
            if action == "links":
                return self.links(c, value)
            if action == "list":
                rows = (
                    c.execute(
                        text(
                            "SELECT id,provider,conversation_id,title,enabled,"
                            "include_files,source_path,attachment_root,"
                            "native_endpoint,is_bulk,created_at,last_seen_at,last_error,deleted "
                            "FROM (SELECT g.*,coalesce((SELECT action='hide' FROM capture_audit "
                            "WHERE grant_id=g.id AND action IN ('hide','restore') "
                            "ORDER BY created_at DESC LIMIT 1),false) AS deleted "
                            "FROM capture_grants g WHERE scope_id=:scope "
                            "AND parent_grant_id IS NULL) listed "
                            "WHERE :include_deleted OR NOT deleted ORDER BY "
                            "created_at DESC"
                        ),
                        p,
                    )
                    .mappings()
                    .all()
                )
                return {"items": [dict(x) for x in rows]}
            lock_writes(c)
            if action == "reissue":
                grant = (
                    c.execute(
                        text(
                            "SELECT * FROM capture_grants WHERE id=:id AND scope_id=:scope "
                            "AND parent_grant_id IS NULL AND enabled FOR UPDATE"
                        ),
                        p,
                    )
                    .mappings()
                    .first()
                )
                if not grant:
                    raise ValueError("capture.grant_not_found")
                token = secrets.token_urlsafe(32)
                c.execute(
                    text("UPDATE capture_grants SET token_hash=:hash WHERE id=:id"),
                    {"id": grant["id"], "hash": hashlib.sha256(token.encode()).hexdigest()},
                )
                self.capture_audit(c, grant["id"], "reissue")
                workspace = Path(__file__).resolve().parents[2]
                runtime = workspace / ".venv/Scripts/python.exe"
                return {
                    "grant_id": str(grant["id"]),
                    "token": token,
                    "provider": grant["provider"],
                    "conversation_id": grant["conversation_id"],
                    "all_conversations": grant["is_bulk"],
                    "include_files": grant["include_files"],
                    "attachment_root": grant["attachment_root"],
                    "runtime": str(runtime) if runtime.is_file() else sys.executable,
                    "workspace": str(workspace),
                }
            if action in ("delete", "restore"):
                grant = (
                    c.execute(
                        text(
                            "SELECT id,enabled FROM capture_grants WHERE id=:id "
                            "AND scope_id=:scope AND parent_grant_id IS NULL FOR UPDATE"
                        ),
                        p,
                    )
                    .mappings()
                    .first()
                )
                if not grant:
                    raise ValueError("capture.grant_not_found")
                if grant["enabled"]:
                    raise ValueError("capture.revoke_first")
                self.capture_audit(c, value.id, "hide" if action == "delete" else "restore")
                return {"ok": True, "deleted": action == "delete"}
            if action == "grant":
                if value.all_conversations and (
                    value.provider not in ("gpt", "deepseek")
                    or value.source_path
                    or value.native_endpoint
                    or value.attachment_root
                ):
                    raise ValueError("capture.bulk_browser_only")
                source = safe_path(value.source_path) if value.source_path else None
                if source and (
                    not source.is_file() or source.suffix.lower() not in (".json", ".jsonl")
                ):
                    raise ValueError("capture.invalid_source")
                root = safe_path(value.attachment_root) if value.attachment_root else None
                if value.native_endpoint:
                    endpoint = urlsplit(value.native_endpoint)
                    if (
                        source
                        or endpoint.scheme != "http"
                        or endpoint.hostname != "127.0.0.1"
                        or not endpoint.port
                        or endpoint.username
                        or endpoint.password
                        or endpoint.path not in ("", "/")
                        or endpoint.query
                        or endpoint.fragment
                    ):
                        raise ValueError("capture.invalid_native_endpoint")
                if root and (not root.is_dir() or not value.include_files):
                    raise ValueError("capture.invalid_attachment_root")
                if source and source.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError("capture.source_too_large")
                conversation = "__all__" if value.all_conversations else key(value.conversation_id)
                title, _ = build_shared_memory(self.engine, DatabasePermissions(c)).memory.sanitize(
                    value.title.strip()
                    or (
                        f"{value.provider} 全部对话"
                        if value.all_conversations
                        else f"{value.provider} 对话 {conversation[:12]}"
                    )
                )
                if c.scalar(
                    text(
                        "SELECT 1 FROM capture_grants WHERE scope_id=:scope AND "
                        "provider=:provider AND conversation_id=:conversation AND "
                        "enabled"
                    ),
                    {**p, "provider": value.provider, "conversation": conversation},
                ):
                    raise ValueError("capture.already_authorized")
                ident, token = uuid4(), secrets.token_urlsafe(32)
                c.execute(
                    text(
                        "INSERT INTO capture_grants(id,scope_id,actor_id,provider,"
                        "conversation_id,title,token_hash,include_files,"
                        "source_path,attachment_root,native_endpoint,is_bulk) VALUES(:id,"
                        ":scope,:actor,:provider,:conversation,:title,:hash,:files,"
                        ":source,:root,:native,:bulk)"
                    ),
                    {
                        **p,
                        "id": ident,
                        "provider": value.provider,
                        "conversation": conversation,
                        "title": title,
                        "hash": hashlib.sha256(token.encode()).hexdigest(),
                        "files": value.include_files,
                        "source": str(source) if source else None,
                        "root": str(root) if root else None,
                        "native": value.native_endpoint,
                        "bulk": value.all_conversations,
                    },
                )
                self.capture_audit(c, ident, "grant")
                workspace = Path(__file__).resolve().parents[2]
                runtime = workspace / ".venv/Scripts/python.exe"
                return {
                    "grant_id": str(ident),
                    "token": token,
                    "provider": value.provider,
                    "conversation_id": conversation,
                    "all_conversations": value.all_conversations,
                    "include_files": value.include_files,
                    "attachment_root": str(root) if root else None,
                    "runtime": str(runtime) if runtime.is_file() else sys.executable,
                    "workspace": str(workspace),
                }
            if action == "revoke":
                changed = c.execute(
                    text(
                        "UPDATE capture_grants SET enabled=false,revoked_at=now() "
                        "WHERE id=:id AND scope_id=:scope AND enabled RETURNING id"
                    ),
                    p,
                ).scalar_one_or_none()
                if changed is None:
                    raise ValueError("capture.grant_not_found")
                c.execute(
                    text(
                        "UPDATE capture_grants SET enabled=false,revoked_at=now() "
                        "WHERE parent_grant_id=:id AND scope_id=:scope AND enabled"
                    ),
                    p,
                )
                self.capture_audit(c, changed, "revoke")
                return {"ok": True}
            raise ValueError("capture.invalid_action")

    def capture_audit(self, c: Connection, grant: UUID, action: str) -> None:
        c.execute(
            text(
                "INSERT INTO capture_audit(id,scope_id,actor_id,grant_id,action,created_at) "
                "VALUES(:id,:scope,:actor,:grant,:action,clock_timestamp())"
            ),
            {
                "id": uuid4(),
                "scope": self.scope,
                "actor": self.actor,
                "grant": grant,
                "action": action,
            },
        )

    @staticmethod
    def authorize(c: Connection, grant_id: UUID, token: str | None) -> dict[str, Any]:
        grant = (
            c.execute(
                text("SELECT * FROM capture_grants WHERE id=:id AND enabled FOR UPDATE"),
                {"id": grant_id},
            )
            .mappings()
            .first()
        )
        if not grant or (
            token is not None
            and not secrets.compare_digest(
                grant["token_hash"], hashlib.sha256(token.encode()).hexdigest()
            )
        ):
            raise PermissionError("capture.not_authorized")
        if grant["parent_grant_id"] and not c.scalar(
            text("SELECT enabled FROM capture_grants WHERE id=:id"),
            {"id": grant["parent_grant_id"]},
        ):
            raise PermissionError("capture.not_authorized")
        DatabasePermissions(c).require(grant["actor_id"], grant["scope_id"], "admin")
        return dict(grant)

    def ingest(
        self,
        grant_id: UUID,
        packet: CaptureSnapshot,
        token: str | None = None,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        with self.engine.begin() if connection is None else nullcontext(connection) as c:
            lock_writes(c)
            grant = self.authorize(c, grant_id, token)
            consent_grant_id = grant_id
            if grant["scope_id"] != self.scope or grant["actor_id"] != self.actor:
                raise PermissionError("capture.not_authorized")
            conversation = key(packet.conversation_id)
            if packet.provider != grant["provider"]:
                raise PermissionError("capture.conversation_mismatch")
            if grant["is_bulk"]:
                bulk_files = grant["include_files"]
                if not grant["include_files"] and any(
                    file.data is not None
                    for message in packet.messages
                    for file in message.attachments
                ):
                    raise PermissionError("capture.files_not_authorized")
                if conversation == "__all__":
                    raise ValueError("capture.invalid_conversation")
                root_id = grant_id
                child = (
                    c.execute(
                        text(
                            "SELECT * FROM capture_grants WHERE scope_id=:s AND provider=:provider "
                            "AND conversation_id=:conversation AND enabled FOR UPDATE"
                        ),
                        {
                            "s": self.scope,
                            "provider": packet.provider,
                            "conversation": conversation,
                        },
                    )
                    .mappings()
                    .first()
                )
                if child and child["actor_id"] != self.actor:
                    raise PermissionError("capture.conversation_owned_by_other")
                if not child:
                    child_id = uuid5(root_id, conversation)
                    existing = c.scalar(
                        text("SELECT 1 FROM capture_grants WHERE id=:id"), {"id": child_id}
                    )
                    if existing:
                        c.execute(
                            text(
                                "UPDATE capture_grants SET enabled=true,revoked_at=NULL,"
                                "include_files=:files "
                                "WHERE id=:id"
                            ),
                            {"id": child_id, "files": bulk_files},
                        )
                    else:
                        c.execute(
                            text(
                                "INSERT INTO capture_grants(id,scope_id,actor_id,provider,"
                                "conversation_id,title,token_hash,include_files,parent_grant_id) "
                                "VALUES(:id,:s,:a,:provider,:conversation,:title,:hash,:files,:parent)"
                            ),
                            {
                                "id": child_id,
                                "s": self.scope,
                                "a": self.actor,
                                "provider": packet.provider,
                                "conversation": conversation,
                                "title": f"{packet.provider} 对话 {conversation[:12]}",
                                "hash": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                                "files": grant["include_files"],
                                "parent": root_id,
                            },
                        )
                        self.capture_audit(c, child_id, "grant.bulk-child")
                    child = (
                        c.execute(
                            text("SELECT * FROM capture_grants WHERE id=:id"), {"id": child_id}
                        )
                        .mappings()
                        .one()
                    )
                grant_id = child["id"]
                grant = dict(child)
                # This request uses the explicit bulk consent, never a wider child consent.
                grant["include_files"] = bulk_files
                if packet.messages:
                    c.execute(
                        text("UPDATE capture_grants SET last_seen_at=now() WHERE id=:id"),
                        {"id": root_id},
                    )
            elif conversation != grant["conversation_id"]:
                raise PermissionError("capture.conversation_mismatch")
            grant["consent_grant_id"] = consent_grant_id
            service = build_shared_memory(self.engine, DatabasePermissions(c)).memory
            title_omitted = False
            if packet.conversation_title and packet.conversation_title.strip():
                try:
                    title = service.sanitize(packet.conversation_title.strip())[0]
                except PermissionError as error:
                    if str(error) != "privacy.persistence_denied":
                        raise
                    title, title_omitted = "对话记录（原始标题未收录）", True
                c.execute(
                    text("UPDATE capture_grants SET title=:title WHERE id=:id"),
                    {"title": title, "id": grant_id},
                )
                grant["title"] = title
            # Validate the complete batch before persistence, including across chunk boundaries.
            prepared = []
            for message in packet.messages:
                if not message.text.strip() and not message.attachments:
                    continue
                files = []
                try:
                    safe = service.sanitize(message.text)[0] if message.text.strip() else ""
                    for attachment in message.attachments:
                        name, _ = service.sanitize(safe_filename(attachment.filename))
                        reference = reference_url(attachment.reference)
                        if reference:
                            reference, _ = service.sanitize(reference)
                        if attachment.data is not None:
                            if not grant["include_files"]:
                                raise PermissionError("capture.files_not_authorized")
                            try:
                                decoded = base64.b64decode(attachment.data, validate=True)
                            except ValueError:
                                raise ValueError("files.invalid_data") from None
                            if len(decoded) > MAX_FILE_BYTES:
                                raise ValueError("files.too_large")
                        files.append((attachment, name, reference))
                except PermissionError as error:
                    if str(error) != "privacy.persistence_denied":
                        raise
                    # Keep chronology, never persist denied originals or their attachments.
                    safe, files = PRIVACY_OMITTED, []
                prepared.append((message, safe, files))
            conversation_ref, entry_ref, body, files, legacy, message_count, excluded = assemble(
                c, self, grant, packet, prepared, title_omitted=title_omitted
            )
            privacy_omitted = c.scalar(
                text("SELECT count(*) FROM capture_messages WHERE conversation_id=:c AND body=:b"),
                {"c": conversation_ref, "b": PRIVACY_OMITTED},
            )
            changed = unchanged = pending = 0
            message = CaptureMessage(id="__conversation__", role="conversation", text=body)
            aggregate = [(message, body, files)]
            for message, safe, files in aggregate:
                for part, chunk in enumerate([safe]):
                    body = service.sanitize(chunk)[0]
                    digest = content_hash(body)
                    old = (
                        c.execute(
                            text("SELECT * FROM capture_entries WHERE id=:id"),
                            {"id": entry_ref},
                        )
                        .mappings()
                        .first()
                    )
                    ident = old["id"] if old else uuid4()
                    candidate = old["candidate_id"] if old else None
                    current = None
                    state = (
                        c.execute(
                            text(
                                "SELECT * FROM candidate_states WHERE candidate_id=:id "
                                "ORDER BY revision DESC LIMIT 1"
                            ),
                            {"id": candidate},
                        )
                        .mappings()
                        .first()
                        if candidate
                        else None
                    )
                    if state and state["status"] == "rejected":
                        raise PermissionError("capture.review_rejected")
                    if candidate and c.scalar(
                        text(
                            "SELECT 1 FROM memory_candidates mc JOIN revocations r "
                            "ON r.target_id=mc.source_id AND "
                            "r.kind='intake_source' WHERE mc.id=:id"
                        ),
                        {"id": candidate},
                    ):
                        raise PermissionError("capture.source_revoked")
                    memory_id = old["memory_id"] if old else None
                    if state and state["memory_revision_id"]:
                        memory_id = (
                            c.scalar(
                                text("SELECT memory_id FROM memories WHERE id=:id"),
                                {"id": state["memory_revision_id"]},
                            )
                            or memory_id
                        )
                    if memory_id:
                        current = (
                            c.execute(
                                text(
                                    "SELECT id,revision FROM memories WHERE "
                                    "memory_id=:id AND scope_id=:s ORDER BY "
                                    "revision DESC LIMIT 1"
                                ),
                                {"id": memory_id, "s": self.scope},
                            )
                            .mappings()
                            .first()
                        )
                        if not current or not memory_visible(c, current["id"]):
                            raise PermissionError("capture.memory_not_visible")
                    if old and files:
                        self.store_files(c, grant_id, old["id"], files)
                    if old and old["content_hash"] == digest:
                        # Re-pairing the same conversation keeps its memory and file index.
                        c.execute(
                            text("UPDATE capture_entries SET grant_id=:g WHERE id=:id"),
                            {"g": grant_id, "id": old["id"]},
                        )
                        unchanged += 1
                        pending += bool(state and state["status"] in ("pending", "edited"))
                        continue
                    if state and state["status"] in ("pending", "edited"):
                        # Capture may amend its own pending draft; it still cannot approve it.
                        c.execute(
                            text(
                                "INSERT INTO candidate_states(id,candidate_id,"
                                "revision,status,text,actor_id,reason_code,"
                                "proposed_title,proposed_sources) VALUES(:id,"
                                ":candidate,:revision,'edited',:body,:actor,"
                                "'requires_uncertain_review',:title,CAST(:sources "
                                "AS jsonb))"
                            ),
                            {
                                "id": uuid4(),
                                "candidate": candidate,
                                "revision": state["revision"] + 1,
                                "body": body,
                                "actor": self.actor,
                                "title": state["proposed_title"],
                                "sources": json.dumps(state["proposed_sources"])
                                if state["proposed_sources"] is not None
                                else None,
                            },
                        )
                        audit(c, self.actor, self.scope, candidate, "edit")
                        result = {"status": "pending"}
                    elif current:
                        result = edit_memory(
                            self.engine,
                            self.actor,
                            self.scope,
                            memory_id,
                            current["revision"],
                            body,
                            uuid5(grant_id, message.id + str(part) + digest),
                            connection=c,
                            capture_review=True,
                        )
                        candidate = (
                            UUID(result["candidate_id"]) if result.get("candidate_id") else None
                        )
                    else:
                        result = submit(
                            self.engine,
                            self.actor,
                            self.scope,
                            conversation_memory_title(grant["title"]),
                            body,
                            uuid5(grant_id, message.id + str(part) + digest),
                            connection=c,
                            capture_grant=grant_id,
                        )
                        candidate = UUID(result["candidate_id"])
                    pending += result["status"] in ("pending", "edited")
                    c.execute(
                        text(
                            "INSERT INTO capture_entries(id,grant_id,scope_id,"
                            "message_id,part,content_hash,candidate_id,memory_id) "
                            "VALUES(:id,:g,:s,:m,:p,:hash,:candidate,:memory) ON "
                            "CONFLICT(id) DO UPDATE SET grant_id=EXCLUDED.grant_id,"
                            "content_hash=EXCLUDED.content_hash,"
                            "candidate_id=EXCLUDED.candidate_id,"
                            "memory_id=EXCLUDED.memory_id,updated_at=now()"
                        ),
                        {
                            "id": ident,
                            "g": grant_id,
                            "s": self.scope,
                            "m": message.id,
                            "p": part,
                            "hash": digest,
                            "candidate": candidate,
                            "memory": memory_id,
                        },
                    )
                    c.execute(
                        text("UPDATE capture_conversations SET entry_id=:entry WHERE id=:id"),
                        {"entry": ident, "id": conversation_ref},
                    )
                    merge_legacy(c, self.actor, ident, legacy)
                    if files and part == 0:
                        self.store_files(c, grant_id, ident, files)
                    changed += 1
            c.execute(
                text("UPDATE capture_grants SET last_seen_at=now(),last_error=NULL WHERE id=:id"),
                {"id": grant_id},
            )
            if changed:
                self.capture_audit(c, grant_id, "capture")
            return {
                "changed": changed,
                "unchanged": unchanged,
                "pending": pending,
                "grant_id": str(grant_id),
                "title": grant["title"],
                "messages": message_count,
                "excluded_messages": excluded,
                "privacy_omitted_messages": privacy_omitted,
                "privacy_omitted_title": title_omitted,
                "conversation_id": str(conversation_ref),
            }

    def store_files(self, c: Connection, grant: UUID, entry: UUID, files: list[Any]) -> None:
        library = FileLibrary(self.engine, self.actor, self.scope)
        for attachment, name, reference in files:
            file_id = None
            if attachment.data is not None:
                saved = library.run(
                    "upload",
                    FileRequest(
                        filename=name,
                        data=attachment.data,
                        external_key="capture:" + str(grant) + ":" + content_hash(attachment.id),
                    ),
                    connection=c,
                )
                file_id = saved["id"]
            c.execute(
                text(
                    "INSERT INTO capture_file_links(id,entry_id,file_id,"
                    "attachment_id,filename,reference,state) VALUES(:id,:entry,"
                    ":file,:attachment,:name,:reference,:state) ON "
                    "CONFLICT(entry_id,attachment_id) DO UPDATE SET "
                    "file_id=coalesce(EXCLUDED.file_id,capture_file_links.file_id),"
                    "filename=EXCLUDED.filename,reference=EXCLUDED.reference,"
                    "state=CASE WHEN EXCLUDED.file_id IS NOT NULL OR "
                    "capture_file_links.file_id IS NOT NULL THEN 'saved' ELSE "
                    "'reference' END"
                ),
                {
                    "id": uuid4(),
                    "entry": entry,
                    "file": file_id,
                    "attachment": attachment.id,
                    "name": name,
                    "reference": reference,
                    "state": "saved" if file_id else "reference",
                },
            )

    def links(self, c: Connection, value: CaptureInput) -> dict[str, Any]:
        p = {"scope": self.scope, "memory": value.memory_id, "file": value.file_id}
        if not value.memory_id and not value.file_id:
            raise ValueError("capture.invalid_link_query")
        rows = (
            c.execute(
                text(
                    "SELECT e.id,coalesce(e.memory_id,m.memory_id) AS memory_id,"
                    "g.title,g.provider,l.file_id,l.filename,l.reference,\n        "
                    "CASE WHEN l.file_id IS NULL AND l.state='saved' THEN "
                    "'deleted' ELSE l.state END AS state,\n        "
                    "coalesce(s.status,'approved') AS memory_status\n        FROM "
                    "capture_entries e JOIN capture_grants g ON g.id=e.grant_id\n   "
                    "     LEFT JOIN LATERAL (SELECT status,memory_revision_id FROM "
                    "candidate_states WHERE candidate_id=e.candidate_id ORDER BY "
                    "revision DESC LIMIT 1) s ON true\n        LEFT JOIN memories m "
                    "ON m.id=s.memory_revision_id\n        JOIN capture_file_links "
                    "l ON l.entry_id=e.id\n        WHERE e.scope_id=:scope AND "
                    "((CAST(:memory AS uuid) IS NOT NULL AND coalesce(e.memory_id,"
                    "m.memory_id)=CAST(:memory AS uuid)) OR (CAST(:file AS uuid) "
                    "IS NOT NULL AND l.file_id=CAST(:file AS uuid)))"
                ),
                p,
            )
            .mappings()
            .all()
        )
        visible = []
        for row in rows:
            if row["memory_id"]:
                current = c.scalar(
                    text(
                        "SELECT id FROM memories WHERE memory_id=:m AND scope_id=:s "
                        "ORDER BY revision DESC LIMIT 1"
                    ),
                    {"m": row["memory_id"], "s": self.scope},
                )
                if not current or not memory_visible(c, current):
                    continue
            visible.append(dict(row))
        return {"items": visible}
