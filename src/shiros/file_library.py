"""Opaque files are never executed, rendered as HTML, or promoted into memories."""

import base64
import hashlib
import re
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text

from shiros.identity import DatabasePermissions

MAX_FILE_BYTES = 50 * 1024 * 1024


def file_audit(c: Any, actor: UUID, scope: UUID, subject: UUID, action: str) -> None:
    c.execute(
        text(
            "INSERT INTO file_audit(id,scope_id,actor_id,subject_id,action) "
            "VALUES (:id,:scope,:actor,:subject,:action)"
        ),
        {"id": uuid4(), "scope": scope, "actor": actor, "subject": subject, "action": action},
    )


class FileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    filename: str = Field(default="file.bin", max_length=250)
    data: str = Field(default="", max_length=70_000_000)
    query: str = Field(default="", max_length=250)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    external_key: str | None = Field(default=None, max_length=300)


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "_", name).strip(" .")
    return name[:240] or "file.bin"


class FileLibrary:
    def __init__(self, engine: Engine, actor: UUID, scope: UUID) -> None:
        self.engine, self.actor, self.scope = engine, actor, scope

    def run(self, action: str, value: FileRequest) -> dict[str, Any]:
        if action not in {"list", "upload", "conversation-import", "read", "delete"}:
            raise ValueError("files.invalid_action")
        with self.engine.begin() as c:
            DatabasePermissions(c).require(
                self.actor, self.scope, "read" if action in {"list", "read"} else "admin"
            )
            params: dict[str, Any] = {"scope": self.scope, "id": value.id}
            if action == "list":
                params.update(q="%" + value.query + "%", offset=value.offset, limit=value.limit)
                condition = "scope_id=:scope AND filename ILIKE :q"
                total = c.scalar(
                    text("SELECT count(*) FROM library_files WHERE " + condition), params
                )
                items = (
                    c.execute(
                        text(
                            "SELECT id,filename,size,sha256,created_at,updated_at,external_key "
                            "FROM library_files WHERE "
                            + condition
                            + " ORDER BY updated_at DESC,id LIMIT :limit OFFSET :offset"
                        ),
                        params,
                    )
                    .mappings()
                    .all()
                )
                return {"items": [dict(x) for x in items], "total": total}
            if action in {"upload", "conversation-import"}:
                try:
                    data = base64.b64decode(value.data, validate=True)
                except ValueError:
                    raise ValueError("files.invalid_data") from None
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError("files.too_large")
                if action == "conversation-import":
                    from shiros.conversation_import import conversation_files

                    converted = conversation_files(data, value.filename)
                    for name, content in [(safe_filename(value.filename), data), *converted]:
                        ident = uuid4()
                        c.execute(
                            text(
                                "INSERT INTO library_files "
                                "(id,scope_id,filename,media_type,size,sha256,content,created_by) "
                                "VALUES (:id,:scope,:name,'application/octet-stream',"
                                ":size,:sha,:data,:actor)"
                            ),
                            {
                                "id": ident,
                                "scope": self.scope,
                                "name": name,
                                "size": len(content),
                                "sha": hashlib.sha256(content).hexdigest(),
                                "data": content,
                                "actor": self.actor,
                            },
                        )
                        file_audit(c, self.actor, self.scope, ident, "file.conversation-import")
                    return {"conversations": len(converted), "files": len(converted) + 1}
                file_id = uuid4()
                params.update(
                    id=file_id,
                    name=safe_filename(value.filename),
                    data=data,
                    size=len(data),
                    sha=hashlib.sha256(data).hexdigest(),
                    actor=self.actor,
                    key=value.external_key,
                )
                row = (
                    c.execute(
                        text(
                            "INSERT INTO library_files "
                            "(id,scope_id,filename,media_type,size,sha256,content,"
                            "created_by,external_key) "
                            "VALUES (:id,:scope,:name,'application/octet-stream',"
                            ":size,:sha,:data,:actor,:key) "
                            "ON CONFLICT(scope_id,external_key) DO UPDATE SET "
                            "filename=EXCLUDED.filename,size=EXCLUDED.size,sha256=EXCLUDED.sha256,"
                            "content=EXCLUDED.content,updated_at=now() "
                            "RETURNING id,filename,size,sha256"
                        ),
                        params,
                    )
                    .mappings()
                    .one()
                )
                file_audit(c, self.actor, self.scope, row["id"], "file.upload")
                return dict(row)
            found = (
                c.execute(
                    text(
                        "SELECT filename,content FROM library_files "
                        "WHERE id=:id AND scope_id=:scope"
                    ),
                    params,
                )
                .mappings()
                .first()
            )
            if found is None:
                raise ValueError("files.not_found")
            if action == "read":
                return {"filename": found["filename"], "content": bytes(found["content"])}
            c.execute(text("DELETE FROM library_files WHERE id=:id AND scope_id=:scope"), params)
            assert value.id is not None
            file_audit(c, self.actor, self.scope, value.id, "file.delete")
            return {"ok": True}
