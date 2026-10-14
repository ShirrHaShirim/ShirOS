"""Local hook producer. Tokens stay in a user-owned file and never enter stdout."""

import argparse
import json
import re
import urllib.request
from urllib.parse import urlsplit

from shiros.capture import safe_path
from shiros.capture_adapters import normalize
from shiros.file_library import MAX_FILE_BYTES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--conversation", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    config = json.loads(safe_path(args.config).read_text(encoding="utf-8"))
    pairs = config if isinstance(config, list) else config.get("pairings", [config])
    pair = next(
        (
            x
            for x in pairs
            if x.get("provider") == args.provider and x.get("conversation_id") == args.conversation
        ),
        None,
    )
    if not pair:
        return
    endpoint = urlsplit(pair["endpoint"])
    if (
        endpoint.scheme != "http"
        or endpoint.hostname != "127.0.0.1"
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or not re.fullmatch(r"/capture/[0-9a-f-]{36}", endpoint.path)
    ):
        raise ValueError("capture.invalid_endpoint")
    source = safe_path(args.source)
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("capture.source_too_large")
    packet = normalize(
        args.provider,
        args.conversation,
        source.read_bytes(),
        include_files=pair.get("include_files", False),
        attachment_root=pair.get("attachment_root"),
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for n in range(0, len(packet.messages), 100):
        body = packet.model_copy(update={"messages": packet.messages[n : n + 100]}).model_dump(
            mode="json"
        )
        request = urllib.request.Request(
            pair["endpoint"],
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Capture-Token": pair["token"]},
        )
        with opener.open(request, timeout=60) as response:
            response.read()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Capture failed; check the selected transcript and ShirOS permission."
        ) from None
