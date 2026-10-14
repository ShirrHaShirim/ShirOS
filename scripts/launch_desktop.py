"""Open the existing local ShirOS workspace in a native window."""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
SESSION = LOCAL / "browser-session" / "session.json"
STATUS = LOCAL / "desktop-window-status.json"
SETTINGS = LOCAL / "desktop-settings.json"
CONTROL = LOCAL / "desktop-control.json"
ICON = ROOT / "desktop" / "shiros-icon.ico"


def send_action(action: str) -> None:
    temporary = CONTROL.with_name(f"desktop-control-{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"action": action}), encoding="utf-8")
    temporary.replace(CONTROL)


def session() -> tuple[int, str] | None:
    try:
        state = json.loads(SESSION.read_text(encoding="utf-8"))
        port, token = int(state["port"]), str(state["token"])
        if not 1024 <= port <= 65535 or len(token) != 43:
            return None
        with urlopen(f"http://127.0.0.1:{port}/workbench-health", timeout=2) as response:
            if json.load(response).get("service") != "ShirOS Workbench":
                return None
        request = Request(
            f"http://127.0.0.1:{port}/ui-api/session",
            data=b"{}",
            headers={"Content-Type": "application/json", "X-Shiros-Token": token},
        )
        with urlopen(request, timeout=3) as response:
            if response.status != 200:
                return None
        return port, token
    except (OSError, ValueError, KeyError):
        return None


def ensure_service() -> tuple[int, str]:
    current = session()
    if current:
        return current
    # Use existing credentials and data. Never initialize or replace a database.
    python = str(ROOT / ".venv" / "Scripts" / "python.exe")
    with (LOCAL / "desktop-service.log").open("a", encoding="utf-8") as log:
        subprocess.run(
            [python, str(ROOT / "scripts" / "local_db.py"), "start"],
            cwd=ROOT,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=True,
            timeout=60,
        )
        for port in range(8001, 8011):
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    continue
                break
        else:
            raise RuntimeError("desktop.no_free_port")
        process = subprocess.Popen(
            [python, "-m", "shiros.cli", "web", "--port", str(port)],
            cwd=ROOT,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError("desktop.service_failed")
        current = session()
        if current:
            return current
        time.sleep(0.25)
    raise RuntimeError("desktop.service_timeout")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--action", choices=["show", "close", "quit", "background-on", "background-off"]
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    import pystray  # type: ignore[import-untyped]
    import webview
    from PIL import Image

    if args.action:
        send_action(args.action)
        return

    port, token = ensure_service()
    if args.check:
        print("Existing workspace authenticated; desktop dependencies ready.")
        return
    import msvcrt

    lock = (LOCAL / "desktop-window.lock").open("a+b")
    if (LOCAL / "desktop-window.lock").stat().st_size == 0:
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        send_action("show")
        return
    CONTROL.unlink(missing_ok=True)
    try:
        preferences = json.loads(SETTINGS.read_text(encoding="utf-8"))
        background = preferences.get("background_mode", True) is True
        fullscreen = preferences.get("fullscreen", True) is True
        language = "en" if preferences.get("language") == "en" else "zh"
    except (OSError, ValueError, AttributeError):
        background = True
        fullscreen = True
        language = "zh"
    quitting = False
    stopped = threading.Event()
    tray_ready = threading.Event()
    write_lock = threading.Lock()
    view_state = "starting"

    # Persistent WebView state can include session storage; restrict it like the
    # existing owner-only browser capability directory, never like public assets.
    profile = LOCAL / "desktop-webview"
    profile.mkdir(parents=True, exist_ok=True)
    if profile.is_symlink() or profile.is_junction():
        raise PermissionError("desktop.invalid_profile")
    from shiros.identity import os_principal

    sid = os_principal().removeprefix("windows:")
    subprocess.run(
        ["icacls.exe", str(profile), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ShirOS.Workspace")

    from shiros.desktop_settings import (
        DesktopSettings,
        drag_window,
        ensure_borderless,
        is_maximized,
        native_minimize,
        native_show,
        resize_window,
    )

    desktop_api = DesktopSettings(
        f"http://127.0.0.1:{port}",
        lambda: current_window().get_current_url(),
        lambda: background,
        lambda enabled: set_background(enabled),
        lambda: quit_desktop(),
        lambda: fullscreen,
        lambda enabled: set_fullscreen(enabled),
        lambda: native_minimize(current_window()),
        lambda: current_window().destroy(),
        lambda: maximize_restore(),
        lambda: begin_drag(),
        lambda edge: resize_window(current_window(), edge),
        lambda: not fullscreen and not is_maximized(current_window()),
        set_language=lambda value: set_language(value),
    )
    window = webview.create_window(
        ("ShirOS · Workspace" if language == "en" else "ShirOS · 我的工作台"),
        f"http://127.0.0.1:{port}/#token={token}",
        width=1360,
        height=900,
        min_size=(900, 640),
        maximized=True,
        js_api=desktop_api,
        frameless=True,
        easy_drag=False,
        fullscreen=fullscreen,
    )
    assert window is not None

    def current_window() -> webview.Window:
        assert window is not None
        return window

    def save_settings() -> None:
        temporary = SETTINGS.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"background_mode": background, "fullscreen": fullscreen, "language": language}
            ),
            encoding="utf-8",
        )
        temporary.replace(SETTINGS)

    def set_language(value: str) -> None:
        nonlocal language
        language = value
        title = "ShirOS · Workspace" if language == "en" else "ShirOS · 我的工作台"
        window.set_title(title)
        tray.title = title
        tray.update_menu()
        save_settings()

    def set_fullscreen(enabled: bool) -> None:
        nonlocal fullscreen
        if fullscreen != enabled:
            window.toggle_fullscreen()
            fullscreen = enabled
        ensure_borderless(window)
        save_settings()
        record(view_state)

    def maximize_restore() -> None:
        if fullscreen:
            set_fullscreen(False)
            window.maximize()
        elif is_maximized(window):
            window.restore()
        else:
            window.maximize()
        ensure_borderless(window)

    def begin_drag() -> None:
        if fullscreen:
            set_fullscreen(False)
        if is_maximized(window):
            window.restore()
        ensure_borderless(window)
        drag_window(window)

    def record(state: str) -> None:
        nonlocal view_state
        with write_lock:
            view_state = state
            temporary = STATUS.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "state": state,
                        "pid": os.getpid(),
                        "port": port,
                        "background_mode": background,
                        "fullscreen": fullscreen,
                        "tray_ready": tray_ready.is_set(),
                    }
                ),
                encoding="utf-8",
            )
            temporary.replace(STATUS)

    def show() -> None:
        was_background = view_state == "background"
        native_show(window)
        if was_background and not fullscreen:
            window.maximize()
        ensure_borderless(window)
        record("loaded")

    def set_background(enabled: bool) -> None:
        nonlocal background
        background = enabled
        save_settings()
        tray.update_menu()
        if not enabled and view_state == "background":
            show()
        else:
            record(view_state)

    def quit_desktop() -> None:
        nonlocal quitting
        quitting = True
        window.destroy()

    def closing() -> bool:
        if background and not quitting and tray_ready.is_set():
            window.hide()
            record("background")
            return False
        return True

    def closed() -> None:
        stopped.set()
        record("closed")
        tray.stop()

    tray = pystray.Icon(
        "ShirOS-workspace",
        Image.open(ICON),
        ("ShirOS · Workspace" if language == "en" else "ShirOS · 我的工作台"),
        menu=pystray.Menu(
            pystray.MenuItem(
                lambda item: "Open workspace" if language == "en" else "打开工作台",
                lambda: show(),
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
                lambda item: "Quit ShirOS" if language == "en" else "退出桌面程序",
                lambda: quit_desktop(),
            ),
        ),
    )

    def tray_setup(icon: pystray.Icon) -> None:
        icon.visible = True
        tray_ready.set()

    def watch_controls() -> None:
        threading.Thread(target=tray.run, kwargs={"setup": tray_setup}, daemon=True).start()
        while not stopped.wait(0.2):
            if not CONTROL.exists():
                continue
            try:
                action = json.loads(CONTROL.read_text(encoding="utf-8"))["action"]
                CONTROL.unlink(missing_ok=True)
                if action == "show":
                    show()
                elif action == "close":
                    window.destroy()
                elif action == "quit":
                    quit_desktop()
                elif action in ("background-on", "background-off"):
                    set_background(action == "background-on")
            except (OSError, ValueError, KeyError):
                CONTROL.unlink(missing_ok=True)

    window.events.loaded += lambda: ensure_borderless(window)
    window.events.loaded += lambda: record("loaded")
    window.events.closing += closing
    window.events.closed += closed
    record("starting")
    # Closing the UI leaves the shared database and MCP services running.
    try:
        webview.start(
            watch_controls,
            gui="edgechromium",
            private_mode=False,
            storage_path=str(profile),
            icon=str(ICON),
        )
    finally:
        stopped.set()
        tray.stop()
        lock.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        STATUS.write_text(
            json.dumps({"state": "failed", "error_type": type(error).__name__}),
            encoding="utf-8",
        )
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "ShirOS could not open its desktop window. Check the local service and WebView2.",
            "ShirOS",
            0x10,
        )
        sys.exit(1)
