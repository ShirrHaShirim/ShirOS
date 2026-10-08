"""Loopback browser adapter. Browser clients never select their identity or scope."""

import asyncio
import secrets
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from shiros import tags
from shiros.core.retrieval import SearchRequest
from shiros.domains.music import MusicRequest, MusicService
from shiros.file_library import FileLibrary, FileRequest
from shiros.identity import DatabasePermissions, LocalIdentity
from shiros.markdown_export import memory_markdown
from shiros.review_workflow import ReviewWorkflow
from shiros.shared_memory import build_shared_memory
from shiros.visibility import visible_memory

WEB_ROOT = Path(__file__).parent / "web"


class BrowserGuard:
    def __init__(self, app: ASGIApp, port: int, token: str) -> None:
        self.app, self.port, self.token = app, port, token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode("latin1")
        origin = headers.get(b"origin", b"").decode("latin1")
        allowed = f"127.0.0.1:{self.port}"
        api = scope["path"].startswith("/ui-api/")
        client = scope.get("client")
        denied = (
            host != allowed
            or (client is not None and client[0] not in ("127.0.0.1", "::1", "testclient"))
            or (origin and origin != f"http://{allowed}")
            or headers.get(b"sec-fetch-site") == b"cross-site"
            or (
                api
                and not secrets.compare_digest(
                    headers.get(b"x-shiros-token", b""), self.token.encode("ascii")
                )
            )
        )
        if denied:
            await JSONResponse({"error": "permission.denied"}, status_code=403)(
                scope, receive, send
            )
            return
        if api:
            if scope["method"] != "POST" or not headers.get(b"content-type", b"").startswith(
                b"application/json"
            ):
                await JSONResponse({"error": "request.invalid"}, status_code=400)(
                    scope, receive, send
                )
                return
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                limit = (
                    70_000_000
                    if scope["path"]
                    in {"/ui-api/files/upload", "/ui-api/files/conversation-import"}
                    else 12000000
                    if scope["path"] == "/ui-api/music/image-upload"
                    else 400000
                )
                if len(body) > limit:
                    await JSONResponse({"error": "intake.too_large"}, status_code=413)(
                        scope, receive, send
                    )
                    return
                if not message.get("more_body", False):
                    break

            async def buffered() -> Message:
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            receive = buffered

        async def protected(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; script-src 'self'; "
                        b"style-src 'self'; img-src 'self' blob:; connect-src 'self'; "
                        b"frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                    ),
                ]
            await send(message)

        await self.app(scope, receive, protected)


class BrowserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID | None = None
    title: str = Field(default="note.txt", max_length=250)
    sources: list[dict[str, Any]] | None = Field(default=None, max_length=30)
    text: str = Field(default="", max_length=65536)
    query: str = Field(default="", max_length=1000)
    mode: Literal["keyword", "semantic", "hybrid", "structured"] = "keyword"
    kind: Literal["text", "markdown", "json", "csv", "intake_source", "memory"] = "text"
    hidden: bool = False
    revision: int | None = Field(default=None, ge=1)
    name: str = Field(default="", max_length=64)
    parent_id: UUID | None = None
    tag_id: UUID | None = None
    untagged: bool = False
    tag_ids: list[UUID] = Field(default_factory=list, max_length=30)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


