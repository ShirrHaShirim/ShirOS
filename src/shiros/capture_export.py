"""Explicit all-conversation file import; preserve the source and apply ordinary review policy."""

import base64
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, text

from shiros.adapters.database.records import lock_writes
from shiros.capture import CaptureInput, CaptureService, CaptureSnapshot
from shiros.capture_adapters import local_attachment, text_parts
from shiros.file_library import MAX_FILE_BYTES, FileLibrary, FileRequest, safe_filename
from shiros.identity import DatabasePermissions


def export_records(
    data: bytes, filename: str
) -> tuple[list[dict[str, Any]], zipfile.ZipFile | None]:
    archive = None
    try:
        raw = data
        if filename.lower().endswith(".zip"):
            archive = zipfile.ZipFile(io.BytesIO(data))
            entries = [x for x in archive.infolist() if x.filename == "conversations.json"]
            if len(entries) != 1 or entries[0].file_size > MAX_FILE_BYTES:
                raise ValueError("capture.invalid_export")
            raw = archive.read(entries[0])
        parsed = json.loads(raw.decode("utf-8-sig"))
        records = parsed if isinstance(parsed, list) else [parsed]
        if not 1 <= len(records) <= 10000 or any(
            not isinstance(x, dict) or not isinstance(x.get("mapping"), dict) for x in records
        ):
            raise ValueError("capture.invalid_export")
        return records, archive
    except (ValueError, zipfile.BadZipFile, RuntimeError, OSError):
        if archive:
            archive.close()
        raise ValueError("capture.invalid_export") from None


def export_packet(
    item: dict[str, Any], index: int, archive: zipfile.ZipFile | None, include_files: bool
) -> CaptureSnapshot:
    conversation = str(
        item.get("id")
        or item.get("conversation_id")
        or hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
    )
    title = str(item.get("title") or f"ChatGPT 对话 {index + 1}")[:180]
    messages = []
    total_bytes = 0
    for node_id, node in item["mapping"].items():
        if not isinstance(node, dict):
            raise ValueError("capture.invalid_export")
        message = node.get("message")
        if not isinstance(message, dict) or message.get("channel") == "analysis":
            continue
        author = message.get("author") or {}
        if not isinstance(author, dict):
            raise ValueError("capture.invalid_export")
        role = author.get("role", "unknown")
        if role not in ("user", "assistant", "tool", "system"):
            continue
        content = message.get("content", {})
        body = text_parts(content)
        metadata = message.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("capture.invalid_export")
        files = metadata.get("attachments") or []
        if not isinstance(files, list):
            raise ValueError("capture.invalid_export")
        parts = content.get("parts", []) if isinstance(content, dict) else []
        if not isinstance(parts, list):
            raise ValueError("capture.invalid_export")
        files = [
            *files,
            *[
                x
                for x in parts
                if isinstance(x, dict)
                and (x.get("asset_pointer") or x.get("type") in ("image", "file", "input_image"))
            ],
        ]
        message_files = []
        for file in files:
            if not isinstance(file, dict):
                continue
            value = {
                **file,
                "id": str(
                    file.get("id") or file.get("file_id") or file.get("asset_pointer") or node_id
                ),
            }
            link = local_attachment(value, False, None)
            name = str(
                file.get("name")
                or file.get("filename")
                or file.get("file_id")
                or str(file.get("asset_pointer") or "").rsplit("/", 1)[-1]
                or "attachment.bin"
            )
            link["filename"] = safe_filename(name)
            if archive and include_files:
                ids = {
                    name,
                    str(file.get("file_id") or file.get("id") or ""),
                    str(file.get("asset_pointer") or "").rsplit("/", 1)[-1],
                } - {""}
                found = [
                    entry
                    for entry in archive.infolist()
                    if not PurePosixPath(entry.filename).is_absolute()
                    and ".." not in PurePosixPath(entry.filename).parts
                    and "\\" not in entry.filename
                    and not entry.is_dir()
                    and ((entry.external_attr >> 16) & 0o170000) != 0o120000
                    and any(
                        PurePosixPath(entry.filename).name == ident
                        or PurePosixPath(entry.filename).name.startswith(ident + "-")
                        for ident in ids
                    )
                ]
                if len(found) == 1 and found[0].file_size <= MAX_FILE_BYTES:
                    total_bytes += found[0].file_size
                    if total_bytes > 100 * 1024 * 1024:
                        raise ValueError("capture.export_too_large")
                    try:
                        link["data"] = base64.b64encode(archive.read(found[0])).decode()
                    except (RuntimeError, zipfile.BadZipFile, OSError):
                        raise ValueError("capture.invalid_export") from None
            message_files.append(link)
        created = message.get("create_time")
        observed = (
            datetime.fromtimestamp(created, UTC) if isinstance(created, (float, int)) else None
        )
        if body.strip() or message_files:
            messages.append(
                {
                    "id": str(message.get("id") or node_id),
                    "role": role,
                    "text": (f"上级消息: {node['parent']}\n\n" if node.get("parent") else "")
                    + body,
                    "observed_at": observed,
                    "attachments": message_files,
                }
            )
    if not messages:
        raise ValueError("capture.empty_conversation")
    return CaptureSnapshot.model_validate(
        {
            "provider": "gpt",
            "conversation_id": conversation,
            "conversation_title": title,
            "messages": messages,
            "message_order": [m["id"] for m in messages],
        }
    )


