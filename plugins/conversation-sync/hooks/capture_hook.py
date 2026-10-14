"""Read hook metadata only; run the installed producer after exact-session pairing."""

import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    config = Path(
        os.environ.get("SHIROS_CAPTURE_CONFIG", str(Path.home() / ".config/ShirOS/capture.json"))
    )
    if not config.is_file():
        return
    data = json.loads(config.read_text(encoding="utf-8"))
    pairs = data if isinstance(data, list) else data.get("pairings", [data])
    hook = json.loads(sys.stdin.read())
    if not hook.get("transcript_path"):
        return
    pair = next(
        (
            x
            for x in pairs
            if x.get("provider") == "codex" and x.get("conversation_id") == hook.get("session_id")
        ),
        None,
    )
    if not pair:
        return
    runtime = pair.get("runtime")
    workspace = pair.get("workspace")
    if not runtime or not workspace:
        return
    subprocess.run(
        [
            runtime,
            "-m",
            "shiros.capture_producer",
            "--config",
            str(config),
            "--provider",
            "codex",
            "--conversation",
            hook["session_id"],
            "--source",
            hook["transcript_path"],
        ],
        cwd=workspace,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=8,
        check=False,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # Hook failure must not interrupt a conversation or print its contents.
