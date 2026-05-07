from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QMessageBox

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
