from __future__ import annotations

from omnidesk.utils import windows_theme


class _FakeWidget:
    def __init__(self, hwnd: int | None) -> None:
        self._hwnd = hwnd
        self.win_id_calls = 0

    def winId(self) -> int | None:
        self.win_id_calls += 1
        return self._hwnd


def test_apply_dark_title_bar_ignores_non_windows(monkeypatch) -> None:
    widget = _FakeWidget(123)
    calls: list[int] = []
    monkeypatch.setattr(windows_theme.sys, "platform", "linux")
    monkeypatch.setattr(windows_theme, "_set_dark_mode_for_hwnd", calls.append)

    windows_theme.apply_dark_title_bar(widget)  # type: ignore[arg-type]

    assert widget.win_id_calls == 0
    assert calls == []


def test_apply_dark_title_bar_ignores_missing_hwnd(monkeypatch) -> None:
    widget = _FakeWidget(None)
    calls: list[int] = []
    monkeypatch.setattr(windows_theme.sys, "platform", "win32")
    monkeypatch.setattr(windows_theme, "_set_dark_mode_for_hwnd", calls.append)

    windows_theme.apply_dark_title_bar(widget)  # type: ignore[arg-type]

    assert widget.win_id_calls == 1
    assert calls == []


def test_apply_dark_title_bar_passes_hwnd_on_windows(monkeypatch) -> None:
    widget = _FakeWidget(456)
    calls: list[int] = []
    monkeypatch.setattr(windows_theme.sys, "platform", "win32")
    monkeypatch.setattr(windows_theme, "_set_dark_mode_for_hwnd", calls.append)

    windows_theme.apply_dark_title_bar(widget)  # type: ignore[arg-type]

    assert calls == [456]


def test_set_dark_mode_returns_when_dwmapi_is_missing(monkeypatch) -> None:
    class _FakeWindll:
        dwmapi = None

    monkeypatch.setattr(windows_theme.ctypes, "windll", _FakeWindll(), raising=False)

    windows_theme._set_dark_mode_for_hwnd(123)


def test_set_dark_mode_tries_fallback_attribute_until_success(monkeypatch) -> None:
    class _FakeDwmApi:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def DwmSetWindowAttribute(self, _hwnd, attribute, _value, _size) -> int:
            self.calls.append(attribute.value)
            return 0 if attribute.value == 19 else 1

    class _FakeWindll:
        def __init__(self) -> None:
            self.dwmapi = _FakeDwmApi()

    fake_windll = _FakeWindll()
    monkeypatch.setattr(windows_theme.ctypes, "windll", fake_windll, raising=False)

    windows_theme._set_dark_mode_for_hwnd(123)

    assert fake_windll.dwmapi.calls == [20, 19]
