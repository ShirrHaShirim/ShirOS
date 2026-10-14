"""Narrow desktop bridge, available only to the local workbench origin."""

from collections.abc import Callable
from threading import Timer
from typing import Any
from urllib.parse import urlsplit


def ensure_borderless(window: Any) -> None:
    """Native animated/resizable window with its entire area used by the web UI."""
    from System import Action  # type: ignore[import-not-found]
    from System.Windows.Forms import FormBorderStyle, Screen  # type: ignore[import-not-found]

    def apply() -> None:
        import ctypes

        style = getattr(FormBorderStyle, "None")
        window.native.FormBorderStyle = style
        window.native.old_style = style
        screen = Screen.FromHandle(window.native.Handle)
        window.native.MaximizedBounds = (
            screen.Bounds if window.native.is_fullscreen else screen.WorkingArea
        )
        user = ctypes.windll.user32
        hwnd = window.native.Handle.ToInt64()
        user.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        _install_client_frame(window, hwnd)
        current = user.GetWindowLongW(hwnd, -16)
        # Preserve DWM's native behavior; the subclass removes the visible frame.
        updated = current | 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000
        user.SetWindowLongW(hwnd, -16, updated)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint,
        ]
        disabled = ctypes.c_int(0)
        dwm.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(disabled), ctypes.sizeof(disabled))
        user.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        if user.IsZoomed(hwnd):
            bounds = screen.Bounds if window.native.is_fullscreen else screen.WorkingArea
            user.SetWindowPos(
                hwnd, None, bounds.X, bounds.Y, bounds.Width, bounds.Height,
                0x0004 | 0x0010 | 0x0020,
            )
        else:
            user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)

    window.native.Invoke(Action(apply))


def _install_client_frame(window: Any, hwnd: int) -> None:
    import ctypes
    from ctypes import wintypes

    if getattr(window, "_shiros_frame_hwnd", None) == hwnd:
        return
    user, controls = ctypes.windll.user32, ctypes.windll.comctl32
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
        ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_size_t,
    )
    controls.DefSubclassProc.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
    ]
    controls.DefSubclassProc.restype = ctypes.c_ssize_t
    controls.SetWindowSubclass.argtypes = [
        ctypes.c_void_p, callback_type, ctypes.c_size_t, ctypes.c_size_t,
    ]
    controls.RemoveWindowSubclass.argtypes = [ctypes.c_void_p, callback_type, ctypes.c_size_t]
    user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
    user.IsZoomed.argtypes = [ctypes.c_void_p]
    user.GetDpiForWindow.argtypes = [ctypes.c_void_p]
    user.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user.MonitorFromWindow.restype = ctypes.c_void_p

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("screen", wintypes.RECT),
            ("work", wintypes.RECT), ("flags", wintypes.DWORD),
        ]

    user.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MonitorInfo)]

    def procedure(handle: int, message: int, wp: int, lp: int, ident: int, data: int) -> int:
        if message == 0x0083 and wp:  # WM_NCCALCSIZE: remove the visible standard frame.
            if user.IsZoomed(handle) and not window.native.is_fullscreen:
                info = MonitorInfo()
                info.size = ctypes.sizeof(info)
                if user.GetMonitorInfoW(user.MonitorFromWindow(handle, 2), ctypes.byref(info)):
                    client = ctypes.cast(lp, ctypes.POINTER(wintypes.RECT)).contents
                    client.left, client.top = info.work.left, info.work.top
                    client.right, client.bottom = info.work.right, info.work.bottom
            return 0
        if message == 0x0084:  # WM_NCHITTEST: resize the edges and corners.
            if not user.IsZoomed(handle) and not window.native.is_fullscreen:
                rect = wintypes.RECT()
                user.GetWindowRect(handle, ctypes.byref(rect))
                border = max(6, round(user.GetDpiForWindow(handle) * 6 / 96))
                x = ctypes.c_short(lp & 0xFFFF).value
                y = ctypes.c_short((lp >> 16) & 0xFFFF).value
                left, right = x < rect.left + border, x >= rect.right - border
                top, bottom = y < rect.top + border, y >= rect.bottom - border
                if top:
                    return 13 if left else 14 if right else 12
                if bottom:
                    return 16 if left else 17 if right else 15
                if left:
                    return 10
                if right:
                    return 11
            return 1  # HTCLIENT; the web navigation handles its controls and dragging.
        if message == 0x0082:  # WM_NCDESTROY
            controls.RemoveWindowSubclass(handle, callback, ident)
        return int(controls.DefSubclassProc(handle, message, wp, lp))

    callback = callback_type(procedure)
    if not controls.SetWindowSubclass(hwnd, callback, 0x534F53, 0):
        raise RuntimeError("desktop.custom_frame_failed")
    # Keep the native callback alive until the owning window is destroyed.
    window._shiros_frame_callback = callback
    window._shiros_frame_hwnd = hwnd


