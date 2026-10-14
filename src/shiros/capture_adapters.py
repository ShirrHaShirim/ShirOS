"""Version-tolerant readers for user-selected transcripts, never credentials."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from shiros.capture import CaptureMessage, CaptureSnapshot, safe_path
from shiros.file_library import MAX_FILE_BYTES


def local_attachment(item: dict[str, Any], include_files: bool, root: str | None) -> dict[str, Any]:
    location = item.get("path") or item.get("url") or item.get("image_url") or item.get("uri") or ""
    if isinstance(location, dict):
        location = location.get("url", "")
    name = (
        item.get("filename")
        or item.get("name")
        or Path(urlsplit(location).path).name
        or "attachment.bin"
    )
    result = {
        "id": str(item.get("id") or hashlib.sha256(str(location or name).encode()).hexdigest()),
        "filename": str(name)[:250],
    }
    if location.startswith(("http://", "https://")):
        result["reference"] = location
    if include_files and location.startswith("data:"):
        head, _, data = location.partition(",")
        if ";base64" in head:
            result["data"] = data
    elif include_files and root and location:
        filename = unquote(urlsplit(location).path) if location.startswith("file:") else location
        if filename.startswith("/") and len(filename) > 3 and filename[2] == ":":
            filename = filename[1:]
        try:
            base = safe_path(root)
            path = safe_path(filename)
            if (
                path.is_relative_to(base)
                and path.is_file()
                and path.stat().st_size <= MAX_FILE_BYTES
            ):
                result["data"] = base64.b64encode(path.read_bytes()).decode("ascii")
        except (OSError, ValueError):
            pass
    return result


def text_parts(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = content.get("parts") or content.get("content") or []
    if not isinstance(content, list):
        return ""
    return "\n".join(
        x if isinstance(x, str) else str(x.get("text", ""))
        for x in content
        if isinstance(x, (str, dict))
    )


def normalize(
    provider: str,
    conversation: str,
    data: bytes,
    *,
    include_files: bool = False,
    attachment_root: str | None = None,
) -> CaptureSnapshot:
    source = data.decode("utf-8-sig")
    try:
        records = json.loads(source)
    except json.JSONDecodeError:
        records = []
        lines = source.splitlines()
        for n, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                if n != len(lines) - 1:
                    raise ValueError("capture.invalid_transcript") from None
    if isinstance(records, dict) and "messages" in records:
        return CaptureSnapshot.model_validate(
            {"provider": provider, "conversation_id": conversation, "messages": records["messages"]}
        )
    if isinstance(records, dict) and "mapping" in records:
        records = [records]
    if isinstance(records, dict) and "type" in records:
        records = [records]
    if not isinstance(records, list):
        raise ValueError("capture.unsupported_format")
    messages = []
    streams = {}
    settled = set()
    if provider in ("codex", "deepseek-harness"):
        for record in records:
            if isinstance(record, dict) and record.get("type") in ("session", "session_meta"):
                native_id = (record.get("payload") or record).get("id")
                if native_id and str(native_id) != conversation:
                    raise PermissionError("capture.conversation_mismatch")

    def add(item: dict[str, Any], fallback: str):
        role = item.get("role") or (item.get("author") or {}).get("role")
        if role not in ("user", "assistant", "tool") or item.get("channel") == "analysis":
            return
        content = item.get("content", item.get("text", ""))
        attachments = (
            item.get("attachments") or (item.get("metadata") or {}).get("attachments") or []
        )
        if isinstance(content, list):
            attachments = [
                *attachments,
                *[
                    x
                    for x in content
                    if isinstance(x, dict)
                    and x.get("type") in ("input_image", "image", "file", "input_file")
                ],
            ]
        timestamp = item.get("created_at") or item.get("create_time")
        observed = None
        if isinstance(timestamp, (int, float)):
            observed = datetime.fromtimestamp(
                timestamp / 1000 if timestamp > 10**11 else timestamp, UTC
            )
        elif timestamp:
            try:
                observed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            except ValueError:
                pass
        if observed and observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        messages.append(
            CaptureMessage(
                id=str(item.get("id") or fallback),
                role=role,
                text=text_parts(content),
                observed_at=observed,
                attachments=[
                    local_attachment(x, include_files, attachment_root)
                    for x in attachments
                    if isinstance(x, dict)
                ],
            )
        )

    if provider == "gpt" and records and isinstance(records[0], dict) and "mapping" in records[0]:
        selected = next(
            (x for x in records if str(x.get("id") or x.get("conversation_id")) == conversation),
            None,
        )
        if selected is None:
            raise ValueError("capture.conversation_not_found")
        for ident, node in selected["mapping"].items():
            if isinstance(node, dict) and isinstance(node.get("message"), dict):
                add(node["message"], str(ident))
    else:
        for n, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            kind = record.get("type", "")
            payload = record.get("payload") or record.get("data") or {}
            if provider == "codex":
                if kind == "response_item" and payload.get("type") == "message":
                    add(payload, f"codex-{n}")
                elif kind == "response_item" and payload.get("type") == "function_call_output":
                    add(
                        {
                            "id": payload.get("call_id") or f"tool-{n}",
                            "role": "tool",
                            "content": payload.get("output", ""),
                        },
                        f"tool-{n}",
                    )
            elif provider == "deepseek-harness":
                if kind in ("user/message", "assistant/message"):
                    item = payload.get("message") or payload
                    if kind == "assistant/message":
                        ident = f"assistant-{payload.get('turn', 0)}-{payload.get('step', n)}"
                        settled.add(ident)
                        streams.pop(ident, None)
                        item = {**item, "id": ident}
                    add({**item, "role": kind.split("/")[0]}, f"{kind}-{record.get('seq', n)}")
                elif kind == "assistant/attempt":
                    ident = f"assistant-{payload.get('turn', 0)}-{payload.get('step', n)}"
                    if ident not in settled:
                        streams[ident] = "".join(
                            "".join(x.get("texts", []))
                            for x in payload.get("stream", [])
                            if x.get("type") == "text-chunks"
                        )
                elif (
                    kind == "assistant/chunk"
                    and payload.get("chunk", {}).get("type") == "text-delta"
                ):
                    ident = f"assistant-{payload.get('turn', 0)}-{payload.get('step', n)}"
                    if ident not in settled:
                        streams[ident] = streams.get(ident, "") + payload["chunk"].get("text", "")
            else:
                if "role" in record or "author" in record:
                    add(record, f"message-{n}")
        for ident, value in streams.items():
            if value:
                messages.append(CaptureMessage(id=ident, role="assistant", text=value))
    if not messages:
        raise ValueError("capture.no_messages")
    return CaptureSnapshot(
        provider=provider,
        conversation_id=conversation,
        messages=messages,
        message_order=[m.id for m in messages],
    )
