"""Preserve the original export plus all text nodes, including alternate branches."""

import io
import json
import zipfile
from typing import Any

from shiros.file_library import MAX_FILE_BYTES, safe_filename


def conversation_files(data: bytes, filename: str) -> list[tuple[str, bytes]]:
    try:
        if filename.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = [x for x in archive.infolist() if x.filename == "conversations.json"]
                if len(entries) != 1 or entries[0].file_size > MAX_FILE_BYTES:
                    raise ValueError
                data = archive.read(entries[0])
        parsed = json.loads(data.decode("utf-8-sig"))
        conversations = parsed if isinstance(parsed, list) else [parsed]
        if not 1 <= len(conversations) <= 1000:
            raise ValueError
        result: list[tuple[str, bytes]] = []
        for index, item in enumerate(conversations):
            if not isinstance(item, dict) or not isinstance(item.get("mapping"), dict):
                raise ValueError
            title = str(item.get("title") or "ChatGPT 对话")
            header = {
                k: item.get(k)
                for k in ("id", "conversation_id", "create_time", "update_time", "current_node")
            }
            lines = [
                "# " + title.replace("\n", " "),
                "",
                "来源：ChatGPT 数据导出。保留全部消息节点和分支；非文本附件见原始导出文件。",
                "",
                "    " + json.dumps(header, ensure_ascii=False),
                "",
            ]
            nodes: list[tuple[str, dict[str, Any]]] = list(item["mapping"].items())
            for node_id, node in nodes:
                if not isinstance(node, dict):
                    raise ValueError
                message = node.get("message")
                if not isinstance(message, dict):
                    continue
                role = message.get("author", {}).get("role", "unknown")
                lines += [
                    f"## {role}",
                    "",
                    "    "
                    + json.dumps(
                        {
                            "node_id": node_id,
                            "parent": node.get("parent"),
                            "children": node.get("children"),
                            "create_time": message.get("create_time"),
                            "metadata": message.get("metadata"),
                        },
                        ensure_ascii=False,
                    ),
                    "",
                ]
                content = message.get("content", {})
                parts = content.get("parts", []) if isinstance(content, dict) else []
                for part in parts:
                    lines.append(
                        part
                        if isinstance(part, str)
                        else "附件/结构化内容：" + json.dumps(part, ensure_ascii=False)
                    )
                if not parts:
                    lines.append(json.dumps(content, ensure_ascii=False))
                lines.append("")
            encoded = "\n".join(lines).encode("utf-8")
            if len(encoded) > MAX_FILE_BYTES:
                raise ValueError
            result.append((safe_filename(f"{index + 1:04d}-{title}.md"), encoded))
        if sum(len(x[1]) for x in result) > 100 * 1024 * 1024:
            raise ValueError
        return result
    except (ValueError, TypeError, KeyError, AttributeError, zipfile.BadZipFile, RuntimeError):
        raise ValueError("files.invalid_conversations") from None