def create_workbench(
    engine: Engine,
    identity: LocalIdentity,
    scope_id: UUID,
    port: int = 8001,
    token: str | None = None,
    music_enabled: bool = True,
) -> FastAPI:
    app = FastAPI(
        title="ShirOS Workbench", version="0.5.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    token = token or secrets.token_urlsafe(32)
    workflow = ReviewWorkflow(engine, identity, Path(".local/inbox"))
    app.add_middleware(BrowserGuard, port=port, token=token)
    app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": "request.invalid"}, status_code=422)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        if not music_enabled:
            html = html.replace('<script src="/assets/music.js" defer></script>', "")
            html = html.replace(
                '<button class="nav-item" data-view="music">',
                '<button hidden class="nav-item" data-view="disabled-music">',
            )
        return html

    @app.get("/workbench-health")
    def health() -> dict[str, str]:
        return {"service": "ShirOS Workbench", "stage": "local-workbench", "version": "0.5.0"}

    @app.post("/ui-api/files/{action}")
    def files_action(action: str, value: FileRequest) -> Response:
        try:
            with engine.connect() as connection:
                actor = workflow._actor(connection, scope_id, "read")
            result = FileLibrary(engine, actor, scope_id).run(action, value)
            if action == "read":
                return Response(
                    result["content"],
                    media_type="application/octet-stream",
                    headers={
                        "Content-Disposition": "attachment; filename*=UTF-8''"
                        + quote(result["filename"])
                    },
                )
            return JSONResponse(jsonable_encoder(result))
        except PermissionError:
            return JSONResponse({"error": "permission.denied"}, status_code=403)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "files.operation_failed"}, status_code=503)

    @app.post("/ui-api/music/{action}")
    def music_action(action: str, value: MusicRequest) -> Response:
        if not music_enabled:
            return JSONResponse({"error": "music.disabled"}, status_code=404)
        try:
            with engine.connect() as connection:
                actor = workflow._actor(connection, scope_id, "read")
            result = MusicService(engine, actor, scope_id).run(action, value)
            if action == "image-read":
                return Response(content=result["data"], media_type=result["media_type"])
            return JSONResponse(jsonable_encoder(result))
        except PermissionError as error:
            return JSONResponse({"error": str(error)}, status_code=403)
        except ValueError as error:
            code = str(error)
            return JSONResponse(
                {"error": code if code.startswith("music.") else "music.invalid_request"},
                status_code=400,
            )
        except Exception:
            return JSONResponse({"error": "music.operation_failed"}, status_code=503)

    @app.post("/ui-api/{action}")
    def action(action: str, value: BrowserInput) -> Response:
        try:
            result: Any
            if action == "export-md":
                with engine.connect() as connection:
                    actor = workflow._actor(connection, scope_id, "read")
                output = []
                if value.id is not None:
                    memory = workflow.memory(scope_id, value.id)
                    if memory is None:
                        raise ValueError("source.not_found")
                    from shiros.memory_metadata import details

                    with engine.connect() as connection:
                        item = {**memory.model_dump(mode="json"), **details(connection, memory)}
                    item["tags"] = tags.memory_tags(engine, actor, scope_id, value.id)
                    output.append(memory_markdown(item))
                else:
                    offset = 0
                    while True:
                        page = tags.library(engine, actor, scope_id, offset=offset, limit=100)
                        output.extend(memory_markdown(item) for item in page["items"])
                        offset += 100
                        if offset >= page["total"]:
                            break
                return Response(
                    "\n\n---\n\n".join(output) or "# ShirOS\n\n暂无记忆。\n",
                    media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="shiros-memories.md"'},
                )
            if action in (
                "tags",
                "tag-create",
                "tag-update",
                "memory-tags",
                "set-memory-tags",
                "library",
            ):
                with engine.connect() as connection:
                    actor = workflow._actor(connection, scope_id, "read")
                if action == "tags":
                    result = {"items": tags.list_tags(engine, actor, scope_id)}
                elif action == "tag-create":
                    result = tags.create_tag(engine, actor, scope_id, value.name, value.parent_id)
                elif action == "library":
                    result = tags.library(
                        engine,
                        actor,
                        scope_id,
                        value.tag_id,
                        value.offset,
                        value.limit,
                        value.untagged,
                    )
                else:
                    if value.id is None:
                        raise ValueError("request.invalid")
                    if action == "tag-update":
                        result = tags.update_tag(
                            engine, actor, scope_id, value.id, value.name, value.parent_id
                        )
                    elif action == "memory-tags":
                        result = {"items": tags.memory_tags(engine, actor, scope_id, value.id)}
                    else:
                        result = {
                            "items": tags.set_memory_tags(
                                engine, actor, scope_id, value.id, value.tag_ids
                            )
                        }
            elif action in ("session", "memories"):
                with engine.connect() as connection:
                    actor = workflow._actor(connection, scope_id, "read")
                    services = build_shared_memory(engine, DatabasePermissions(connection))
                    if action == "session":
                        count = connection.scalar(
                            text(
                                "SELECT count(*) FROM memories m WHERE scope_id=:scope AND "
                                "NOT EXISTS(SELECT 1 FROM memories n "
                                "WHERE n.supersedes_id=m.id) AND " + visible_memory()
                            ),
                            {"scope": scope_id},
                        )
                        result = {
                            "scope": scope_id,
                            "locale": "zh-CN",
                            "principal": "local",
                            "stats": {
                                "memories": count,
                                "candidates": len(workflow.queue(scope_id)),
                            },
                        }
                    else:
                        hits = asyncio.run(
                            services.retrieval.search(
                                actor,
                                SearchRequest(
                                    scope_ids=(scope_id,),
                                    query=value.query,
                                    mode=value.mode,
                                    limit=value.limit,
                                ),
                            )
                        )
                        from shiros.memory_metadata import details

                        result = {
                            "items": [
                                {
                                    **hit.memory.model_dump(mode="json"),
                                    **details(connection, hit.memory),
                                }
                                for hit in hits
                            ]
                        }
            elif action == "queue":
                candidates = workflow.queue(scope_id)
                with engine.connect() as connection:
                    sources = {
                        row.id: row
                        for row in connection.execute(
                            text(
                                "SELECT id,title,kind FROM review_sources WHERE scope_id=:scope "
                                "AND id=ANY(CAST(:ids AS uuid[]))"
                            ),
                            {"scope": scope_id, "ids": [item.source_id for item in candidates]},
                        )
                    }
                result = {
                    "items": [
                        {
                            **item.model_dump(mode="json"),
                            "title": item.proposed_title or sources[item.source_id].title,
                            "kind": sources[item.source_id].kind,
                        }
                        for item in candidates
                    ]
                }
            elif action == "preview":
                suffix = {"text": ".txt", "markdown": ".md", "json": ".json", "csv": ".csv"}.get(
                    value.kind
                )
                if suffix is None:
                    raise ValueError("intake.invalid_type")
                title = value.title if Path(value.title).suffix else value.title + suffix
                result = workflow.preview_text(scope_id, title, value.text)
            else:
                if value.id is None:
                    raise ValueError("request.invalid")
                if action == "stage":
                    result = workflow.stage(scope_id, value.id)
                elif action == "inspect":
                    result = workflow.inspect(scope_id, value.id)
                    result["candidate"]["title"] = result["source"]["title"]
                elif action == "edit":
                    result = workflow.edit(
                        scope_id,
                        value.id,
                        value.text,
                        value.title if "title" in value.model_fields_set else None,
                        value.sources,
                    )
                elif action == "approve":
                    if value.revision is None:
                        raise ValueError("request.invalid")
                    result = workflow.approve(scope_id, value.id, value.revision)
                elif action == "reject":
                    result = workflow.reject(scope_id, value.id)
                elif action == "memory":
                    memory = workflow.memory(scope_id, value.id)
                    result = {"memory": None}
                    if memory:
                        with engine.connect() as connection:
                            actor = workflow._actor(connection, scope_id, "read")
                            services = build_shared_memory(engine, DatabasePermissions(connection))
                            source = asyncio.run(
                                services.retrieval.source(
                                    actor, scope_id, memory.provenance.source_id
                                )
                            )
                        from shiros.memory_metadata import details

                        with engine.connect() as connection:
                            metadata = details(connection, memory)
                        result = {
                            "memory": {
                                **memory.model_dump(mode="json"),
                                **metadata,
                                "tags": tags.memory_tags(engine, actor, scope_id, memory.memory_id),
                            },
                            "source": source,
                        }
                elif action == "revoke":
                    workflow.revoke(scope_id, value.id, value.kind)
                    result = {"ok": True}
                elif action == "hide":
                    workflow.hide(scope_id, value.id, value.hidden)
                    result = {"ok": True}
                else:
                    raise ValueError("request.invalid")
            return JSONResponse(jsonable_encoder(result))
        except PermissionError as error:
            return JSONResponse({"error": str(error)}, status_code=403)
        except ValueError as error:
            code = str(error)
            allowed = {
                "candidate.not_found",
                "candidate.closed",
                "candidate.revision_conflict",
                "intake.invalid_path",
                "intake.invalid_type",
                "intake.too_large",
                "intake.invalid_content",
                "intake.empty",
                "request.invalid",
                "source.not_found",
                "memory.empty_content",
                "memory.revision_conflict",
                "memory.invalid_metadata",
                "memory.invalid_title",
                "memory.invalid_source_id",
                "memory.too_many_sources",
            }
            return JSONResponse(
                {"error": code if code in allowed else "request.invalid"}, status_code=400
            )
        except Exception:
            return JSONResponse({"error": "operation.failed"}, status_code=503)

    return app
