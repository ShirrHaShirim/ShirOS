"""Owner-only ephemeral browser capability; never put it in server logs or reports."""

import json
import os
import secrets
import subprocess
import webbrowser
from pathlib import Path

from shiros.identity import os_principal

SESSION_ROOT = Path(__file__).resolve().parents[2] / ".local" / "browser-session"


def start_session(port: int) -> str:
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    if SESSION_ROOT.is_symlink() or SESSION_ROOT.is_junction():
        raise PermissionError("permission.denied")
    if os.name == "nt":
        sid = os_principal().removeprefix("windows:")
        subprocess.run(
            [
                "icacls.exe",
                str(SESSION_ROOT),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(OI)(CI)F",
            ],
            check=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        SESSION_ROOT.chmod(0o700)
    token = secrets.token_urlsafe(32)
    target = SESSION_ROOT / "session.json"
    if target.is_symlink():
        raise PermissionError("permission.denied")
    target.write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o600)
    return token


def open_session() -> None:
    session = json.loads((SESSION_ROOT / "session.json").read_text(encoding="utf-8"))
    port, token = int(session["port"]), str(session["token"])
    if not 1024 <= port <= 65535 or len(token) != 43:
        raise ValueError("session.invalid")
    webbrowser.open(f"http://127.0.0.1:{port}/#token={token}")
