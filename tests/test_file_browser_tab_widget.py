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
    QKeyEvent,
    QPainter,
    QPixmap,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
)
from PyQt6.QtWidgets import QAbstractItemView, QListView, QMessageBox, QStyle, QStyleOptionViewItem

import omnidesk.ui.file_browser_tab as file_browser_tab_module
from omnidesk.ui.file_browser_status import BrowserStatus
from omnidesk.ui.file_browser_tab import (
    FileBrowserTab,
    navigation_cursor_action,
    navigation_event_without_control,
)


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
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
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
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
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
    delegate = cast(file_browser_tab_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate())
    name = "(C56) [ART THEATER] M.F.H.H. M&D (宇宙海賊ミトの大冒険、おジャ魔女どれみ)"

    wrapped = delegate._two_line_text(name, tab._tile_view.fontMetrics(), 120)
    lines = wrapped.splitlines()

    assert len(lines) == 2
    assert lines[0] != name
    assert all(line for line in lines)


def test_file_browser_tab_tile_delegate_places_two_line_label_below_icon(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(file_browser_tab_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate())
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 184, 222)
    option.decorationSize = QSize(160, 160)
    option.fontMetrics = tab._tile_view.fontMetrics()

    icon_rect, text_rect = delegate._tile_rects(option)

    assert icon_rect == QRect(12, 4, 160, 160)
    assert text_rect.y() > icon_rect.bottom()
    assert text_rect.height() == option.fontMetrics.lineSpacing() * 2
    assert text_rect.bottom() <= option.rect.bottom()


def test_file_browser_tab_tile_delegate_does_not_fill_selected_tile_background(qtbot) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    delegate = cast(file_browser_tab_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate())
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
    delegate = cast(file_browser_tab_module._TwoLineTileNameDelegate, tab._tile_view.itemDelegate())
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
        "omnidesk.ui.file_browser_tab.QMessageBox.question",
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
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
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
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
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
        "omnidesk.ui.file_browser_tab.QProcess.startDetached",
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


def test_file_browser_tab_execute_command_starts_cmd_special_case(monkeypatch, qtbot) -> None:
    starts: list[tuple[str, list[str], str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setenv("COMSPEC", "C:/Windows/System32/cmd.exe")
    monkeypatch.setattr(
        "omnidesk.ui.file_browser_tab.QProcess.startDetached",
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
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
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
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )
    monkeypatch.setattr(tab, "_perform_copy_or_move", lambda *args, **kwargs: copied.append(True))

    tab._handle_external_drop([source], nested, move=True)

    assert warnings == [("Drop failed", "Cannot move a folder into itself.")]
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
        "_perform_copy_or_move",
        lambda paths, target_dir, move: operations.append((paths, target_dir, move)),
    )
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))

    tab._handle_external_drop([source], dest, move=False)

    assert operations == [([source], dest, False)]
    assert refreshed == [True]


def test_file_browser_tab_rename_selected_success(monkeypatch, qtbot, tmp_path: Path) -> None:
    original = tmp_path / "old.txt"
    original.write_text("old", encoding="utf-8")
    refreshed: list[bool] = []
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_selected_paths", lambda: [original])
    monkeypatch.setattr(
        "omnidesk.ui.file_browser_tab.QInputDialog.getText",
        lambda *args, **kwargs: ("new.txt", True),
    )
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))
    monkeypatch.setattr(tab, "_select_path", lambda path: selected.append(path) or True)

    tab._rename_selected()

    assert not original.exists()
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "old"
    assert refreshed == [True]
    assert selected == [tmp_path / "new.txt"]


def test_file_browser_tab_rename_selected_warns_for_conflict(
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
    monkeypatch.setattr(tab, "_selected_paths", lambda: [original])
    monkeypatch.setattr(
        "omnidesk.ui.file_browser_tab.QInputDialog.getText",
        lambda *args, **kwargs: ("target.txt", True),
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._rename_selected()

    assert warnings == [("Rename failed", f"{target} already exists.")]
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
        "omnidesk.ui.file_browser_tab.QInputDialog.getText",
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
        "_perform_copy_or_move",
        lambda paths, dest_dir, move: operations.append((paths, dest_dir, move)),
    )
    monkeypatch.setattr(tab, "refresh", lambda: refreshed.append(True))

    tab._clipboard = {"paths": [copied], "mode": "copy"}
    tab._paste_into_current()
    tab._clipboard = {"paths": [moved], "mode": "move"}
    tab._paste_into_current()

    assert operations == [([copied], tmp_path, False), ([moved], tmp_path, True)]
    assert refreshed == [True, True]
    assert tab._clipboard is None


def test_file_browser_tab_perform_copy_or_move_warns_on_errors(monkeypatch, qtbot) -> None:
    warnings: list[tuple[str, str]] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "omnidesk.ui.file_browser_tab.perform_copy_or_move",
        lambda sources, dest_dir, move: ["copy failed"],
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._perform_copy_or_move([Path("source.txt")], Path("dest"), move=False)

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
    view.setState(QAbstractItemView.State.DragSelectingState)

    view.event(QEvent(QEvent.Type.DragLeave))

    assert view._drag_start_path is None
    assert view.state() == QAbstractItemView.State.NoState


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
        "omnidesk.ui.file_browser_tab.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    tab._delete_selected()

    assert not source.exists()
    assert tab._pending_selection_path == tmp_path
    assert refreshed == [True]


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
        "omnidesk.ui.file_browser_tab.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(file_browser_tab_module, "delete_paths", lambda paths: ["delete failed"])
    monkeypatch.setattr(
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    tab._delete_selected()

    assert warnings == [("Delete failed", "delete failed")]


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
        "omnidesk.ui.file_browser_tab.QProcess.startDetached",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "omnidesk.ui.file_browser_tab.QMessageBox.warning",
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
    tab._status_folder_count = 4
    tab._status_file_count = 5
    monkeypatch.setattr(tab, "_selected_paths", lambda: [selected])
    monkeypatch.setattr(
        file_browser_tab_module,
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
    monkeypatch.setattr(file_browser_tab_module, "directory_item_counts", lambda _path: (2, 3))
    monkeypatch.setattr(tab, "_selected_paths", lambda: [])
    statuses: list[BrowserStatus] = []
    tab.statusChanged.connect(lambda status: statuses.append(cast(BrowserStatus, status)))

    tab._on_directory_loaded("")

    qtbot.waitUntil(lambda: bool(statuses) and statuses[-1].total_count == 5, timeout=1000)
    status = statuses[-1]
    assert tab._status_folder_count == 2
    assert tab._status_file_count == 3
    assert status.total_count == 5


def test_file_browser_tab_request_status_counts_clears_stale_counts(
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
    assert tab._status_folder_count == 0
    assert tab._status_file_count == 0
    status = statuses[-1]
    assert status.folder_count == 0
    assert status.file_count == 0
    assert status.selected_count == 1
    assert status.selected_file_size == 3


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
