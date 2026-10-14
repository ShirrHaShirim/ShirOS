"""Reconnect the managed ShirOS tunnel runtime with a correctly quoted MCP command."""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HEALTH_FILE = Path.home() / ".local" / "state" / "tunnel-client" / "health" / "shiros.url"


def _log(message: str) -> None:
    print(message, flush=True)


def _health_url() -> str | None:
    try:
        return HEALTH_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _probe(url: str, path: str) -> str | None:
    try:
        with urllib.request.urlopen(url + path, timeout=3) as response:
            payload: str = response.read().decode("utf-8", "replace")
        return payload.strip()
    except (OSError, urllib.error.URLError, ValueError):
        return None


def _wait_ready(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = _health_url()
        if url is not None and _probe(url, "/readyz") == "ready":
            _log(f"Tunnel runtime ready at {url}")
            return True
        time.sleep(1.0)
    return False


def _stop_runtime(tunnel_client: Path, alias: str) -> None:
    subprocess.run(
        [str(tunnel_client), "runtimes", "stop", alias],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        url = _health_url()
        if url is None or _probe(url, "/readyz") is None:
            return
        time.sleep(1.0)
    subprocess.run(["taskkill", "/IM", "tunnel-client.exe", "/F"], check=False)


def _ensure_mcp_command(profile_path: Path, mcp_command: str) -> None:
    data: dict[str, Any] = json.loads(profile_path.read_text(encoding="utf-8"))
    commands = data.get("mcp", {}).get("commands")
    if not commands or commands[0].get("command") == mcp_command:
        return
    commands[0]["command"] = mcp_command
    profile_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _log("Updated the tunnel profile MCP command")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", default="shiros")
    parser.add_argument("--profile", default="shiros-chatgpt")
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--tunnel-id", required=True)
    parser.add_argument("--client", default="chatgpt")
    args = parser.parse_args()
    if not os.environ.get("CONTROL_PLANE_API_KEY"):
        raise SystemExit("CONTROL_PLANE_API_KEY is not set.")
    tunnel_client = ROOT / ".local" / "tunnel-client" / "bin" / "tunnel-client.exe"
    if not tunnel_client.is_file():
        raise SystemExit("Tunnel client is not installed.")
    profile_path = Path(args.profile_dir) / f"{args.profile}.yaml"
    if not profile_path.is_file():
        raise SystemExit(
            f"Tunnel profile {profile_path} is missing; restore it with a managed connect first."
        )
    # pythonw.exe is a GUI-subsystem process: it never attaches to the console,
    # so closing the operator terminal cannot kill the bridge with CTRL_CLOSE.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.is_file() else Path(sys.executable)
    mcp_command = f'"{interpreter.as_posix()}" -m shiros.memory_bridge serve --client {args.client}'
    _ensure_mcp_command(profile_path, mcp_command)
    _stop_runtime(tunnel_client, args.alias)
    HEALTH_FILE.unlink(missing_ok=True)
    # DETACHED_PROCESS keeps the runtime off the operator console, so closing
    # the terminal cannot deliver console control events to it or its bridge.
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(
        [str(tunnel_client), "run", "--config", str(profile_path.resolve())],
        cwd=ROOT,
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )
    if not _wait_ready(timeout=60):
        _log(
            "Tunnel runtime did not become ready; check "
            "$HOME/.local/state/tunnel-client/logs/shiros.log"
        )
        raise SystemExit(1)
    url = _health_url()
    if url is not None:
        _log(f"health: {url} | readyz={_probe(url, '/readyz')}")


if __name__ == "__main__":
    main()
