"""Self-contained desktop composition; never consult repository credentials or data."""

import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run() -> None:
    import msvcrt

    import psycopg
    import uvicorn
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import URL, text

    from shiros.adapters.database.connection import make_engine
    from shiros.apps.workbench import create_workbench
    from shiros.identity import LocalIdentity, os_principal

    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    install = Path(sys.executable).parent if getattr(sys, "frozen", False) else bundle
    edition = json.loads((install / "edition.json").read_text(encoding="utf-8"))["edition"]
    if edition not in {"full", "core"}:
        raise ValueError("desktop.invalid_edition")
    data = (
        args.data_dir or Path(os.environ["LOCALAPPDATA"]) / "ShirOS-Desktop" / edition
    ).resolve()
    if data.is_symlink() or data.is_junction():
        raise PermissionError("desktop.invalid_data_dir")
    data.mkdir(parents=True, exist_ok=True)
    sid = os_principal().removeprefix("windows:")
    subprocess.run(
        ["icacls.exe", str(data), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    lock = (data / "desktop.lock").open("a+b")
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise RuntimeError("ShirOS is already running for this edition") from None
    os.chdir(data)
    postgres = bundle / "postgres"
    binary = postgres / "bin"
    env = dict(os.environ)
    env["PATH"] = str(binary) + os.pathsep + env.get("PATH", "")
    env.pop("PGPASSWORD", None)
    config_file = data / "database.json"
    if config_file.is_symlink():
        raise PermissionError("desktop.invalid_data_dir")
    if config_file.exists():
        config = json.loads(config_file.read_text(encoding="utf-8"))
    else:
        config = {"port": free_port(), "password": secrets.token_urlsafe(32)}
        config_file.write_text(json.dumps(config), encoding="utf-8")
    cluster = data / "pgdata"

    def pg(name: str, arguments: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(binary / (name + ".exe")), *arguments],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            check=check,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    engine: Any = None
    server: Any = None
    stop = threading.Event()
    try:
        if not (cluster / "PG_VERSION").exists():
            password_file = data / "initial-password"
            password_file.write_text(config["password"], encoding="ascii")
            try:
                pg(
                    "initdb",
                    [
                        "-D",
                        str(cluster),
                        "-U",
                        "shiros",
                        "--encoding=UTF8",
                        "--locale=C",
                        "--auth=scram-sha-256",
                        "--pwfile=" + str(password_file),
                    ],
                )
            finally:
                password_file.unlink(missing_ok=True)
        if pg("pg_ctl", ["-D", str(cluster), "status"], check=False).returncode:
            pg(
                "pg_ctl",
                [
                    "-D",
                    str(cluster),
                    "-l",
                    str(data / "postgres.log"),
                    "-w",
                    "start",
                    "-o",
                    f"-h 127.0.0.1 -p {int(config['port'])}",
                ],
            )
        kwargs = dict(
            host="127.0.0.1", port=int(config["port"]), user="shiros", password=config["password"]
        )
        with psycopg.connect(dbname="postgres", autocommit=True, **kwargs) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname='shiros'").fetchone():
                conn.execute("CREATE DATABASE shiros")
        engine = make_engine(
            URL.create(
                "postgresql+psycopg",
                database="shiros",
                **{
                    "host": kwargs["host"],
                    "port": kwargs["port"],
                    "username": kwargs["user"],
                    "password": kwargs["password"],
                },
            )
        )
        migrations = Config()
        migrations.set_main_option("script_location", str(bundle / "migrations"))
        with engine.connect() as connection:
            migrations.attributes["connection"] = connection
            command.upgrade(migrations, "head")
        identity = LocalIdentity(engine)
        scope = identity.bootstrap()
        port, token = free_port(), secrets.token_urlsafe(32)
        app = create_workbench(
            engine, identity, scope, port, token, music_enabled=edition == "full"
        )
        if args.smoke_test:
            from fastapi.testclient import TestClient

            client = TestClient(app, base_url=f"http://127.0.0.1:{port}")
            headers = {"X-Shiros-Token": token}
            session = client.post("/ui-api/session", json={}, headers=headers)
            files = client.post("/ui-api/files/list", json={}, headers=headers)
            music = client.post("/ui-api/music/list", json={}, headers=headers)
            with engine.connect() as c:
                counts = {
                    table: c.scalar(text("SELECT count(*) FROM " + table))
                    for table in (
                        "memories",
                        "memory_candidates",
                        "library_files",
                        "music_objects",
                        "sources",
                    )
                }
            result = {
                "edition": edition,
                "counts": counts,
                "session_status": session.status_code,
                "files_status": files.status_code,
                "music_status": music.status_code,
                "version": client.get("/workbench-health").json()["version"],
            }
            (data / "smoke-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            assert all(n == 0 for n in counts.values())
            assert session.status_code == files.status_code == 200
            assert music.status_code == (200 if edition == "full" else 404)
            return
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_config=None)
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(300):
            if server.started:
                break
            time.sleep(0.1)
        if not server.started:
            raise RuntimeError("desktop.server_failed")

        def watch() -> None:
            from shiros.file_library import MAX_FILE_BYTES, FileLibrary, FileRequest

            folder = Path(os.environ["USERPROFILE"]) / "Downloads" / "ShirOS-GPT"
            seen: dict[str, int] = {}
            while not stop.wait(5):
                if not folder.exists() or folder.is_symlink() or folder.is_junction():
                    continue
                for path in folder.glob("*.md"):
                    try:
                        if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
                            continue
                        stamp = path.stat().st_mtime_ns
                        if seen.get(path.name) == stamp:
                            continue
                        with engine.connect() as c:
                            actor = identity.current(c)
                        FileLibrary(engine, actor, scope).run(
                            "upload",
                            FileRequest(
                                filename=path.name,
                                data=base64.b64encode(path.read_bytes()).decode("ascii"),
                                external_key="chatgpt-browser:" + path.name,
                            ),
                        )
                        seen[path.name] = stamp
                    except Exception:
                        continue

        threading.Thread(target=watch, daemon=True).start()
        import webview

        webview.create_window(
            "ShirOS · " + ("Music" if edition == "full" else "Core"),
            f"http://127.0.0.1:{port}/#token={token}",
            width=1360,
            height=900,
            min_size=(900, 640),
        )
        webview.start(gui="edgechromium")
    finally:
        stop.set()
        if server:
            server.should_exit = True
            thread.join(timeout=10)
        if engine:
            engine.dispose()
        if (cluster / "PG_VERSION").exists():
            pg("pg_ctl", ["-D", str(cluster), "-w", "stop", "-m", "fast"], check=False)
        lock.close()


def main() -> None:
    try:
        run()
    except Exception as error:
        import ctypes

        if "--smoke-test" in sys.argv:
            import traceback

            if "--data-dir" in sys.argv:
                target = Path(sys.argv[sys.argv.index("--data-dir") + 1])
                target.mkdir(parents=True, exist_ok=True)
                (target / "smoke-error.json").write_text(
                    json.dumps(
                        {
                            "error_type": type(error).__name__,
                            "trace": [
                                {
                                    "file": Path(frame.filename).name,
                                    "line": frame.lineno,
                                    "function": frame.name,
                                }
                                for frame in traceback.extract_tb(error.__traceback__)
                            ],
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            raise SystemExit(1) from None

        # No SQL, credential, or data payloads in the dialog.
        code = type(error).__name__
        ctypes.windll.user32.MessageBoxW(
            None,
            "ShirOS 启动失败（" + code + "）。请检查 WebView2、磁盘空间和数据目录权限。",
            "ShirOS",
            16,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
