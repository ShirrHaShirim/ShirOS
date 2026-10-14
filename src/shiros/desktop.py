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

STARTUP_DATA_DIR: Path | None = None


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _trace(error: BaseException) -> list[dict[str, Any]]:
    import traceback

    return [
        {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
        for frame in traceback.extract_tb(error.__traceback__)
    ]


def _write_startup_error(error: BaseException) -> Path | None:
    """Persist a diagnostics file outside the message box; never any payloads."""
    payload = {"error_type": type(error).__name__, "trace": _trace(error)}
    targets: list[Path] = []
    if STARTUP_DATA_DIR is not None:
        targets.append(STARTUP_DATA_DIR / "startup-error.json")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        targets.append(Path(local) / "ShirOS-Desktop" / "startup-error.json")
    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return target
        except OSError:
            continue
    return None


def _append_error_log(data_dir: Path | None, context: str, error: BaseException) -> None:
    if data_dir is None:
        return
    entry = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "context": context,
        "error_type": type(error).__name__,
        "trace": _trace(error),
    }
    try:
        with (data_dir / "desktop-errors.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _signal_running_instance(data: Path) -> None:
    """Let a double launch surface the window that is already running."""
    control = data / "desktop-control.json"
    temporary = control.with_name("desktop-control-second.json")
    try:
        temporary.write_text(json.dumps({"action": "show"}), encoding="utf-8")
        temporary.replace(control)
    except OSError:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "ShirOS 已在运行，请从任务栏或系统托盘打开窗口。\n\n"
            "ShirOS is already running. Open it from the taskbar or system tray.",
            "ShirOS",
            64,
        )