def native_minimize(window: Any) -> None:
    _native_show(window, 6)


def native_show(window: Any) -> None:
    _native_show(window, 9 if window.native.WindowState.ToString() == "Minimized" else 5)


def _native_show(window: Any, mode: int) -> None:
    import ctypes

    from System import Action

    def show() -> None:
        user = ctypes.windll.user32
        user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user.ShowWindow(window.native.Handle.ToInt64(), mode)

    window.native.Invoke(Action(show))


def is_maximized(window: Any) -> bool:
    from System.Windows.Forms import FormWindowState

    return bool(window.native.WindowState == FormWindowState.Maximized)


def drag_window(window: Any) -> None:
    _start_nonclient_action(window, 2)


def resize_window(window: Any, edge: str) -> None:
    hits = {"left": 10, "right": 11, "top": 12, "top-left": 13,
            "top-right": 14, "bottom": 15, "bottom-left": 16, "bottom-right": 17}
    if edge not in hits:
        raise ValueError("desktop.invalid_resize_edge")
    if window.native.is_fullscreen or is_maximized(window):
        return
    _start_nonclient_action(window, hits[edge])


def _start_nonclient_action(window: Any, hit: int) -> None:
    import ctypes

    from System import Action

    def drag() -> None:
        user = ctypes.windll.user32
        user.ReleaseCapture()
        user.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        user.SendMessageW(window.native.Handle.ToInt64(), 0x00A1, hit, 0)

    window.native.Invoke(Action(drag))


class DesktopSettings:
    def __init__(
        self,
        origin: str,
        current_url: Callable[[], str | None],
        background: Callable[[], bool],
        set_background: Callable[[bool], None] | None,
        quit_desktop: Callable[[], None],
        fullscreen: Callable[[], bool],
        set_fullscreen: Callable[[bool], None],
        minimize: Callable[[], None],
        close_window: Callable[[], None],
        maximize_restore: Callable[[], None] = lambda: None,
        drag: Callable[[], None] = lambda: None,
        resize: Callable[[str], None] = lambda edge: None,
        resizable: Callable[[], bool] = lambda: False,
        set_language: Callable[[str], None] | None = None,
    ) -> None:
        self._origin = urlsplit(origin)
        self._url = current_url
        self._background = background
        self._set_background = set_background
        self._quit = quit_desktop
        self._fullscreen = fullscreen
        self._set_fullscreen = set_fullscreen
        self._minimize = minimize
        self._close = close_window
        self._maximize = maximize_restore
        self._drag = drag
        self._resize = resize
        self._resizable = resizable
        self._set_language = set_language

    def _require_local(self) -> None:
        current = urlsplit(self._url() or "")
        if (current.scheme, current.hostname, current.port) != (
            self._origin.scheme,
            self._origin.hostname,
            self._origin.port,
        ):
            raise PermissionError("desktop.local_window_required")

    def set_interface_language(self, language: str) -> dict[str, Any]:
        self._require_local()
        if language not in ("zh", "en"):
            raise ValueError("desktop.invalid_language")
        if self._set_language is not None:
            self._set_language(language)
        return {"language": language, "supported": self._set_language is not None}

    def get_settings(self) -> dict[str, bool]:
        self._require_local()
        return {
            "background_mode": self._background(),
            "background_supported": self._set_background is not None,
            "fullscreen": self._fullscreen(),
            "resizable": self._resizable(),
        }

    def set_background_mode(self, enabled: bool) -> dict[str, bool]:
        self._require_local()
        if type(enabled) is not bool:
            raise ValueError("desktop.invalid_background_mode")
        if self._set_background is None:
            raise ValueError("desktop.background_unavailable")
        self._set_background(enabled)
        return self.get_settings()

    def quit_desktop(self) -> None:
        self._require_local()
        # Allow the bridge response to reach the window before it is destroyed.
        timer = Timer(0.2, self._quit)
        timer.daemon = True
        timer.start()

    def set_fullscreen(self, enabled: bool) -> dict[str, bool]:
        self._require_local()
        if type(enabled) is not bool:
            raise ValueError("desktop.invalid_fullscreen")
        self._set_fullscreen(enabled)
        return self.get_settings()

    def minimize_window(self) -> None:
        self._require_local()
        self._minimize()

    def maximize_window(self) -> dict[str, bool]:
        self._require_local()
        self._maximize()
        return self.get_settings()

    def drag_window(self) -> None:
        self._require_local()
        self._drag()

    def resize_window(self, edge: str) -> None:
        self._require_local()
        if edge not in {"left", "right", "top", "bottom", "top-left", "top-right",
                        "bottom-left", "bottom-right"}:
            raise ValueError("desktop.invalid_resize_edge")
        self._resize(edge)

    def close_window(self) -> None:
        self._require_local()
        timer = Timer(0.2, self._close)
        timer.daemon = True
        timer.start()