def import_export(engine: Engine, actor: UUID, scope: UUID, value: FileRequest) -> dict[str, Any]:
    try:
        data = base64.b64decode(value.data, validate=True)
    except ValueError:
        raise ValueError("files.invalid_data") from None
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("files.too_large")
    records, archive = export_records(data, value.filename)
    digest = hashlib.sha256(data).hexdigest()
    conversation_key = "export-" + actor.hex[:8] + "-" + digest
    results = {
        "conversations": len(records),
        "imported": 0,
        "changed": 0,
        "unchanged": 0,
        "pending": 0,
        "privacy_omitted_messages": 0,
        "privacy_omitted_titles": 0,
        "failed": [],
        "files": 0,
    }
    root_id = None
    service = CaptureService(engine, actor, scope)
    try:
        with engine.begin() as c:
            lock_writes(c)
            DatabasePermissions(c).require(actor, scope, "admin")
            if c.scalar(
                text(
                    "SELECT id FROM capture_grants WHERE scope_id=:s AND actor_id=:a "
                    "AND conversation_id=:conversation AND enabled"
                ),
                {"s": scope, "a": actor, "conversation": conversation_key},
            ):
                raise ValueError("capture.export_in_progress")
            # Choosing the file again is a new explicit import. Existing visible conversations
            # deduplicate normally; removed/rejected copies need a fresh consent generation.
            grant = service.manage(
                "grant",
                CaptureInput(
                    provider="gpt",
                    conversation_id=conversation_key,
                    title="ChatGPT 导出文件导入",
                    include_files=value.include_files,
                ),
                connection=c,
            )
            root_id = UUID(grant["grant_id"])
            c.execute(
                text(
                    "UPDATE capture_grants SET is_bulk=true,enabled=true,revoked_at=NULL,"
                    "include_files=:files WHERE id=:id"
                ),
                {"id": root_id, "files": value.include_files},
            )
            original = FileLibrary(engine, actor, scope).run(
                "upload",
                value.model_copy(update={"external_key": "conversation-export:" + digest}),
                connection=c,
            )
            results["files"] = 1
        # Commit one conversation at a time so a large import does not block other work.
        for index, item in enumerate(records):
            try:
                packet = export_packet(item, index, archive, value.include_files)
                with engine.begin() as c:
                    outcome = service.ingest(root_id, packet, connection=c)
                    c.execute(
                        text(
                            "INSERT INTO capture_file_links(id,entry_id,file_id,attachment_id,"
                            "filename,state) SELECT gen_random_uuid(),id,:file,:attachment,"
                            ":name,'saved' FROM capture_entries WHERE grant_id=:g AND "
                            "message_id='__conversation__' ON CONFLICT(entry_id,"
                            "attachment_id) DO UPDATE SET file_id=EXCLUDED.file_id,state='saved'"
                        ),
                        {
                            "file": original["id"],
                            "attachment": "export-source:" + digest,
                            "name": safe_filename(value.filename),
                            "g": UUID(outcome["grant_id"]),
                        },
                    )
                results["imported"] += 1
                for field in ("changed", "unchanged", "pending"):
                    results[field] += outcome[field]
                results["privacy_omitted_messages"] += outcome["privacy_omitted_messages"]
                results["privacy_omitted_titles"] += int(outcome["privacy_omitted_title"])
            except (ValueError, PermissionError) as error:
                code = (
                    str(error)
                    if str(error).startswith(("capture.", "privacy.", "files.", "memory."))
                    else "capture.invalid_export"
                )
                results["failed"].append({"index": index + 1, "code": code})
                if code == "capture.not_authorized":
                    break
        return results
    finally:
        try:
            if root_id is not None:
                service.manage("revoke", CaptureInput(id=root_id))
        finally:
            if archive:
                archive.close()