def _webview2_missing() -> bool:
    import winreg

    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\Microsoft"),
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft"),
        (winreg.HKEY_CURRENT_USER, "Software\\Microsoft"),
    )
    for root, base in roots:
        path = base + "\\EdgeUpdate\\Clients\\" + guid
        try:
            with winreg.OpenKey(root, path) as handle:
                version, _ = winreg.QueryValueEx(handle, "pv")
        except OSError:
            continue
        if isinstance(version, str) and version not in ("", "0.0.0.0"):
            return False
    return True


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
    parser.add_argument(
        "--headless-test",
        action="store_true",
        help="Serve local UI for isolated browser verification",
    )
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    install = Path(sys.executable).parent if getattr(sys, "frozen", False) else bundle
    package = json.loads((install / "edition.json").read_text(encoding="utf-8"))
    edition = package["edition"]
    if edition not in {"full", "core"}:
        raise ValueError("desktop.invalid_edition")
    profile_name = "test" if package.get("test_catalogue") is True else edition
    data = (
        args.data_dir or Path(os.environ["LOCALAPPDATA"]) / "ShirOS-Desktop" / profile_name
    ).resolve()
    if data.is_symlink() or data.is_junction():
        raise PermissionError("desktop.invalid_data_dir")
    data.mkdir(parents=True, exist_ok=True)
    global STARTUP_DATA_DIR
    STARTUP_DATA_DIR = data
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
        # Installer auto-start plus a manual double-click (or a relaunch while the
        # app keeps running in the tray) must not fail with an unrelated error
        # dialog: hand the request to the running instance and exit quietly.
        lock.close()
        _signal_running_instance(data)
        return
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
        seed_result = None
        if package.get("test_catalogue") is True:
            from shiros.music_seed import import_music_seed

            with engine.connect() as c:
                actor = identity.current(c)
            seed_result = import_music_seed(engine, actor, scope, install / "music-seed.json.gz")
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
                        "music_reviews",
                        "music_memberships",
                        "music_playback_sources",
                        "music_review_deletions",
                        "music_images",
                        "capture_grants",
                        "capture_entries",
                        "memory_tags",
                        "client_capability_grants",
                    )
                }
            result = {
                "edition": edition,
                "counts": counts,
                "session_status": session.status_code,
                "files_status": files.status_code,
                "music_status": music.status_code,
                "version": client.get("/workbench-health").json()["version"],
                "seed": seed_result,
            }
            (data / "smoke-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            private = (
                "memories",
                "memory_candidates",
                "library_files",
                "capture_grants",
                "capture_entries",
                "memory_tags",
                "client_capability_grants",
            )
            assert all(counts[table] == 0 for table in private)
            if package.get("test_catalogue") is True:
                assert counts["music_objects"] > 0 and counts["music_images"] > 0
                assert counts["music_reviews"] == 0
                assert counts["music_memberships"] == 0
                assert counts["music_playback_sources"] == 0
                assert counts["music_review_deletions"] == 0
            else:
                assert counts["music_objects"] == counts["sources"] == 0
            assert session.status_code == files.status_code == 200
            assert music.status_code == (200 if edition == "full" else 404)
            return
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_config=None)
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        # The first start on a slow or virus-scanned machine can be much slower
        # than development hardware; wait before declaring a failure.
        for _ in range(1200):
            if server.started:
                break
            time.sleep(0.1)
        if not server.started:
            raise RuntimeError("desktop.server_failed")
        (data / "session.json").write_text(
            json.dumps({"port": port, "token": token}), encoding="utf-8"
        )
        if args.headless_test:
            while not (data / "quit-test").exists():
                time.sleep(0.2)
            (data / "quit-test").unlink()
            return

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

        # Imported conversations require an explicit choice on tester installations.
        if package.get("test_catalogue") is not True:
            threading.Thread(target=watch, daemon=True).start()
        if _webview2_missing():
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                "缺少 Microsoft Edge WebView2 Runtime。\n"
                "请从微软官方下载安装后重新打开 ShirOS：\n"
                "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                "Microsoft Edge WebView2 Runtime is required. Install it from "
                "Microsoft, then open ShirOS again.",
                "ShirOS",
                16,
            )
            return
        import pystray
        import webview
        from PIL import Image

        from shiros.desktop_settings import (
            DesktopSettings,
            drag_window,
            ensure_borderless,
            is_maximized,
            native_minimize,
            native_show,
            resize_window,
        )

        preferences = data / "desktop-settings.json"
        try:
            settings = json.loads(preferences.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        fullscreen = settings.get("fullscreen", True) is True
        background = settings.get("background_mode", True) is True
        language = "en" if settings.get("language") == "en" else "zh"
        quitting = False
        tray_ready = threading.Event()

        def save_settings() -> None:
            preferences.write_text(
                json.dumps(
                    {"fullscreen": fullscreen, "background_mode": background, "language": language}
                ),
                encoding="utf-8",
            )

        def set_language(value: str) -> None:
            nonlocal language
            language = value
            title = "ShirOS · Workspace" if language == "en" else "ShirOS · 我的工作台"
            current_window().set_title(title)
            tray.title = title
            tray.update_menu()
            save_settings()

        def set_background(enabled: bool) -> None:
            nonlocal background
            background = enabled
            save_settings()
            tray.update_menu()
            if not enabled:
                show()

        def show() -> None:
            current_window().show()
            native_show(current_window())
            ensure_borderless(current_window())

        def quit_desktop() -> None:
            nonlocal quitting
            quitting = True
            current_window().destroy()

        def closing() -> bool:
            if background and not quitting and tray_ready.is_set():
                current_window().hide()
                return False
            return True

        def set_fullscreen(enabled: bool) -> None:
            nonlocal fullscreen
            if fullscreen != enabled:
                current_window().toggle_fullscreen()
                fullscreen = enabled
            ensure_borderless(current_window())
            save_settings()

        def maximize_restore() -> None:
            if fullscreen:
                set_fullscreen(False)
                current_window().maximize()
            elif is_maximized(current_window()):
                current_window().restore()
            else:
                current_window().maximize()
            ensure_borderless(current_window())

        def begin_drag() -> None:
            if fullscreen:
                set_fullscreen(False)
            if is_maximized(current_window()):
                current_window().restore()
            ensure_borderless(current_window())
            drag_window(current_window())

        desktop_api = DesktopSettings(
            f"http://127.0.0.1:{port}",
            lambda: current_window().get_current_url(),
            lambda: background,
            set_background,
            quit_desktop,
            lambda: fullscreen,
            set_fullscreen,
            lambda: native_minimize(current_window()),
            lambda: current_window().destroy(),
            maximize_restore,
            begin_drag,
            lambda edge: resize_window(current_window(), edge),
            lambda: not fullscreen and not is_maximized(current_window()),
            set_language=set_language,
        )
        window = webview.create_window(
            "ShirOS · Workspace" if language == "en" else "ShirOS · 我的工作台",
            f"http://127.0.0.1:{port}/#token={token}",
            width=1360,
            height=900,
            min_size=(900, 640),
            js_api=desktop_api,
            fullscreen=fullscreen,
            frameless=True,
            easy_drag=False,
        )
        assert window is not None

        def current_window() -> webview.Window:
            assert window is not None
            return window

        icon = bundle / "shiros-icon.ico"
        tray = pystray.Icon(
            "ShirOS-" + profile_name,
            Image.open(icon),
            "ShirOS",
            menu=pystray.Menu(
                pystray.MenuItem(
                    lambda item: "Open workspace" if language == "en" else "打开工作台",
                    show,
                    default=True,
                ),
                pystray.MenuItem(
                    lambda item: (
                        "Keep running in background" if language == "en" else "关闭窗口后在后台运行"
                    ),
                    lambda: set_background(not background),
                    checked=lambda item: background,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    lambda item: "Quit ShirOS" if language == "en" else "退出桌面程序", quit_desktop
                ),
            ),
        )

        def tray_setup(instance: Any) -> None:
            instance.visible = True
            tray_ready.set()

        def start_tray() -> None:
            threading.Thread(target=tray.run, kwargs={"setup": tray_setup}, daemon=True).start()
            # Local owner-only control file makes automated verification close gracefully.
            while not stop.wait(0.2):
                control = data / "desktop-control.json"
                if not control.exists():
                    continue
                try:
                    action = json.loads(control.read_text(encoding="utf-8")).get("action")
                    control.unlink()
                    if action == "quit":
                        quit_desktop()
                    elif action == "show":
                        show()
                except (OSError, ValueError):
                    pass

        def apply_frame() -> None:
            # A custom-frame failure must not take the whole app down after the
            # window has opened; log it and keep the default native frame.
            try:
                ensure_borderless(current_window())
            except Exception as error:
                _append_error_log(data, "borderless", error)

        window.events.loaded += apply_frame
        window.events.closing += closing
        window.events.closed += lambda: tray.stop()
        try:
            webview.start(
                start_tray,
                gui="edgechromium",
                private_mode=False,
                storage_path=str(data / "webview"),
                icon=str(icon),
            )
        finally:
            tray.stop()
    finally:
        stop.set()
        (data / "session.json").unlink(missing_ok=True)
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

        log_path = _write_startup_error(error)
        if "--smoke-test" in sys.argv or "--headless-test" in sys.argv:
            if "--data-dir" in sys.argv:
                target = Path(sys.argv[sys.argv.index("--data-dir") + 1])
                target.mkdir(parents=True, exist_ok=True)
                (target / "smoke-error.json").write_text(
                    json.dumps(
                        {"error_type": type(error).__name__, "trace": _trace(error)},
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            raise SystemExit(1) from None

        # No SQL, credential, or data payloads in the dialog; the diagnostics
        # file carries file/line information for support.
        code = type(error).__name__
        hint = "\n诊断文件 / Diagnostics: " + str(log_path) if log_path else ""
        ctypes.windll.user32.MessageBoxW(
            None,
            "ShirOS 启动失败（"
            + code
            + "）。\n请确认已安装 Microsoft Edge WebView2，并检查磁盘空间与数据目录权限。"
            + hint
            + "\n\nShirOS could not start ("
            + code
            + "). Check WebView2, disk space and data folder permissions.",
            "ShirOS",
            16,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
