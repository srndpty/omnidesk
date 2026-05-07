from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

import omnidesk.ui.tab_container as tab_container_module
from omnidesk.ui.tab_container import TabContainer


class FakeBrowserTab(QWidget):
    directoryChanged = pyqtSignal(Path)
    requestOpenInNewTab = pyqtSignal(Path)
    nameColumnWidthChanged = pyqtSignal(int)
    DEFAULT_NAME_COLUMN_WIDTH = 420

    def __init__(self, parent=None, *, name_column_width=None):
        super().__init__(parent)
        self._path = Path.cwd()
        self.name_column_width = name_column_width
        self.calls: list[str] = []

    def navigate_to(self, path: Path) -> None:
        self.calls.append(f"navigate:{path}")
        self._path = path

    def current_path(self) -> Path:
        return self._path

    def go_up(self) -> None:
        self.calls.append("go_up")

    def refresh(self) -> None:
        self.calls.append("refresh")

    def focus_view(self) -> None:
        self.calls.append("focus")

    def activate(self) -> None:
        self.calls.append("activate")

    def deactivate(self) -> None:
        self.calls.append("deactivate")

    def set_name_column_width(self, width: int) -> None:
        self.name_column_width = width


def test_tab_bar_is_not_closable_and_elides_from_right(qtbot) -> None:
    container = TabContainer()
    qtbot.addWidget(container)

    tab_bar = container._tabs.tabBar()

    assert not container._tabs.tabsClosable()
    assert tab_bar.elideMode() == Qt.TextElideMode.ElideRight


def test_close_current_tab_uses_shared_close_path(qtbot) -> None:
    container = TabContainer()
    qtbot.addWidget(container)

    container._tabs.addTab(QWidget(), "one")
    container._tabs.addTab(QWidget(), "two")
    container._tabs.setCurrentIndex(1)

    container.close_current_tab()

    assert container._tabs.count() == 1
    assert container._tabs.tabText(0) == "one"


def test_open_tabs_and_navigation_methods_use_current_tab(monkeypatch, qtbot, tmp_path: Path) -> None:
    monkeypatch.setattr(tab_container_module, "FileBrowserTab", FakeBrowserTab)
    container = TabContainer(name_column_width=333)
    qtbot.addWidget(container)

    first = container.open_in_new_tab(tmp_path / "one")
    second = container.open_in_new_tab(tmp_path / "two")

    assert container.tab_count() == 2
    assert container.current_tab() is second
    assert container.tab_paths() == [tmp_path / "one", tmp_path / "two"]
    assert first.name_column_width == 333

    container.select_next_tab()
    assert container.current_tab() is first
    container.select_previous_tab()
    assert container.current_tab() is second

    container.go_up()
    container.refresh()
    container.focus_current()
    container.navigate_current_to(tmp_path / "three")

    assert "go_up" in second.calls
    assert "refresh" in second.calls
    assert "focus" in second.calls
    assert second.current_path() == tmp_path / "three"


def test_directory_and_width_handlers_update_state(monkeypatch, qtbot, tmp_path: Path) -> None:
    monkeypatch.setattr(tab_container_module, "FileBrowserTab", FakeBrowserTab)
    container = TabContainer()
    qtbot.addWidget(container)
    tab = container.open_in_new_tab(tmp_path / "old")

    with qtbot.waitSignal(container.currentPathChanged, timeout=1000) as path_signal:
        tab.navigate_to(tmp_path / "new-name")
        tab.directoryChanged.emit(tmp_path / "emitted")

    assert container._tabs.tabText(0) == "new-name"
    assert path_signal.args == [tmp_path / "emitted"]

    with qtbot.waitSignal(container.nameColumnWidthChanged, timeout=1000) as width_signal:
        tab.nameColumnWidthChanged.emit(512)

    assert width_signal.args == [512]
    assert container.name_column_width() == 512


def test_tab_container_label_for_drive_and_regular_path() -> None:
    assert TabContainer._label_for(Path("C:/")) == "C:"
    assert TabContainer._label_for(Path("C:/Users/example")) == "example"


def test_scroll_tabstrip_fallback_changes_current_index(qtbot) -> None:
    container = TabContainer()
    qtbot.addWidget(container)
    container._tabs.addTab(QWidget(), "one")
    container._tabs.addTab(QWidget(), "two")
    container._tabs.addTab(QWidget(), "three")
    container._tabs.setCurrentIndex(1)

    container._scroll_tabstrip(go_left=True, count=5)
    assert container._tabs.currentIndex() == 0

    container._scroll_tabstrip(go_left=False, count=5)
    assert container._tabs.currentIndex() == 2
