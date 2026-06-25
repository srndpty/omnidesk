from __future__ import annotations

from pathlib import Path
from typing import cast

from PyQt6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QPoint,
    QPointF,
    QRect,
    QSize,
    Qt,
    QThreadPool,
    QUrl,
)
from PyQt6.QtGui import (
    QDragEnterEvent,
    QIcon,
    QKeyEvent,
    QPainter,
    QPixmap,
    QResizeEvent,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QMessageBox,
    QStyle,
    QStyleOptionViewItem,
)

import omnidesk.ui.file_browser.delegates as file_browser_delegates_module
import omnidesk.ui.file_browser.navigation_controller as file_browser_navigation_controller_module
import omnidesk.ui.file_browser.operations_controller as file_browser_operations_controller_module
import omnidesk.ui.file_browser.status_controller as file_browser_status_controller_module
from omnidesk.ui.file_browser_status import BrowserStatus
from omnidesk.ui.file_browser_tab import (
    FileBrowserTab,
    navigation_cursor_action,
    navigation_event_without_control,
)
from omnidesk.ui.file_operation_jobs import FileOperationJob
from omnidesk.ui.file_operations import FileOperationRequest, FileOperationResult


class _MouseMoveStub:
    def __init__(self, position: QPointF) -> None:
        self._position = position
        self.accepted = False

    def buttons(self) -> Qt.MouseButton:
        return Qt.MouseButton.LeftButton

    def position(self) -> QPointF:
        return self._position

    def accept(self) -> None:
        self.accepted = True


class _MouseReleaseStub:
    def __init__(self, button: Qt.MouseButton) -> None:
        self._button = button
        self.accepted = False

    def button(self) -> Qt.MouseButton:
        return self._button

    def accept(self) -> None:
        self.accepted = True


class _DropStub:
    def __init__(
        self,
        mime_data: QMimeData,
        *,
        drop_action: Qt.DropAction = Qt.DropAction.MoveAction,
        modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    ) -> None:
        self._mime_data = mime_data
        self._drop_action = drop_action
        self._modifiers = modifiers
        self.accepted = False
        self.ignored = False

    def mimeData(self) -> QMimeData:  # noqa: N802
        return self._mime_data

    def position(self) -> QPointF:
        return QPointF(1, 1)

    def dropAction(self) -> Qt.DropAction:  # noqa: N802
        return self._drop_action

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers

    def setDropAction(self, action: Qt.DropAction) -> None:  # noqa: N802
        self._drop_action = action

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


def test_file_browser_tab_initializes_and_navigates(qtbot, tmp_path: Path) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    with qtbot.waitSignal(tab.directoryChanged, timeout=1000) as blocker:
        tab.navigate_to(tmp_path)

    assert tab.current_path() == tmp_path
    assert tab._path_edit.text() == str(tmp_path)
    assert blocker.args == [tmp_path]
    assert tab.name_column_width() == FileBrowserTab.DEFAULT_NAME_COLUMN_WIDTH
    assert tab._navigation_history == []
    assert not tab._back_button.isEnabled()


