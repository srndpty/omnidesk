from __future__ import annotations

from pathlib import Path
from typing import cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

import omnidesk.ui.main_window as main_window_module
from omnidesk.ui.main_window import MainWindow


class FakeTab(QWidget):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self.calls: list[str] = []

    def current_path(self) -> Path:
        return self._path

    def navigate_to(self, path: Path) -> None:
        self.calls.append(f"navigate:{path}")
        self._path = path

    def go_up(self) -> None:
        self.calls.append("go_up")

    def refresh(self) -> None:
        self.calls.append("refresh")

    def focus_view(self) -> None:
        self.calls.append("focus")


class FakeTabContainer(QWidget):
    currentPathChanged = pyqtSignal(Path)
    tabCountChanged = pyqtSignal(int)
    nameColumnWidthChanged = pyqtSignal(int)

    def __init__(self, parent=None, *, name_column_width=None):
        super().__init__(parent)
        self.name_column_width = name_column_width
        self.tabs: list[FakeTab] = []
        self.calls: list[str] = []

    def open_in_new_tab(self, path: Path) -> FakeTab:
        tab = FakeTab(path)
        self.tabs.append(tab)
        self.tabCountChanged.emit(len(self.tabs))
        return tab

    def current_tab(self) -> FakeTab | None:
        return self.tabs[-1] if self.tabs else None

    def close_current_tab(self) -> None:
        self.calls.append("close")
        if len(self.tabs) > 1:
            self.tabs.pop()
            self.tabCountChanged.emit(len(self.tabs))

    def tab_count(self) -> int:
        return len(self.tabs)

    def tab_paths(self) -> list[Path]:
        return [tab.current_path() for tab in self.tabs]

    def navigate_current_to(self, path: Path) -> None:
        self.calls.append(f"navigate:{path}")
        tab = self.current_tab()
        if tab is not None:
            tab.navigate_to(path)

    def refresh(self) -> None:
        self.calls.append("refresh")

    def go_up(self) -> None:
        self.calls.append("go_up")

    def focus_current(self) -> None:
        self.calls.append("focus")

    def select_next_tab(self) -> None:
        self.calls.append("next")

    def select_previous_tab(self) -> None:
        self.calls.append("previous")


class FakeColumnBrowser(QWidget):
    currentPathChanged = pyqtSignal(Path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = Path.cwd()
        self.calls: list[str] = []

    def set_root_path(self, path: Path) -> None:
        self.calls.append(f"set_root:{path}")
        self._path = path

    def current_path(self) -> Path:
        return self._path

    def refresh(self) -> None:
        self.calls.append("refresh")

    def go_up(self) -> None:
        self.calls.append("go_up")

    def focus_view(self) -> None:
        self.calls.append("focus")


def _patch_main_window(monkeypatch, settings: dict, default_path: Path, saved: list[dict]) -> None:
    monkeypatch.setattr(main_window_module, "TabContainer", FakeTabContainer)
    monkeypatch.setattr(main_window_module, "ColumnBrowser", FakeColumnBrowser)
    monkeypatch.setattr(main_window_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_window_module, "save_settings", lambda data: saved.append(dict(data)))
    monkeypatch.setattr(main_window_module, "get_default_start_path", lambda: default_path)
    monkeypatch.setattr(main_window_module, "apply_dark_title_bar", lambda _window: None)


def test_main_window_restores_default_state(monkeypatch, qtbot, tmp_path: Path) -> None:
    saved: list[dict] = []
    _patch_main_window(monkeypatch, {}, tmp_path, saved)

    window = MainWindow()
    qtbot.addWidget(window)

    assert window._is_tab_mode()
    assert window._current_active_path() == tmp_path
    assert window._tab_container.tab_count() == 1
    assert window._column_browser.current_path() == tmp_path
    assert "OmniDesk" in window.windowTitle()


def test_main_window_restores_column_session(monkeypatch, qtbot, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    settings = {
        "file_browser": {"name_column_width": 222},
        "session": {"tabs": [str(first), str(second)], "view_mode": "columns"},
    }
    saved: list[dict] = []
    _patch_main_window(monkeypatch, settings, tmp_path, saved)

    window = MainWindow()
    qtbot.addWidget(window)

    assert not window._is_tab_mode()
    assert window._tab_container.name_column_width == 222
    assert window._tab_container.tab_paths() == [first, second]
    assert window._column_browser.current_path() == second
    assert window._toggle_view_action.text() == "Switch to Tab View"


def test_main_window_handlers_delegate_by_mode(monkeypatch, qtbot, tmp_path: Path) -> None:
    saved: list[dict] = []
    _patch_main_window(monkeypatch, {}, tmp_path, saved)
    window = MainWindow()
    qtbot.addWidget(window)

    window._handle_new_tab()
    assert window._tab_container.tab_count() == 2

    window._handle_refresh()
    window._handle_go_up()
    window._handle_next_tab()
    window._handle_previous_tab()

    tab_container = cast(FakeTabContainer, window._tab_container)
    assert "refresh" in tab_container.calls
    assert "go_up" in tab_container.calls
    assert "next" in tab_container.calls
    assert "previous" in tab_container.calls

    window._switch_to_columns()
    window._handle_refresh()
    window._handle_go_up()

    column_browser = cast(FakeColumnBrowser, window._column_browser)
    assert "refresh" in column_browser.calls
    assert "go_up" in column_browser.calls
    assert not window._new_tab_action.isEnabled()

    window._switch_to_tabs()
    assert window._is_tab_mode()
    assert window._toggle_view_action.text() == "Switch to Column View"


def test_main_window_persists_width_and_session(monkeypatch, qtbot, tmp_path: Path) -> None:
    saved: list[dict] = []
    settings: dict = {}
    _patch_main_window(monkeypatch, settings, tmp_path, saved)
    window = MainWindow()
    qtbot.addWidget(window)

    window._handle_name_column_width_changed(640)
    window._persist_settings()

    assert settings["file_browser"]["name_column_width"] == 640
    assert settings["session"]["tabs"] == [str(tmp_path)]
    assert settings["session"]["view_mode"] == "tabs"
    assert saved
