from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QModelIndex

import omnidesk.ui.column_browser as column_browser_module
from omnidesk.ui.column_browser import ColumnBrowser


def test_set_root_path_accepts_directory_and_file(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    child_file = tmp_path / "child.txt"
    child_file.write_text("child", encoding="utf-8")

    with qtbot.waitSignal(browser.currentPathChanged, timeout=1000) as dir_signal:
        browser.set_root_path(tmp_path)

    assert browser.current_path() == tmp_path
    assert browser._path_edit.text() == str(tmp_path)
    assert dir_signal.args == [tmp_path]

    with qtbot.waitSignal(browser.currentPathChanged, timeout=1000) as file_signal:
        browser.set_root_path(child_file)

    assert browser.current_path() == tmp_path
    assert file_signal.args == [tmp_path]


def test_set_root_path_warns_for_missing_path(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        column_browser_module.QMessageBox,
        "warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    browser.set_root_path(tmp_path / "missing")

    assert warnings == [("Cannot navigate", f"{tmp_path / 'missing'} does not exist.")]


def test_go_up_and_path_entry_delegate_to_set_root_path(qtbot, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(child)

    browser.go_up()

    assert browser.current_path() == parent

    browser._path_edit.setText(str(child))
    browser._handle_path_entered()

    assert browser.current_path() == child


def test_handle_selection_changed_ignores_invalid_index(qtbot) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    original = browser.current_path()

    browser._handle_selection_changed(QModelIndex(), QModelIndex())

    assert browser.current_path() == original