def test_file_browser_tab_navigate_to_file_uses_parent(qtbot, tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("file", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    tab.navigate_to(file_path)

    assert tab.current_path() == tmp_path


def test_file_browser_tab_warns_for_missing_navigation(monkeypatch, qtbot, tmp_path: Path) -> None:
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.navigation_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    missing = tmp_path / "missing"
    tab.navigate_to(missing)

    assert warnings == [("Cannot navigate", f"{missing} does not exist.")]
    assert not tab._has_loaded_root


def test_file_browser_tab_go_up_selects_previous_folder(monkeypatch, qtbot, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    selected: list[tuple[Path, QAbstractItemView.ScrollHint]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(child)
    monkeypatch.setattr(
        tab,
        "_select_path",
        lambda path, scroll_hint=QAbstractItemView.ScrollHint.EnsureVisible: selected.append(
            (path, scroll_hint)
        )
        or True,
    )

    tab.go_up()
    qtbot.wait(20)

    assert tab.current_path() == parent
    assert selected == [(child, QAbstractItemView.ScrollHint.PositionAtCenter)]


def test_file_browser_tab_go_up_invalidates_folder_preview(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(child)
    monkeypatch.setattr(
        tab._model,
        "invalidate_folder_thumbnail_preview",
        invalidated.append,
    )
    monkeypatch.setattr(
        file_browser_navigation_controller_module,
        "directory_fingerprint_changed",
        lambda path, previous: path == child and previous is not None,
    )

    tab.go_up()

    assert invalidated == [child]


def test_file_browser_tab_go_up_keeps_folder_preview_when_directory_unchanged(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(child)
    monkeypatch.setattr(
        tab._model,
        "invalidate_folder_thumbnail_preview",
        invalidated.append,
    )
    monkeypatch.setattr(
        file_browser_navigation_controller_module,
        "directory_fingerprint_changed",
        lambda path, previous: False,
    )

    tab.go_up()

    assert invalidated == []


def test_file_browser_tab_navigation_buttons_use_modern_arrow_text(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    for button, name, text in (
        (tab._back_button, "Back", "←"),
        (tab._forward_button, "Forward", "→"),
        (tab._up_button, "Up", "↑"),
    ):
        assert button.text() == text
        assert button.accessibleName() == name
        assert button.icon().isNull()
        assert button.toolTip()


def test_file_browser_tab_backspace_shortcut_goes_back(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    shortcuts = {
        shortcut.key().toString() for shortcut in cast(list[QShortcut], tab.findChildren(QShortcut))
    }

    assert "Backspace" in shortcuts


def test_file_browser_tab_go_back_and_forward_navigate_history(qtbot, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    first.mkdir()
    second.mkdir()
    third.mkdir()
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    tab.navigate_to(first)
    tab.navigate_to(second)
    tab.navigate_to(third)

    assert tab._navigation_history[-2:] == [first, second]
    assert not tab._forward_history
    assert tab._back_button.isEnabled()
    assert not tab._forward_button.isEnabled()

    tab.go_back()

    assert tab.current_path() == second
    assert tab._navigation_history[-1] == first
    assert tab._forward_history == [third]
    assert tab._back_button.isEnabled()
    assert tab._forward_button.isEnabled()

    tab.go_forward()

    assert tab.current_path() == third
    assert tab._navigation_history[-2:] == [first, second]
    assert not tab._forward_history


def test_file_browser_tab_go_back_selects_folder_left_behind(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    selected: list[tuple[Path, QAbstractItemView.ScrollHint]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(parent)
    tab.navigate_to(child)
    monkeypatch.setattr(
        tab,
        "_select_path",
        lambda path, scroll_hint=QAbstractItemView.ScrollHint.EnsureVisible: selected.append(
            (path, scroll_hint)
        )
        or True,
    )

    tab.go_back()

    assert tab.current_path() == parent
    qtbot.waitUntil(lambda: bool(selected), timeout=1000)
    assert selected == [(child, QAbstractItemView.ScrollHint.PositionAtCenter)]


def test_file_browser_tab_go_back_to_parent_invalidates_folder_preview(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(parent)
    tab.navigate_to(child)
    monkeypatch.setattr(
        tab._model,
        "invalidate_folder_thumbnail_preview",
        invalidated.append,
    )
    monkeypatch.setattr(
        file_browser_navigation_controller_module,
        "directory_fingerprint_changed",
        lambda path, previous: path == child and previous is not None,
    )

    tab.go_back()

    assert invalidated == [child]


def test_file_browser_tab_go_back_to_non_parent_keeps_unchanged_preview(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for path in (first, second):
        path.mkdir()
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(first)
    tab.navigate_to(second)
    monkeypatch.setattr(
        tab._model,
        "invalidate_folder_thumbnail_preview",
        invalidated.append,
    )

    tab.go_back()

    assert invalidated == []


def test_file_browser_tab_leaving_changed_directory_invalidates_preview(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    folder = tmp_path / "folder"
    child = folder / "child"
    folder.mkdir()
    child.mkdir()
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(folder)
    tab._mark_current_directory_changed()
    monkeypatch.setattr(tab._model, "invalidate_folder_thumbnail_preview", invalidated.append)
    monkeypatch.setattr(
        file_browser_navigation_controller_module,
        "directory_fingerprint_changed",
        lambda path, previous: False,
    )

    tab.navigate_to(child)

    assert invalidated == [folder]
    assert not tab._current_directory_has_local_changes


def test_file_browser_tab_leaving_changed_directory_for_sibling_invalidates_preview(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(first)
    tab._mark_current_directory_changed()
    monkeypatch.setattr(tab._model, "invalidate_folder_thumbnail_preview", invalidated.append)
    monkeypatch.setattr(
        file_browser_navigation_controller_module,
        "directory_fingerprint_changed",
        lambda path, previous: False,
    )

    tab.navigate_to(second)

    assert invalidated == [first]


def test_file_browser_tab_navigate_to_same_path_keeps_local_change_flag(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    tab._mark_current_directory_changed()
    monkeypatch.setattr(tab._model, "invalidate_folder_thumbnail_preview", invalidated.append)

    tab.navigate_to(tmp_path)

    assert tab._current_directory_has_local_changes
    assert invalidated == []


def test_file_browser_tab_failed_history_navigation_keeps_stacks(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(first)
    tab.navigate_to(second)
    first.rmdir()
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.navigation_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab.go_back()

    assert warnings == [("Cannot navigate", f"{first} does not exist.")]
    assert tab.current_path() == second
    assert tab._navigation_history == [first]
    assert tab._forward_history == []
    assert tab._back_button.isEnabled()
    assert not tab._forward_button.isEnabled()


def test_file_browser_tab_go_up_does_not_record_navigation_history(qtbot, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    sibling = tmp_path / "sibling"
    child.mkdir(parents=True)
    sibling.mkdir()
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(sibling)
    tab.navigate_to(child)
    history_before = list(tab._navigation_history)

    tab.go_up()

    assert tab.current_path() == parent
    assert tab._navigation_history == history_before


def test_file_browser_tab_refresh_preserves_selection_and_resorts(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    selected_path = tmp_path / "selected"
    selected_path.mkdir()
    navigated: list[Path] = []
    sorted_columns: list[tuple[int, Qt.SortOrder]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    monkeypatch.setattr(tab, "_selected_index_path", lambda: selected_path)
    monkeypatch.setattr(tab, "navigate_to", lambda path: navigated.append(path) or True)
    monkeypatch.setattr(tab, "_reset_root_before_refresh", lambda target: False)
    monkeypatch.setattr(tab, "_schedule_refresh_sort", lambda: None)
    monkeypatch.setattr(
        tab._model,
        "sort",
        lambda column, order: sorted_columns.append((column, order)),
    )

    tab.refresh()

    qtbot.waitUntil(lambda: bool(navigated), timeout=1000)
    assert navigated == [tmp_path]
    assert tab._refresh_selection_path == selected_path
    assert tab._pending_selection_scroll_hint == QAbstractItemView.ScrollHint.EnsureVisible
    assert sorted_columns == [(0, Qt.SortOrder.AscendingOrder)]


def test_file_browser_tab_refresh_retries_failed_thumbnails(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    monkeypatch.setattr(tab, "navigate_to", lambda path: True)
    monkeypatch.setattr(tab, "_reset_root_before_refresh", lambda target: False)
    monkeypatch.setattr(tab, "_schedule_refresh_sort", lambda: None)
    monkeypatch.setattr(tab._model, "sort", lambda column, order: None)
    tab._source_model._failed.add(str(tmp_path / "locked.png"))

    tab.refresh()

    assert tab._source_model._failed == set()


def test_file_browser_tab_refresh_keeps_explicit_pending_selection(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    old_selection = tmp_path / "old.txt"
    pending_selection = tmp_path / "created.txt"
    old_selection.write_text("old", encoding="utf-8")
    pending_selection.write_text("created", encoding="utf-8")
    navigated: list[Path] = []
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._current_path = tmp_path
    tab._has_loaded_root = True
    tab._pending_selection_path = pending_selection
    monkeypatch.setattr(tab, "_selected_index_path", lambda: old_selection)
    monkeypatch.setattr(tab, "navigate_to", lambda path: navigated.append(path) or True)
    monkeypatch.setattr(tab, "_reset_root_before_refresh", lambda target: False)
    monkeypatch.setattr(tab, "_schedule_refresh_sort", lambda: None)
    monkeypatch.setattr(tab, "_select_path", lambda path: selected.append(path) or False)

    tab.refresh()

    qtbot.waitUntil(lambda: bool(navigated), timeout=1000)
    assert tab._pending_selection_path == pending_selection
    assert tab._refresh_selection_path == pending_selection
    assert selected == [pending_selection]


def test_file_browser_tab_refresh_and_select_defers_selection_until_model_ready(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    target = tmp_path / "created.txt"
    target.write_text("created", encoding="utf-8")
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._current_path = tmp_path
    tab._has_loaded_root = True
    monkeypatch.setattr(tab, "_reset_root_before_refresh", lambda path: True)
    monkeypatch.setattr(
        tab,
        "_select_path",
        lambda path,
        scroll_hint=QAbstractItemView.ScrollHint.EnsureVisible,
        **kwargs: selected.append(path) or False,
    )

    tab._refresh_and_select(target)

    assert selected == [target]
    assert tab._pending_selection_path == target
    assert tab._refresh_selection_path == target
    assert tab._deferred_refresh_target == tmp_path


def test_file_browser_tab_pending_selection_survives_deferred_refresh(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    target = tmp_path / "created.txt"
    target.write_text("created", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._pending_selection_path = target
    tab._deferred_refresh_target = tmp_path
    monkeypatch.setattr(tab, "_select_path", lambda path: True)

    assert tab._select_pending_path_if_ready()
    assert tab._pending_selection_path == target


def test_file_browser_tab_deferred_refresh_clears_pending_selection_after_ready(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    target = tmp_path / "created.txt"
    target.write_text("created", encoding="utf-8")
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._current_path = tmp_path
    tab._has_loaded_root = True
    tab._pending_selection_path = target
    tab._deferred_refresh_target = tmp_path
    monkeypatch.setattr(tab, "navigate_to", lambda path: True)
    monkeypatch.setattr(tab, "_schedule_refresh_sort", lambda: None)
    monkeypatch.setattr(
        tab,
        "_select_path",
        lambda path, *args, **kwargs: selected.append(path) or True,
    )

    tab._complete_deferred_refresh()

    assert selected == [target]
    assert tab._pending_selection_path is None
    assert tab._pending_selection_scroll_hint == QAbstractItemView.ScrollHint.EnsureVisible


def test_file_browser_tab_refresh_keeps_view_roots_at_current_directory(
    qtbot,
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    (current / "a.txt").write_text("a", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(current)

    tab.refresh()

    qtbot.waitUntil(
        lambda: Path(tab._model.filePath(tab._tree_view.rootIndex())) == current
        and Path(tab._model.filePath(tab._tile_view.rootIndex())) == current,
        timeout=1000,
    )
    assert tab.current_path() == current


def test_file_browser_tab_shutdown_cancels_deferred_refresh(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._current_path = tmp_path
    monkeypatch.setattr(tab, "_reset_root_before_refresh", lambda target: True)

    tab.refresh()

    assert tab._deferred_refresh_timer.isActive()
    assert tab._deferred_refresh_target == tmp_path

    tab.cancel_all_work_for_shutdown()

    assert not tab._deferred_refresh_timer.isActive()
    assert tab._deferred_refresh_target is None


def test_file_browser_tab_refresh_sort_does_not_override_new_user_selection(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    old_selection = tmp_path / "old.txt"
    new_selection = tmp_path / "new.txt"
    old_selection.write_text("old", encoding="utf-8")
    new_selection.write_text("new", encoding="utf-8")
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    tab._refresh_sort_active = True
    tab._refresh_sort_retries = 3
    tab._refresh_selection_path = old_selection
    monkeypatch.setattr(tab, "_selected_index_path", lambda: new_selection)
    monkeypatch.setattr(tab, "_select_path", lambda path: selected.append(path) or True)

    tab._apply_refresh_sort()

    assert selected == []
    assert tab._refresh_selection_path is None
    assert not tab._refresh_sort_active
    assert tab._refresh_sort_retries == 0


def test_file_browser_tab_settled_scroll_does_not_override_new_user_selection(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    old_selection = tmp_path / "old"
    new_selection = tmp_path / "new"
    old_selection.mkdir()
    new_selection.mkdir()
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._settled_scroll_path = old_selection
    tab._settled_scroll_retries = 3
    monkeypatch.setattr(tab, "_selected_index_path", lambda: new_selection)
    monkeypatch.setattr(
        tab,
        "_select_path",
        lambda path,
        scroll_hint=QAbstractItemView.ScrollHint.EnsureVisible,
        **kwargs: selected.append(path) or True,
    )

    tab._apply_settled_scroll()

    assert selected == []
    assert tab._settled_scroll_path is None
    assert tab._settled_scroll_retries == 0


def test_file_browser_tab_new_navigation_clears_forward_history(qtbot, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"
    fourth = tmp_path / "fourth"
    for path in (first, second, third, fourth):
        path.mkdir()
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(first)
    tab.navigate_to(second)
    tab.navigate_to(third)
    tab.go_back()

    tab.navigate_to(fourth)

    assert tab.current_path() == fourth
    assert not tab._forward_history
    assert not tab._forward_button.isEnabled()


def test_file_browser_tab_name_column_width_and_view_toggle(qtbot) -> None:
    tab = FileBrowserTab(name_column_width=222)
    qtbot.addWidget(tab)

    assert tab.name_column_width() == 222

    tab.set_name_column_width(333)
    assert tab.name_column_width() == 333

    before = tab._media_icon_mode
    tab._handle_view_toggle_clicked()

    assert tab._media_icon_mode is (not before)
    assert tab._toggle_view_button.text() in {"List View", "Tile View"}


def test_manual_list_view_persists_through_media_mode_update(qtbot, tmp_path: Path) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)

    # 既定はタイル表示。手動でリスト表示へ切り替える。
    assert tab._media_icon_mode is True
    tab._handle_view_toggle_clicked()
    assert tab._media_icon_mode is False

    # 削除後の refresh などで呼ばれる経路でも、手動選択を維持する。
    tab._update_media_mode(tmp_path, select_default=False)
    assert tab._media_icon_mode is False


def test_file_browser_tab_tile_view_uses_single_pass_fixed_grid(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    tile_view = tab._tile_view

    assert tile_view.movement() == QListView.Movement.Static
    assert tile_view.layoutMode() == QListView.LayoutMode.SinglePass
    assert tile_view.uniformItemSizes()
    assert tile_view.wordWrap()
    assert tile_view.textElideMode() == Qt.TextElideMode.ElideNone


def test_file_browser_tab_tile_view_wraps_long_names_by_character(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    name = "(C56) [ART THEATER] M.F.H.H. M&D (宇宙海賊ミトの大冒険、おジャ魔女どれみ)"

    wrapped = delegate._two_line_text(name, tab._tile_view.fontMetrics(), 120)
    lines = wrapped.splitlines()

    assert len(lines) == 2
    assert lines[0] != name
    assert all(line for line in lines)


def test_file_browser_tab_tile_delegate_places_two_line_label_below_icon(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 184, 222)
    option.decorationSize = QSize(160, 160)
    option.fontMetrics = tab._tile_view.fontMetrics()

    icon_rect, text_rect = delegate._tile_rects(option)

    assert icon_rect == QRect(12, 4, 160, 160)
    assert text_rect.y() > icon_rect.bottom()
    assert text_rect.height() == option.fontMetrics.lineSpacing() * 2
    assert text_rect.bottom() <= option.rect.bottom()


def test_file_browser_tab_tile_delegate_keeps_generated_thumbnail_height(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    thumbnail = QPixmap(160, 160)
    thumbnail.fill()
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 166, 110)
    option.decorationSize = QSize(160, 160)
    option.icon = QIcon(thumbnail)
    option.fontMetrics = tab._tile_view.fontMetrics()

    icon_size = delegate._stable_thumbnail_icon_size(option, QIcon.Mode.Normal)
    icon_rect, text_rect = delegate._tile_rects(option, icon_size=icon_size)

    assert icon_size == QSize(160, 160)
    assert icon_rect == QRect(3, 4, 160, 160)
    assert text_rect.y() > icon_rect.bottom()


def test_file_browser_tab_tile_delegate_keeps_generated_thumbnail_height_when_selected(
    qtbot,
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    thumbnail = QPixmap(160, 160)
    thumbnail.fill()
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 166, 110)
    option.decorationSize = QSize(160, 160)
    option.icon = QIcon(thumbnail)
    option.fontMetrics = tab._tile_view.fontMetrics()

    icon_size = delegate._stable_thumbnail_icon_size(option, QIcon.Mode.Selected)

    assert icon_size == QSize(160, 160)


def test_file_browser_tab_tile_delegate_keeps_default_folder_icon_clipped(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 166, 110)
    option.decorationSize = QSize(160, 160)
    option.icon = tab.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
    option.fontMetrics = tab._tile_view.fontMetrics()

    icon_size = delegate._stable_thumbnail_icon_size(option, QIcon.Mode.Normal)
    icon_rect, _ = delegate._tile_rects(option, icon_size=icon_size)

    assert icon_size is None
    assert icon_rect.height() == 110


def test_file_browser_tab_tile_delegate_does_not_fill_selected_tile_background(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 20, 20)
    option.palette = tab._tile_view.palette()
    option.state = QStyle.StateFlag.State_Selected
    pixmap = QPixmap(option.rect.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)

    delegate._draw_tile_background(painter, option, tab._tile_view.style())
    painter.end()

    assert pixmap.toImage().pixelColor(0, 0) != option.palette.highlight().color()
    assert pixmap.toImage().pixelColor(19, 19) != option.palette.highlight().color()


def test_file_browser_tab_tile_delegate_clears_stale_label_highlight(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    model = QStandardItemModel()
    model.appendRow(QStandardItem("AdwCleaner[C00].txt"))
    index = model.index(0, 0)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 184, 222)
    option.decorationSize = QSize(160, 160)
    option.fontMetrics = tab._tile_view.fontMetrics()
    option.palette = tab._tile_view.palette()
    option.widget = tab._tile_view
    _, text_rect = delegate._tile_rects(option)
    pixmap = QPixmap(option.rect.size())
    pixmap.fill(option.palette.base().color())

    selected_option = QStyleOptionViewItem(option)
    selected_option.state = QStyle.StateFlag.State_Selected
    painter = QPainter(pixmap)
    delegate.paint(painter, selected_option, index)
    painter.end()

    unselected_option = QStyleOptionViewItem(option)
    unselected_option.state = QStyle.StateFlag.State_None
    painter = QPainter(pixmap)
    delegate.paint(painter, unselected_option, index)
    painter.end()

    image = pixmap.toImage()
    highlight = option.palette.highlight().color()
    for x in range(text_rect.left(), text_rect.right() + 1):
        assert image.pixelColor(x, text_rect.top()) != highlight
        assert image.pixelColor(x, text_rect.bottom()) != highlight


def test_file_browser_tab_tile_delegate_clips_icon_to_icon_rect(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    pixmap = QPixmap(80, 80)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    icon_rect = QRect(20, 20, 20, 20)

    class OversizedIcon:
        def paint(self, painter, rect, alignment, mode, state) -> None:
            painter.fillRect(rect.adjusted(-10, -10, 10, 10), Qt.GlobalColor.red)

    delegate._draw_icon(
        painter,
        cast(QIcon, OversizedIcon()),
        icon_rect,
        QIcon.Mode.Normal,
    )
    painter.end()

    image = pixmap.toImage()
    assert image.pixelColor(icon_rect.center()) == Qt.GlobalColor.red
    assert image.pixelColor(icon_rect.left() - 1, icon_rect.center().y()).alpha() == 0
    assert image.pixelColor(icon_rect.right() + 1, icon_rect.center().y()).alpha() == 0
    assert image.pixelColor(icon_rect.center().x(), icon_rect.top() - 1).alpha() == 0
    assert image.pixelColor(icon_rect.center().x(), icon_rect.bottom() + 1).alpha() == 0


def test_file_browser_tab_tile_delegate_marks_copy_clipboard_target(monkeypatch, qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    model = QStandardItemModel()
    model.appendRow(QStandardItem("copy-target.txt"))
    index = model.index(0, 0)
    tab._tile_view.setModel(model)
    monkeypatch.setattr(tab, "_clipboard_visual_mode_for_index", lambda _index: "copy")
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 184, 222)
    option.decorationSize = QSize(160, 160)
    option.fontMetrics = tab._tile_view.fontMetrics()
    option.palette = tab._tile_view.palette()
    option.widget = tab._tile_view
    pixmap = QPixmap(option.rect.size())
    pixmap.fill(option.palette.base().color())
    painter = QPainter(pixmap)

    delegate.paint(painter, option, index)
    painter.end()

    image = pixmap.toImage()
    assert image.pixelColor(1, 1) != option.palette.base().color()
    assert image.pixelColor(1, 1) != option.palette.highlight().color()


def test_file_browser_tab_tree_delegate_marks_copy_clipboard_target(monkeypatch, qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._DropTargetItemDelegate, tab._tree_view.itemDelegate()
    )
    model = QStandardItemModel()
    model.appendRow(QStandardItem("copy-target.txt"))
    index = model.index(0, 0)
    tab._tree_view.setModel(model)
    monkeypatch.setattr(tab, "_clipboard_visual_mode_for_index", lambda _index: "copy")
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 120, 20)
    option.palette = tab._tree_view.palette()
    option.widget = tab._tree_view
    pixmap = QPixmap(option.rect.size())
    pixmap.fill(option.palette.base().color())
    painter = QPainter(pixmap)

    delegate.paint(painter, option, index)
    painter.end()

    assert pixmap.toImage().pixelColor(0, 10) != option.palette.base().color()


def test_file_browser_tab_tree_delegate_marks_copy_only_on_first_column(monkeypatch, qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._DropTargetItemDelegate, tab._tree_view.itemDelegate()
    )
    model = QStandardItemModel()
    model.appendRow([QStandardItem("copy-target.txt"), QStandardItem("detail")])
    index = model.index(0, 1)
    tab._tree_view.setModel(model)
    monkeypatch.setattr(tab, "_clipboard_visual_mode_for_index", lambda _index: "copy")
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 120, 20)
    option.palette = tab._tree_view.palette()
    option.widget = tab._tree_view
    pixmap = QPixmap(option.rect.size())
    pixmap.fill(option.palette.base().color())
    painter = QPainter(pixmap)

    delegate.paint(painter, option, index)
    painter.end()

    assert pixmap.toImage().pixelColor(0, 10) == option.palette.base().color()


def test_file_browser_tab_address_bar_opens_existing_file(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("file", encoding="utf-8")
    opened: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    monkeypatch.setattr(tab, "_open_file", lambda path: opened.append(path))

    tab._path_edit.setText("file.txt")
    tab._handle_path_entered()

    assert opened == [file_path]


def test_file_browser_tab_address_bar_runs_unknown_command(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    commands: list[str] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    monkeypatch.setattr(tab, "_execute_address_command", lambda command: commands.append(command))

    tab._path_edit.setText("not-a-path --flag")
    tab._handle_path_entered()

    assert commands == ["not-a-path --flag"]


def test_file_browser_tab_delete_cancel_does_not_refresh(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("file", encoding="utf-8")
    refreshed: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_selected_paths", lambda: [file_path])
    monkeypatch.setattr(tab, "_selection_path_before_deleted_items", lambda paths: None)
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )

    tab._delete_selected()

    assert refreshed == []
    assert file_path.exists()


def test_file_browser_tab_execute_command_handles_parse_error(monkeypatch, qtbot) -> None:
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.command_runner.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._execute_address_command('"unterminated')

    assert warnings
    assert warnings[0][0] == "Command"


def test_file_browser_tab_execute_command_warns_when_missing(monkeypatch, qtbot) -> None:
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_resolve_program_for_windows", lambda program: (None, False))
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.command_runner.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._execute_address_command("missing-tool --flag")

    assert warnings == [
        ("Command not found", "'missing-tool' is not found in current folder or PATH.")
    ]


def test_file_browser_tab_execute_command_starts_direct_and_batch(monkeypatch, qtbot) -> None:
    starts: list[tuple[str, list[str], str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.command_runner.QProcess.startDetached",
        lambda program, args, cwd: starts.append((program, list(args), cwd)) or True,
    )

    monkeypatch.setattr(
        tab, "_resolve_program_for_windows", lambda program: ("C:/bin/tool.exe", False)
    )
    tab._execute_address_command("tool --flag")

    monkeypatch.setattr(
        tab, "_resolve_program_for_windows", lambda program: ("C:/bin/script.cmd", True)
    )
    monkeypatch.setenv("COMSPEC", "C:/Windows/System32/cmd.exe")
    tab._execute_address_command("script arg")

    assert starts[0] == ("C:/bin/tool.exe", ["--flag"], str(tab.current_path()))
    assert starts[1] == (
        "C:/Windows/System32/cmd.exe",
        ["/C", "C:/bin/script.cmd", "arg"],
        str(tab.current_path()),
    )


def test_file_browser_tab_execute_command_strips_argument_quotes(monkeypatch, qtbot) -> None:
    starts: list[tuple[str, list[str], str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.command_runner.QProcess.startDetached",
        lambda program, args, cwd: starts.append((program, list(args), cwd)) or True,
    )
    monkeypatch.setattr(
        tab, "_resolve_program_for_windows", lambda program: ("C:/bin/tool.exe", False)
    )

    tab._execute_address_command('tool "a b.txt" plain.txt')

    assert starts == [("C:/bin/tool.exe", ["a b.txt", "plain.txt"], str(tab.current_path()))]


def test_file_browser_tab_execute_command_starts_cmd_special_case(monkeypatch, qtbot) -> None:
    starts: list[tuple[str, list[str], str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setenv("COMSPEC", "C:/Windows/System32/cmd.exe")
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.command_runner.QProcess.startDetached",
        lambda program, args, cwd: starts.append((program, list(args), cwd)) or True,
    )

    tab._execute_address_command("cmd")

    assert starts == [("C:/Windows/System32/cmd.exe", [], str(tab.current_path()))]


def test_file_browser_tab_activate_deactivate_thumbnail_state(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    tab.activate()
    assert tab._is_active
    assert tab._thumbnail_request_timer.isActive()

    tab.deactivate()
    assert not tab._is_active
    assert not tab._thumbnail_request_timer.isActive()
    assert not tab._thumbnail_scroll_settle_timer.isActive()
    assert not tab._thumbnail_idle_batch_timer.isActive()


def test_file_browser_tab_resize_event_restarts_thumbnails_when_active(
    monkeypatch,
    qtbot,
) -> None:
    restarted: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._is_active = True
    monkeypatch.setattr(tab, "_restart_thumbnail_requests", lambda: restarted.append(True))

    tab.resizeEvent(QResizeEvent(QSize(640, 480), QSize(320, 240)))

    assert restarted == [True]


def test_file_browser_tab_deactivate_does_not_cancel_file_operation_jobs(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    job = FileOperationJob(FileOperationRequest([], None, "delete"))
    tab._file_operation_jobs.append(job)

    tab.activate()
    tab.deactivate()

    assert not job.cancelled


def test_file_browser_tab_shutdown_cancels_file_operation_jobs(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    job = FileOperationJob(FileOperationRequest([], None, "delete"))
    tab._file_operation_jobs.append(job)

    tab.cancel_all_work_for_shutdown()

    assert job.cancelled
    assert tab._file_operation_jobs == []


def test_file_browser_tab_cancelled_file_operation_result_does_not_update_ui(
    monkeypatch,
    qtbot,
) -> None:
    warnings: list[tuple[str, str]] = []
    refreshed: list[bool] = []
    changed_dirs: list[list[Path]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))
    monkeypatch.setattr(tab, "_mark_changed_directories", lambda dirs: changed_dirs.append(dirs))
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._handle_file_operation_finished(
        FileOperationResult(["cancelled"], [Path("dest")], cancelled=True)
    )

    assert warnings == []
    assert refreshed == []
    assert changed_dirs == []


def test_file_browser_tab_restore_selection_after_removed_paths_refreshes_current_dir(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    removed = tmp_path / "removed.txt"
    replacement = tmp_path / "next.txt"
    replacement.write_text("next", encoding="utf-8")
    refreshed: list[bool] = []
    selected: list[bool] = []
    focused: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._current_path = tmp_path
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))
    monkeypatch.setattr(
        tab,
        "_select_pending_path_if_ready",
        lambda: selected.append(True) or True,
    )
    monkeypatch.setattr(tab, "focus_view", lambda: focused.append(True))

    tab.restore_selection_after_removed_paths([removed], replacement)

    assert tab._pending_selection_path == replacement
    assert refreshed == [True]
    assert selected == [True]
    assert focused == [True]


def test_file_browser_tab_external_drop_warns_for_missing_destination(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    warnings: list[tuple[str, str]] = []
    copied: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    monkeypatch.setattr(tab, "_perform_copy_or_move", lambda *args, **kwargs: copied.append(True))

    tab._handle_external_drop([tmp_path / "source.txt"], tmp_path / "missing", move=False)

    assert warnings == [("Drop failed", f"Destination {tmp_path / 'missing'} does not exist.")]
    assert copied == []


def test_file_browser_tab_external_drop_blocks_moving_folder_into_itself(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    warnings: list[tuple[str, str]] = []
    copied: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    monkeypatch.setattr(tab, "_perform_copy_or_move", lambda *args, **kwargs: copied.append(True))

    tab._handle_external_drop([source], nested, move=True)

    assert warnings == []
    assert copied == []


def test_file_browser_tab_external_drop_performs_operation_and_refreshes(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    dest = tmp_path / "dest"
    dest.mkdir()
    operations: list[tuple[list[Path], Path, bool]] = []
    refreshed: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        tab,
        "_perform_copy_or_move_with_result",
        lambda paths, target_dir, move: operations.append((paths, target_dir, move))
        or FileOperationResult([], []),
    )
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))

    tab._handle_external_drop([source], dest, move=False)

    assert operations == [([source], dest, False)]
    assert refreshed == [True]


def test_file_browser_tab_external_drop_into_subfolder_invalidates_target_preview(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    dest = tmp_path / "dest"
    dest.mkdir()
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    monkeypatch.setattr(
        tab,
        "_perform_copy_or_move_with_result",
        lambda *args, **kwargs: FileOperationResult([], [dest]),
    )
    monkeypatch.setattr(tab._model, "invalidate_folder_thumbnail_preview", invalidated.append)
    monkeypatch.setattr(tab, "refresh", lambda: None)

    tab._handle_external_drop([source], dest, move=False)

    assert invalidated == [dest]


def test_file_browser_tab_external_move_invalidates_source_and_target_previews(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    source_parent = tmp_path / "source-parent"
    dest = tmp_path / "dest"
    source_parent.mkdir()
    dest.mkdir()
    source = source_parent / "source.txt"
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    monkeypatch.setattr(
        tab,
        "_perform_copy_or_move_with_result",
        lambda *args, **kwargs: FileOperationResult([], [dest, source_parent]),
    )
    monkeypatch.setattr(tab._model, "invalidate_folder_thumbnail_preview", invalidated.append)
    monkeypatch.setattr(tab, "refresh", lambda: None)

    tab._handle_external_drop([source], dest, move=True)

    assert invalidated == [dest, source_parent]


def test_file_browser_tab_apply_rename_success(monkeypatch, qtbot, tmp_path: Path) -> None:
    original = tmp_path / "old.txt"
    original.write_text("old", encoding="utf-8")
    refreshed: list[bool] = []
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))
    monkeypatch.setattr(tab, "_select_path", lambda path: selected.append(path) or True)

    tab._apply_rename(original, "new.txt")

    assert not original.exists()
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "old"
    assert refreshed == [True]
    assert selected == [tmp_path / "new.txt"]


def test_file_browser_tab_apply_rename_ignores_unchanged_name(qtbot, tmp_path: Path) -> None:
    original = tmp_path / "old.txt"
    original.write_text("old", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    # An empty or unchanged name (e.g. the user pressed Escape) is a no-op.
    tab._apply_rename(original, "")
    tab._apply_rename(original, "old.txt")

    assert original.read_text(encoding="utf-8") == "old"


def test_file_browser_tab_apply_rename_warns_for_conflict(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    original = tmp_path / "old.txt"
    target = tmp_path / "target.txt"
    original.write_text("old", encoding="utf-8")
    target.write_text("target", encoding="utf-8")
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._apply_rename(original, "target.txt")

    assert warnings == [("Rename failed", f"{target} already exists.")]
    assert original.exists()


def test_file_browser_tab_apply_rename_warns_when_original_missing(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    # The editor captures the path eagerly, so a rename can be committed after
    # the file disappeared underneath us; the failure must surface as a warning.
    original = tmp_path / "gone.txt"
    warnings: list[tuple[str, str]] = []
    refreshed: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._apply_rename(original, "renamed.txt")

    assert len(warnings) == 1
    assert warnings[0][0] == "Rename failed"
    assert not (tmp_path / "renamed.txt").exists()
    assert refreshed == []


def test_file_browser_tab_apply_rename_clips_after_confirmation(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    from omnidesk.ui.file_operations import clip_child_name

    original = tmp_path / "src.txt"
    original.write_text("data", encoding="utf-8")
    long_name = "あ" * 500 + ".txt"
    expected = clip_child_name(tmp_path, long_name)
    questions: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "refresh", lambda: None)
    monkeypatch.setattr(tab, "_select_path", lambda path: True)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.question",
        lambda _parent, title, text, *args: questions.append((title, text))
        or QMessageBox.StandardButton.Ok,
    )

    tab._apply_rename(original, long_name)

    assert len(questions) == 1
    assert (tmp_path / expected).exists()
    assert not original.exists()


def test_file_browser_tab_apply_rename_rejects_overlong_path_separator_name(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    # An over-long name whose separator is outside the clip range must be
    # rejected as a path-separator name, not silently clipped into a new name.
    original = tmp_path / "src.txt"
    original.write_text("data", encoding="utf-8")
    bad_name = "a" * 500 + "/other.txt"
    warnings: list[tuple[str, str]] = []
    questions: list[object] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.question",
        lambda *args: questions.append(args) or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._apply_rename(original, bad_name)

    # No clip confirmation was shown, and the rename was rejected.
    assert questions == []
    assert warnings == [("Rename failed", "Name must not contain path separators.")]
    assert original.exists()
    assert list(tmp_path.iterdir()) == [original]


def test_file_browser_tab_apply_rename_cancel_returns_to_edit(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    original = tmp_path / "src.txt"
    original.write_text("data", encoding="utf-8")
    long_name = "あ" * 500 + ".txt"
    reopened: list[tuple[Path, str | None]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        tab,
        "_begin_inline_edit",
        lambda path, *, seed_text=None: reopened.append((path, seed_text)) or True,
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.question",
        lambda *args: QMessageBox.StandardButton.Cancel,
    )

    tab._apply_rename(original, long_name)

    # Nothing renamed; editor reopened seeded with the user's original input.
    assert original.exists()
    assert reopened == [(original, long_name)]


def test_file_browser_tab_apply_rename_warns_on_conflict_after_clip(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    from omnidesk.ui.file_operations import clip_child_name

    original = tmp_path / "src.txt"
    original.write_text("data", encoding="utf-8")
    long_name = "あ" * 500 + ".txt"
    existing = tmp_path / clip_child_name(tmp_path, long_name)
    existing.write_text("existing", encoding="utf-8")
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.question",
        lambda *args: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._apply_rename(original, long_name)

    assert len(warnings) == 1
    assert warnings[0][0] == "Rename failed"
    assert original.exists()


def test_file_browser_tab_create_new_file_and_folder_success(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    refreshed: list[bool] = []
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))
    monkeypatch.setattr(tab, "_select_path", lambda path: selected.append(path) or True)
    names = iter([("created.txt", True), ("created-folder", True)])
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QInputDialog.getText",
        lambda *args, **kwargs: next(names),
    )

    tab._create_new_file()
    tab._create_new_folder()

    assert (tmp_path / "created.txt").is_file()
    assert (tmp_path / "created-folder").is_dir()
    assert refreshed == [True, True]
    assert selected == [tmp_path / "created.txt", tmp_path / "created-folder"]


def test_file_browser_tab_paste_copy_and_move_updates_clipboard_and_actions(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    copied = tmp_path / "copy.txt"
    moved = tmp_path / "move.txt"
    copied.write_text("copy", encoding="utf-8")
    moved.write_text("move", encoding="utf-8")
    operations: list[tuple[list[Path], Path, bool]] = []
    refreshed: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    monkeypatch.setattr(
        tab,
        "_perform_copy_or_move_with_result",
        lambda paths, dest_dir, move: operations.append((paths, dest_dir, move))
        or FileOperationResult([], [dest_dir]),
    )
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))

    tab._clipboard = {"paths": [copied], "mode": "copy"}
    tab._paste_into_current()
    tab._clipboard = {"paths": [moved], "mode": "move"}
    tab._paste_into_current()

    assert operations == [([copied], tmp_path, False), ([moved], tmp_path, True)]
    assert refreshed == [True, True]
    assert tab._clipboard is None


def test_file_browser_tab_paste_marks_partial_success_changed_dirs(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    changed: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    tab._clipboard = {"paths": [source], "mode": "copy"}
    monkeypatch.setattr(
        tab,
        "_perform_copy_or_move_with_result",
        lambda paths, dest_dir, move: FileOperationResult(["copy failed"], [dest_dir]),
    )
    monkeypatch.setattr(tab, "_mark_changed_directories", lambda dirs: changed.extend(dirs))
    monkeypatch.setattr(tab, "refresh", lambda: None)
    monkeypatch.setattr(tab, "_update_action_states", lambda: None)

    tab._paste_into_current()

    assert changed == [tmp_path]


def test_file_browser_tab_perform_copy_or_move_warns_on_errors(monkeypatch, qtbot) -> None:
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.perform_copy_or_move",
        lambda sources, dest_dir, move: ["copy failed"],
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._perform_copy_or_move([Path("source.txt")], Path("dest"), move=False)

    assert warnings == [("Operation issues", "copy failed")]


def test_file_browser_tab_perform_copy_or_move_with_result_warns_on_errors(
    monkeypatch, qtbot
) -> None:
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    result = FileOperationResult(["copy failed"], [Path("dest")])
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.perform_copy_or_move_with_result",
        lambda sources, dest_dir, move: result,
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    actual = tab._perform_copy_or_move_with_result([Path("source.txt")], Path("dest"), move=False)

    assert actual is result
    assert warnings == [("Operation issues", "copy failed")]


def test_file_browser_tab_section_resize_emits_name_width(monkeypatch, qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._media_icon_mode = False

    with qtbot.waitSignal(tab.nameColumnWidthChanged, timeout=1000) as blocker:
        tab._handle_section_resized(0, 100, 512)

    assert blocker.args == [512]
    assert tab.name_column_width() == 512


def test_file_browser_tab_ctrl_enter_opens_selected_folder(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_selected_index_path", lambda: selected)
    event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier,
    )

    with qtbot.waitSignal(tab.requestOpenInNewTab, timeout=1000) as blocker:
        tab.keyPressEvent(event)

    assert blocker.args == [selected]


def test_navigation_event_without_control_strips_ctrl_for_arrow_key() -> None:
    event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Down,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )

    replacement = navigation_event_without_control(event)

    assert replacement is not None
    assert replacement.key() == Qt.Key.Key_Down
    assert replacement.modifiers() == Qt.KeyboardModifier.ShiftModifier


def test_navigation_event_without_control_ignores_non_navigation_key() -> None:
    event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_A,
        Qt.KeyboardModifier.ControlModifier,
    )

    assert navigation_event_without_control(event) is None


def test_navigation_cursor_action_maps_arrow_keys() -> None:
    assert navigation_cursor_action(Qt.Key.Key_Down) == QAbstractItemView.CursorAction.MoveDown
    assert navigation_cursor_action(Qt.Key.Key_A) is None


def test_tree_view_does_not_intercept_left_right_navigation(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    assert not tab._tree_view._select_single_navigation_target(Qt.Key.Key_Left)
    assert not tab._tree_view._select_single_navigation_target(Qt.Key.Key_Right)


def test_tile_view_allows_left_right_single_navigation(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    model = QStandardItemModel()
    for name in ("first", "second"):
        model.appendRow(QStandardItem(name))
    view = tab._tile_view
    view.setModel(model)
    selection_model = view.selectionModel()
    assert selection_model is not None
    selection_model.setCurrentIndex(
        model.index(0, 0), QItemSelectionModel.SelectionFlag.ClearAndSelect
    )

    assert view._select_single_navigation_target(Qt.Key.Key_Right)
    assert [index.row() for index in selection_model.selectedRows()] == [1]


def test_file_view_mouse_release_delegates_regular_buttons(monkeypatch, qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    view = tab._tile_view
    calls: list[object] = []

    monkeypatch.setattr(QListView, "mouseReleaseEvent", lambda _self, event: calls.append(event))
    event = _MouseReleaseStub(Qt.MouseButton.LeftButton)

    view.mouseReleaseEvent(event)

    assert calls == [event]
    assert not event.accepted


def test_file_view_arrow_navigation_clears_extended_selection(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    model = QStandardItemModel()
    for name in ("first", "second", "third"):
        model.appendRow(QStandardItem(name))
    view = tab._tree_view
    view.setModel(model)
    first = model.index(0, 0)
    third = model.index(2, 0)
    selection_model = view.selectionModel()
    assert selection_model is not None
    selection_model.setCurrentIndex(first, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    selection_model.select(third, QItemSelectionModel.SelectionFlag.Select)

    view.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Down,
            Qt.KeyboardModifier.NoModifier,
        )
    )

    selected_rows = [index.row() for index in selection_model.selectedRows()]
    assert selected_rows == [1]


def test_file_view_event_accepts_url_drag_enter(qtbot, tmp_path: Path) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.TargetMoveAction
    view = tab._tile_view

    enter_event = QDragEnterEvent(
        QPoint(1, 1),
        actions,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )

    assert view.event(enter_event)
    assert enter_event.isAccepted()
    assert enter_event.dropAction() == Qt.DropAction.MoveAction


def test_file_view_drop_uses_controller_path_when_model_drop_is_rejected(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    event = _DropStub(mime)
    calls: list[tuple[list[Path], Path, bool]] = []
    monkeypatch.setattr(tab._tile_view, "indexAt", lambda _point: QModelIndex())
    monkeypatch.setattr(
        tab,
        "_handle_external_drop",
        lambda paths, target_dir, move: calls.append((paths, target_dir, move)) or True,
    )

    assert not tab._model.canDropMimeData(
        mime,
        Qt.DropAction.MoveAction,
        0,
        0,
        QModelIndex(),
    )
    assert tab._tile_view._handle_drop_event(event)

    assert calls == [([source], tmp_path, True)]
    assert event.accepted
    assert event.dropAction() == Qt.DropAction.MoveAction


def test_file_view_drag_paths_falls_back_to_drag_start_path(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    source = tmp_path / "source.txt"
    selected = tmp_path / "other.txt"
    view = tab._tile_view
    view._drag_start_path = source

    monkeypatch.setattr(view, "selected_paths", lambda: [])
    assert view._drag_paths() == [source]

    monkeypatch.setattr(view, "selected_paths", lambda: [selected])
    assert view._drag_paths() == [source]

    monkeypatch.setattr(view, "selected_paths", lambda: [source, selected])
    assert view._drag_paths() == [source, selected]


def test_file_view_reset_drag_state_clears_internal_drag_state(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    view = tab._tile_view
    view._drag_start_path = tmp_path / "source.txt"
    view.setState(QAbstractItemView.State.DragSelectingState)
    update_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(view.viewport(), "update", lambda *args: update_calls.append(args))

    view._reset_drag_state()

    assert view._drag_start_path is None
    assert view.state() == QAbstractItemView.State.NoState
    assert update_calls == [()]


def test_file_view_clear_drag_selection_artifacts_keeps_drag_path(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    view = tab._tile_view
    source = tmp_path / "source.txt"
    view._drag_start_path = source
    view.setState(QAbstractItemView.State.DragSelectingState)
    update_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(view.viewport(), "update", lambda *args: update_calls.append(args))

    view._clear_drag_selection_artifacts()

    assert view._drag_start_path == source
    assert view.state() == QAbstractItemView.State.NoState
    assert update_calls == [()]


def test_file_view_mouse_move_on_item_before_drag_threshold_does_not_select(
    monkeypatch, qtbot
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    view = tab._tile_view
    view._drag_start_pos = QPointF(10, 10)
    view._drag_on_item = True
    view.setState(QAbstractItemView.State.NoState)
    started: list[Qt.DropAction] = []
    monkeypatch.setattr(view, "startDrag", lambda actions: started.append(actions))
    event = _MouseMoveStub(QPointF(12, 12))

    view.mouseMoveEvent(event)

    assert event.accepted
    assert started == []
    assert view.state() == QAbstractItemView.State.NoState


def test_file_view_drag_leave_resets_drag_state(qtbot, tmp_path: Path) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    view = tab._tile_view
    view._drag_start_path = tmp_path / "source.txt"
    model = QStandardItemModel()
    model.appendRow(QStandardItem("target"))
    view.setModel(model)
    view._drop_target_index = model.index(0, 0)
    view.setState(QAbstractItemView.State.DragSelectingState)

    view.event(QEvent(QEvent.Type.DragLeave))

    assert view._drag_start_path is None
    assert not view._drop_target_index.isValid()
    assert view.state() == QAbstractItemView.State.NoState


def test_file_view_drop_target_highlight_tracks_directory_indexes(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    view = tab._tile_view
    model = QStandardItemModel()
    for name in ("folder", "file.txt"):
        model.appendRow(QStandardItem(name))
    view.setModel(model)
    folder = tmp_path / "folder"
    file_path = tmp_path / "file.txt"
    updated: list[QRect] = []

    monkeypatch.setattr(
        view,
        "indexAt",
        lambda pos: model.index(0, 0) if pos.x() == 0 else model.index(1, 0),
    )
    monkeypatch.setattr(
        tab._model,
        "fileInfo",
        lambda index: _FakeFileInfo(
            folder if index.row() == 0 else file_path, is_dir=index.row() == 0
        ),
    )
    monkeypatch.setattr(view, "visualRect", lambda index: QRect(index.row() * 10, 0, 10, 10))
    monkeypatch.setattr(view.viewport(), "update", lambda rect: updated.append(QRect(rect)))

    view._update_drop_target_highlight(QPoint(0, 0))

    assert view._is_drop_target_index(model.index(0, 0))
    assert updated == [QRect(0, 0, 10, 10)]

    view._update_drop_target_highlight(QPoint(1, 0))

    assert not view._drop_target_index.isValid()
    assert updated[-1] == QRect(0, 0, 10, 10)


def test_file_view_drop_target_highlight_ignores_initial_file_index(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    view = tab._tile_view
    model = QStandardItemModel()
    model.appendRow(QStandardItem("file.txt"))
    view.setModel(model)
    updated: list[QRect] = []

    monkeypatch.setattr(view, "indexAt", lambda _pos: model.index(0, 0))
    monkeypatch.setattr(
        tab._model,
        "fileInfo",
        lambda _index: _FakeFileInfo(tmp_path / "file.txt", is_dir=False),
    )
    monkeypatch.setattr(view.viewport(), "update", lambda rect: updated.append(QRect(rect)))

    view._update_drop_target_highlight(QPoint(0, 0))

    assert not view._drop_target_index.isValid()
    assert updated == []


def test_file_browser_tab_tree_delegate_marks_only_drop_target_selected(monkeypatch, qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._DropTargetItemDelegate, tab._tree_view.itemDelegate()
    )
    model = QStandardItemModel()
    model.appendRow([QStandardItem("folder"), QStandardItem("detail")])
    model.appendRow([QStandardItem("other"), QStandardItem("detail")])
    target = model.index(0, 0)
    other = model.index(1, 0)
    tab._tree_view.setModel(model)
    tab._tree_view._drop_target_index = target
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 120, 20)
    option.palette = tab._tree_view.palette()
    option.widget = tab._tree_view
    pixmap = QPixmap(option.rect.size())
    painter = QPainter(pixmap)
    states: list[QStyle.StateFlag] = []

    def capture_paint(_self, _painter, painted_option, _index) -> None:
        states.append(painted_option.state)

    monkeypatch.setattr(file_browser_delegates_module.QStyledItemDelegate, "paint", capture_paint)

    delegate.paint(painter, option, target)
    delegate.paint(painter, option, other)
    painter.end()

    assert states[0] & QStyle.StateFlag.State_Selected
    assert not states[1] & QStyle.StateFlag.State_Selected


def test_file_browser_tab_tile_delegate_uses_highlight_for_drop_target(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(
        file_browser_delegates_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate()
    )
    model = QStandardItemModel()
    model.appendRow(QStandardItem("folder"))
    index = model.index(0, 0)
    tab._tile_view.setModel(model)
    tab._tile_view._drop_target_index = index
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 184, 222)
    option.decorationSize = QSize(160, 160)
    option.fontMetrics = tab._tile_view.fontMetrics()
    option.palette = tab._tile_view.palette()
    option.widget = tab._tile_view
    _, text_rect = delegate._tile_rects(option)
    pixmap = QPixmap(option.rect.size())
    pixmap.fill(option.palette.base().color())
    painter = QPainter(pixmap)

    delegate.paint(painter, option, index)
    painter.end()

    image = pixmap.toImage()
    assert image.pixelColor(text_rect.center()) == option.palette.highlight().color()


def test_file_browser_tab_selection_changed_repaints_selected_and_deselected(
    monkeypatch, qtbot
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    model = QStandardItemModel()
    for name in ("first.txt", "second.txt"):
        model.appendRow(QStandardItem(name))
    view = tab._tree_view
    tab._media_icon_mode = False
    view.setModel(model)
    updated: list[QRect] = []
    monkeypatch.setattr(view.viewport(), "update", lambda rect: updated.append(QRect(rect)))
    monkeypatch.setattr(
        view,
        "visualRect",
        lambda index: QRect(index.row() * 10, 0, 10, 10),
    )
    selected = QItemSelection(model.index(1, 0), model.index(1, 0))
    deselected = QItemSelection(model.index(0, 0), model.index(0, 0))

    tab._handle_selection_changed(selected, deselected)

    assert len(updated) == 2
    assert all(rect.isValid() for rect in updated)


def test_file_browser_tab_tile_selection_changed_repaints_entire_viewport(
    monkeypatch, qtbot
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    model = QStandardItemModel()
    for name in ("first.txt", "second.txt"):
        model.appendRow(QStandardItem(name))
    view = tab._tile_view
    tab._media_icon_mode = True
    view.setModel(model)
    update_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(view.viewport(), "update", lambda *args: update_calls.append(args))
    selected = QItemSelection(model.index(1, 0), model.index(1, 0))
    deselected = QItemSelection(model.index(0, 0), model.index(0, 0))

    tab._handle_selection_changed(selected, deselected)

    assert update_calls == [()]


def test_file_browser_tab_copy_and_cut_selected_update_clipboard(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_selected_paths", lambda: [source])

    tab._copy_selected()
    assert tab._clipboard == {"paths": [source], "mode": "copy"}

    tab._cut_selected()
    assert tab._clipboard == {"paths": [source], "mode": "move"}


def test_file_browser_tab_clipboard_visual_mode_matches_normalized_path(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    model = QStandardItemModel()
    model.appendRow(QStandardItem(source.name))
    index = model.index(0, 0)
    monkeypatch.setattr(tab._model, "filePath", lambda _index: str(source))
    monkeypatch.setattr(tab, "_repaint_clipboard_paths", lambda _paths: None)
    monkeypatch.setattr(tab, "_update_action_states", lambda: None)

    tab._set_clipboard({"paths": [source], "mode": "copy"})
    assert tab._clipboard_visual_mode_for_index(index) == "copy"

    tab._set_clipboard({"paths": [source], "mode": "move"})
    assert tab._clipboard_visual_mode_for_index(index) == "move"

    tab._set_clipboard({"paths": [tmp_path / "other.txt"], "mode": "copy"})
    assert tab._clipboard_visual_mode_for_index(index) is None


def test_file_browser_tab_clipboard_path_normalization_does_not_resolve_symlinks(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)

    def fail_resolve(self, *args, **kwargs):
        raise AssertionError(f"should not resolve {self}")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    normalized = tab._normalise_clipboard_path(tmp_path / "link.txt")

    assert normalized.is_absolute()
    assert normalized.name == "link.txt"


def test_file_browser_tab_set_clipboard_repaints_previous_and_current_targets(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    previous = tmp_path / "previous.txt"
    current = tmp_path / "current.txt"
    repainted: list[set[Path]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._clipboard = {"paths": [previous], "mode": "copy"}
    tab._clipboard_path_set = {tab._normalise_clipboard_path(previous)}
    monkeypatch.setattr(tab, "_repaint_clipboard_paths", lambda paths: repainted.append(paths))
    monkeypatch.setattr(tab, "_update_action_states", lambda: None)

    tab._set_clipboard({"paths": [current], "mode": "move"})

    assert tab._clipboard == {"paths": [current], "mode": "move"}
    assert repainted == [
        {
            tab._normalise_clipboard_path(previous),
            tab._normalise_clipboard_path(current),
        }
    ]


def test_file_browser_tab_set_clipboard_none_repaints_previous_target(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    previous = tmp_path / "previous.txt"
    repainted: list[set[Path]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._clipboard = {"paths": [previous], "mode": "move"}
    tab._clipboard_path_set = {tab._normalise_clipboard_path(previous)}
    monkeypatch.setattr(tab, "_repaint_clipboard_paths", lambda paths: repainted.append(paths))
    monkeypatch.setattr(tab, "_update_action_states", lambda: None)

    tab._set_clipboard(None)

    assert tab._clipboard is None
    assert tab._clipboard_path_set == set()
    assert repainted == [{tab._normalise_clipboard_path(previous)}]


def test_file_browser_tab_delete_selected_confirms_deletes_and_refreshes(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    refreshed: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_selected_paths", lambda: [source])
    monkeypatch.setattr(tab, "_selection_path_before_deleted_items", lambda paths: tmp_path)
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    tab._delete_selected()

    assert not source.exists()
    assert tab._pending_selection_path == tmp_path
    assert refreshed == [True]


def test_file_browser_tab_delete_then_go_up_invalidates_folder_preview(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    source = child / "source.jpg"
    source.write_text("source", encoding="utf-8")
    invalidated: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(child)
    monkeypatch.setattr(tab, "_selected_paths", lambda: [source])
    monkeypatch.setattr(tab, "_selection_path_before_deleted_items", lambda paths: None)
    monkeypatch.setattr(tab._model, "invalidate_folder_thumbnail_preview", invalidated.append)
    monkeypatch.setattr(
        file_browser_navigation_controller_module,
        "directory_fingerprint_changed",
        lambda path, previous: False,
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    tab._delete_selected()
    tab.go_up()

    assert invalidated == [child]


def test_file_browser_tab_delete_selected_warns_when_delete_reports_errors(
    monkeypatch,
    qtbot,
) -> None:
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_selected_paths", lambda: [Path("missing.txt")])
    monkeypatch.setattr(tab, "_selection_path_before_deleted_items", lambda paths: None)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        file_browser_operations_controller_module, "delete_paths", lambda paths: ["delete failed"]
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.operations_controller.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._delete_selected()

    assert warnings == [("Move to Trash failed", "delete failed")]


def test_file_browser_tab_execute_command_warns_when_start_fails(monkeypatch, qtbot) -> None:
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        tab,
        "_resolve_program_for_windows",
        lambda program: ("C:/bin/tool.exe", False),
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.command_runner.QProcess.startDetached",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser.command_runner.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._execute_address_command("tool --flag")

    assert warnings == [("Command", "Failed to start:\ntool --flag")]


def test_file_browser_tab_request_settled_thumbnails_resets_scrolling(
    monkeypatch,
    qtbot,
) -> None:
    calls: list[bool] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._is_scrolling_for_thumbnails = True
    monkeypatch.setattr(
        tab,
        "_request_visible_thumbnails",
        lambda scrolling=False: calls.append(scrolling),
    )

    tab._request_settled_thumbnails()

    assert not tab._is_scrolling_for_thumbnails
    assert calls == [False]


def test_file_browser_tab_request_visible_thumbnails_batches_idle_work(
    monkeypatch,
    qtbot,
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._is_active = True
    monkeypatch.setattr(tab, "_visible_tile_indexes", lambda _view: [QModelIndex()])
    calls: list[tuple[int | None, bool]] = []

    def set_visible_thumbnail_targets(indexes, *, request_limit=None, allow_folder_preview=True):
        calls.append((request_limit, allow_folder_preview))
        return 1

    monkeypatch.setattr(
        tab._model,
        "set_visible_thumbnail_targets",
        set_visible_thumbnail_targets,
    )

    tab._request_visible_thumbnails(scrolling=False)

    assert calls == [(6, True)]
    assert tab._thumbnail_idle_batch_timer.isActive()

    tab._thumbnail_idle_batch_timer.stop()
    calls.clear()
    tab._request_visible_thumbnails(scrolling=True)

    assert calls == [(6, False)]
    assert not tab._thumbnail_idle_batch_timer.isActive()


def test_file_browser_tab_selection_status_uses_cached_item_counts(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    selected = tmp_path / "selected.txt"
    selected.write_bytes(b"abc")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    qtbot.wait(100)
    tab._status_folder_count = 4
    tab._status_file_count = 5
    monkeypatch.setattr(tab, "_request_status_item_counts", lambda _path: None)
    monkeypatch.setattr(tab, "_selected_paths", lambda: [selected])
    monkeypatch.setattr(
        file_browser_status_controller_module,
        "directory_item_counts",
        lambda _path: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    statuses: list[object] = []
    tab.statusChanged.connect(statuses.append)

    tab._update_action_states()

    status = cast(BrowserStatus, statuses[-1])
    assert status.folder_count == 4
    assert status.file_count == 5
    assert status.selected_count == 1
    assert status.selected_file_size == 3


def test_file_browser_tab_directory_loaded_updates_status_item_counts(
    monkeypatch,
    qtbot,
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        file_browser_status_controller_module, "directory_item_counts", lambda _path: (2, 3)
    )
    monkeypatch.setattr(tab, "_selected_paths", lambda: [])
    statuses: list[BrowserStatus] = []
    tab.statusChanged.connect(lambda status: statuses.append(cast(BrowserStatus, status)))

    tab._on_directory_loaded("")

    qtbot.waitUntil(lambda: bool(statuses) and statuses[-1].total_count == 5, timeout=1000)
    status = statuses[-1]
    assert tab._status_folder_count == 2
    assert tab._status_file_count == 3
    assert status.total_count == 5


def test_file_browser_tab_request_status_counts_keeps_previous_counts_until_ready(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    selected = tmp_path / "selected.txt"
    selected.write_bytes(b"abc")

    class NoopThreadPool:
        def __init__(self) -> None:
            self.jobs: list[object] = []

        def start(self, job: object) -> None:
            self.jobs.append(job)

    pool = NoopThreadPool()
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._status_folder_count = 8
    tab._status_file_count = 13
    tab._status_count_pool = cast(QThreadPool, pool)
    monkeypatch.setattr(tab, "_selected_paths", lambda: [selected])
    statuses: list[BrowserStatus] = []
    tab.statusChanged.connect(lambda status: statuses.append(cast(BrowserStatus, status)))

    tab._request_status_item_counts(tmp_path)

    assert pool.jobs
    assert tab._status_folder_count == 8
    assert tab._status_file_count == 13
    assert statuses == []


def test_file_browser_tab_activate_restarts_cancelled_status_count_job(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    requested: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._current_path = tmp_path
    tab._is_active = True
    tab._status_count_jobs[1] = cast(
        file_browser_status_controller_module._DirectoryCountJob, object()
    )
    monkeypatch.setattr(tab, "_request_status_item_counts", requested.append)

    tab.deactivate()
    tab.activate()

    assert tab._is_active
    assert requested == [tmp_path]


def test_file_browser_tab_async_status_counts_keep_current_selection(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    selected = tmp_path / "selected.txt"
    selected.write_bytes(b"abc")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab._current_path = tmp_path
    tab._status_count_generation = 7
    monkeypatch.setattr(tab, "_selected_paths", lambda: [selected])
    statuses: list[BrowserStatus] = []
    tab.statusChanged.connect(lambda status: statuses.append(cast(BrowserStatus, status)))

    tab._handle_status_item_counts_ready(str(tmp_path), 7, 2, 3)

    status = statuses[-1]
    assert status.folder_count == 2
    assert status.file_count == 3
    assert status.selected_count == 1
    assert status.selected_file_size == 3


class _FakeFileInfo:
    def __init__(self, path: Path, *, is_dir: bool) -> None:
        self._path = path
        self._is_dir = is_dir

    def isDir(self) -> bool:
        return self._is_dir

    def isFile(self) -> bool:
        return not self._is_dir

    def absoluteFilePath(self) -> str:
        return str(self._path)


def test_file_browser_tab_handle_current_changed_emits_for_directory(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        tab._model,
        "fileInfo",
        lambda _index: _FakeFileInfo(tmp_path, is_dir=True),
    )

    with qtbot.waitSignal(tab.directoryChanged, timeout=1000) as blocker:
        tab._handle_current_changed(tab._model.index(str(tmp_path)), tab._model.index(""))

    assert blocker.args == [tmp_path]
