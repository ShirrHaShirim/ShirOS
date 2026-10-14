"""The window bridge must not expose desktop controls to external pages."""

import pytest

from shiros.desktop_settings import DesktopSettings


def test_desktop_settings_origin_and_boolean_validation() -> None:
    state = {"url": "http://127.0.0.1:8001/", "background": True, "fullscreen": True}
    calls: list[tuple[str, bool]] = []
    resize_calls: list[str] = []
    languages: list[str] = []
    api = DesktopSettings(
        "http://127.0.0.1:8001",
        lambda: str(state["url"]),
        lambda: bool(state["background"]),
        lambda value: calls.append(("background", value)),
        lambda: None,
        lambda: bool(state["fullscreen"]),
        lambda value: calls.append(("fullscreen", value)),
        lambda: None,
        lambda: None,
        resize=resize_calls.append,
        resizable=lambda: True,
        set_language=languages.append,
    )
    assert api.get_settings()["fullscreen"]
    api.set_background_mode(False)
    api.set_fullscreen(False)
    assert calls == [("background", False), ("fullscreen", False)]
    api.resize_window("bottom-right")
    assert resize_calls == ["bottom-right"]
    api.set_interface_language("en")
    api.set_interface_language("zh")
    assert languages == ["en", "zh"]
    with pytest.raises(ValueError):
        api.set_interface_language("invalid")
    with pytest.raises(ValueError):
        api.resize_window("invalid")
    with pytest.raises(ValueError):
        api.set_background_mode("false")  # type: ignore[arg-type]
    for url in ("https://example.com/", "http://127.0.0.1:8002/", "file:///page.html"):
        state["url"] = url
        with pytest.raises(PermissionError):
            api.get_settings()
        with pytest.raises(PermissionError):
            api.set_fullscreen(True)
        with pytest.raises(PermissionError):
            api.close_window()
        with pytest.raises(PermissionError):
            api.resize_window("right")
        with pytest.raises(PermissionError):
            api.set_interface_language("en")
    assert len(calls) == 2
    assert languages == ["en", "zh"]
