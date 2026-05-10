from __future__ import annotations

from pathlib import Path
from typing import cast

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent, QShortcut
from PyQt6.QtWidgets import QMessageBox

import omnidesk.ui.file_browser_tab as file_browser_tab_module
from omnidesk.ui.file_browser_status import BrowserStatus
from omnidesk.ui.file_browser_tab import FileBrowserTab


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
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(child)
    monkeypatch.setattr(tab, "_select_path", lambda path: selected.append(path) or True)

    tab.go_up()
    qtbot.wait(20)

    assert tab.current_path() == parent
    assert selected == [child]


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
    selected: list[Path] = []
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(parent)
    tab.navigate_to(child)
    monkeypatch.setattr(tab, "_select_path", lambda path: selected.append(path) or True)

    tab.go_back()

    assert tab.current_path() == parent
    assert selected == [child]


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
    statuses: list[object] = []
    tab.statusChanged.connect(statuses.append)

    tab._on_directory_loaded("")

    status = cast(BrowserStatus, statuses[-1])
    assert tab._status_folder_count == 2
    assert tab._status_file_count == 3
    assert status.total_count == 5


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
