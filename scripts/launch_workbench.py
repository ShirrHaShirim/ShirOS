"""Start local dependencies and open the owner's browser session without logging its token."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from shiros.browser_session import SESSION_ROOT, open_session

ROOT = Path(__file__).resolve().parents[1]


def running(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/workbench-health", timeout=1) as response:
            return bool(json.load(response).get("service") == "ShirOS Workbench")
    except (OSError, URLError, ValueError):
        return False


def main() -> None:
    os.chdir(ROOT)
    subprocess.run([sys.executable, "scripts/local_db.py", "start"], check=True)
    subprocess.run([sys.executable, "-m", "shiros.cli", "migrate"], check=True)
    try:
        state = json.loads((SESSION_ROOT / "session.json").read_text(encoding="utf-8"))
        if running(int(state["port"])):
            open_session()
            return
    except (OSError, ValueError, KeyError):
        pass
    port = 8001
    for candidate in range(8001, 8011):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            port = candidate
            break
    else:
        raise RuntimeError("No free loopback port")
    with (
        (ROOT / ".local/web.stdout.log").open("a") as stdout,
        (ROOT / ".local/web.stderr.log").open("a") as stderr,
    ):
        process = subprocess.Popen(
            [sys.executable, "-m", "shiros.cli", "web", "--port", str(port)],
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    for _ in range(40):
        if process.poll() is not None:
            raise RuntimeError("Workbench did not start")
        if running(port):
            open_session()
            return
        time.sleep(0.25)
    raise RuntimeError("Workbench startup timed out")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("ShirOS could not start. Check local database and port availability.")
        raise SystemExit(1) from None
