"""Portable UTF-8 export of authorized current memory revisions."""

import json
from typing import Any


def memory_markdown(memory: dict[str, Any], source: Any = None) -> str:
    title = str(memory.get("title") or memory.get("memory_id") or "记忆").replace("\n", " ")
    metadata = {
        key: memory.get(key)
        for key in (
            "memory_id",
            "id",
            "revision",
            "created_at",
            "domain",
            "provenance",
            "tags",
            "source_records",
        )
        if key in memory
    }
    # Indent JSON rather than fencing it: user text cannot close the metadata block.
    formatted = json.dumps(metadata, ensure_ascii=False, indent=2, default=str)
    result = f"# {title}\n\n## 记录信息\n\n" + "\n".join("    " + x for x in formatted.splitlines())
    result += "\n\n## 正文\n\n" + str(memory.get("text", "")) + "\n"
    if source is not None:
        result += (
            "\n## 原始来源\n\n"
            + "\n".join(
                "    " + x
                for x in json.dumps(source, ensure_ascii=False, indent=2, default=str).splitlines()
            )
            + "\n"
        )
    return result
