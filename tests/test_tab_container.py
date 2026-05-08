from __future__ import annotations

from pathlib import Path
from typing import cast

from PyQt6.QtCore import QEvent, QPoint, Qt, QUrl, pyqtSignal
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

    def _handle_external_drop(self, paths: list[Path], dest_dir: Path, move: bool) -> None:
        self.calls.append(f"drop:{paths}:{dest_dir}:{move}")


class StubMimeData:
    def __init__(self, urls=None):
        self._urls = urls or []

    def hasUrls(self):
        return bool(self._urls)

    def urls(self):
        return self._urls


class StubPosition:
    def toPoint(self):
        return QPoint(5, 5)


class StubEvent:
    def __init__(
        self, event_type, *, urls=None, modifiers=Qt.KeyboardModifier.NoModifier, button=None
    ):
        self._type = event_type
        self._mime = StubMimeData(urls)
        self._modifiers = modifiers
        self._button = button
        self.accepted = False
        self.drop_action = None

    def type(self):
        return self._type

    def mimeData(self):
        return self._mime

    def modifiers(self):
        return self._modifiers

    def setDropAction(self, action):
        self.drop_action = action

    def accept(self):
        self.accepted = True

    def acceptProposedAction(self):
        self.accepted = True

    def position(self):
        return StubPosition()

    def button(self):
        return self._button


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


def test_open_tabs_and_navigation_methods_use_current_tab(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    monkeypatch.setattr(tab_container_module, "FileBrowserTab", FakeBrowserTab)
    container = TabContainer(name_column_width=333)
    qtbot.addWidget(container)

    first = cast(FakeBrowserTab, container.open_in_new_tab(tmp_path / "one"))
    second = cast(FakeBrowserTab, container.open_in_new_tab(tmp_path / "two"))

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


def test_tab_pinning_uses_tab_data_without_changing_label(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tab_container_module, "FileBrowserTab", FakeBrowserTab)
    container = TabContainer()
    qtbot.addWidget(container)
    container.open_in_new_tab(tmp_path / "one")
    container.open_in_new_tab(tmp_path / "two", pinned=True)

    assert container._tabs.tabText(0) == "one"
    assert container._tabs.tabText(1) == "two"
    assert not container.is_tab_pinned(0)
    assert container.is_tab_pinned(1)
    assert container.tab_pinned_states() == [False, True]

    container._toggle_tab_pinned(0)

    assert container.is_tab_pinned(0)
    assert container._tabs.tabText(0) == "one"
    assert container.tab_pinned_states() == [True, True]

    container._toggle_tab_pinned(0)

    assert not container.is_tab_pinned(0)
    assert container._tabs.tabText(0) == "one"


def test_tab_context_menu_exposes_pin_and_close_actions(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tab_container_module, "FileBrowserTab", FakeBrowserTab)
    container = TabContainer()
    qtbot.addWidget(container)
    container.open_in_new_tab(tmp_path / "one")
    container.open_in_new_tab(tmp_path / "two")

    menu = container._create_tab_context_menu(0)
    actions = menu.actions()

    assert [action.text() for action in actions] == ["Pin Tab", "Close Tab"]
    assert actions[1].isEnabled()

    actions[0].trigger()
    pinned_menu = container._create_tab_context_menu(0)

    assert container.is_tab_pinned(0)
    assert pinned_menu.actions()[0].text() == "Unpin Tab"
    assert not pinned_menu.actions()[1].isEnabled()


def test_tab_context_menu_disables_close_for_last_tab(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tab_container_module, "FileBrowserTab", FakeBrowserTab)
    container = TabContainer()
    qtbot.addWidget(container)
    container.open_in_new_tab(tmp_path / "one")

    menu = container._create_tab_context_menu(0)

    assert menu.actions()[1].text() == "Close Tab"
    assert not menu.actions()[1].isEnabled()


def test_pinned_tabs_cannot_be_closed_until_unpinned(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tab_container_module, "FileBrowserTab", FakeBrowserTab)
    container = TabContainer()
    qtbot.addWidget(container)
    container.open_in_new_tab(tmp_path / "one")
    container.open_in_new_tab(tmp_path / "two")
    container._tabs.setCurrentIndex(0)
    container._toggle_tab_pinned(0)

    container.close_current_tab()
    assert container.tab_count() == 2
    assert container._tabs.tabText(0) == "one"

    container._close_tab(0)
    assert container.tab_count() == 2
    assert container._tabs.tabText(0) == "one"

    container._toggle_tab_pinned(0)
    container.close_current_tab()

    assert container.tab_count() == 1
    assert container._tabs.tabText(0) == "two"


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


def test_event_filter_drag_move_and_drop(monkeypatch, qtbot, tmp_path: Path) -> None:
    monkeypatch.setattr(tab_container_module, "FileBrowserTab", FakeBrowserTab)
    container = TabContainer()
    qtbot.addWidget(container)
    tab = cast(FakeBrowserTab, container.open_in_new_tab(tmp_path))
    tab_bar = container._tabs.tabBar()
    monkeypatch.setattr(tab_bar, "tabAt", lambda _point: 0)
    local_file = tmp_path / "drag.txt"
    local_file.write_text("drag", encoding="utf-8")
    urls = [QUrl.fromLocalFile(str(local_file))]

    drag_move = StubEvent(
        QEvent.Type.DragMove,
        urls=urls,
        modifiers=Qt.KeyboardModifier.ControlModifier,
    )
    assert container.eventFilter(tab_bar, cast(QEvent, drag_move))
    assert drag_move.drop_action == Qt.DropAction.CopyAction
    assert drag_move.accepted

    drop = StubEvent(QEvent.Type.Drop, urls=urls)
    assert container.eventFilter(tab_bar, cast(QEvent, drop))
    assert drop.drop_action == Qt.DropAction.MoveAction
    assert any(call.startswith("drop:") for call in tab.calls)


def test_event_filter_drag_enter_and_middle_click(monkeypatch, qtbot, tmp_path: Path) -> None:
    monkeypatch.setattr(tab_container_module, "FileBrowserTab", FakeBrowserTab)
    container = TabContainer()
    qtbot.addWidget(container)
    container.open_in_new_tab(tmp_path / "one")
    container.open_in_new_tab(tmp_path / "two")
    tab_bar = container._tabs.tabBar()
    monkeypatch.setattr(tab_bar, "tabAt", lambda _point: 1)

    drag_enter = StubEvent(QEvent.Type.DragEnter, urls=[QUrl.fromLocalFile(str(tmp_path))])
    assert container.eventFilter(tab_bar, cast(QEvent, drag_enter))
    assert drag_enter.accepted

    middle = StubEvent(QEvent.Type.MouseButtonRelease, button=Qt.MouseButton.MiddleButton)
    assert container.eventFilter(tab_bar, cast(QEvent, middle))
    assert container.tab_count() == 1
