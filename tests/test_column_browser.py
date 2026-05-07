from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QModelIndex, QUrl

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


def test_refresh_and_focus_view_delegate_to_child_widgets(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    refreshed: list[object] = []
    focused: list[object] = []
    monkeypatch.setattr(browser._model, "refresh", lambda index: refreshed.append(index), raising=False)
    monkeypatch.setattr(browser._view, "setFocus", lambda reason: focused.append(reason))

    browser.refresh()
    browser.focus_view()

    assert len(refreshed) == 1
    assert focused


class _FakeFileInfo:
    def __init__(self, path: Path, *, is_dir: bool) -> None:
        self._path = path
        self._is_dir = is_dir

    def absoluteFilePath(self) -> str:
        return str(self._path)

    def isDir(self) -> bool:
        return self._is_dir


def test_handle_activated_navigates_to_directory(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    target = tmp_path / "child"
    target.mkdir()
    calls: list[Path] = []
    monkeypatch.setattr(
        browser._model,
        "fileInfo",
        lambda _index: _FakeFileInfo(target, is_dir=True),
    )
    monkeypatch.setattr(browser, "set_root_path", lambda path: calls.append(path))

    browser._handle_activated(QModelIndex())

    assert calls == [target]


def test_handle_activated_opens_file(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    target = tmp_path / "file.txt"
    opened: list[QUrl] = []
    monkeypatch.setattr(
        browser._model,
        "fileInfo",
        lambda _index: _FakeFileInfo(target, is_dir=False),
    )
    monkeypatch.setattr(column_browser_module.QDesktopServices, "openUrl", opened.append)

    browser._handle_activated(QModelIndex())

    assert [Path(url.toLocalFile()) for url in opened] == [target]


def test_refresh_falls_back_to_resetting_root_when_model_has_no_refresh(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)

    browser.refresh()

    assert browser.current_path() == tmp_path


def test_handle_path_entered_ignores_blank_input(monkeypatch, qtbot) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    calls: list[Path] = []
    monkeypatch.setattr(browser, "set_root_path", lambda path: calls.append(path))
    browser._path_edit.setText("   ")

    browser._handle_path_entered()

    assert calls == []


def test_handle_selection_changed_emits_for_directory(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    target = tmp_path / "selected"
    target.mkdir()
    monkeypatch.setattr(
        browser._model,
        "fileInfo",
        lambda _index: _FakeFileInfo(target, is_dir=True),
    )

    with qtbot.waitSignal(browser.currentPathChanged, timeout=1000) as blocker:
        browser._handle_selection_changed(browser._model.index(str(tmp_path)), QModelIndex())

    assert blocker.args == [target]
    assert browser.current_path() == target


def test_handle_selection_changed_updates_file_without_emitting(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    target = tmp_path / "selected.txt"
    target.write_text("selected", encoding="utf-8")
    monkeypatch.setattr(
        browser._model,
        "fileInfo",
        lambda _index: _FakeFileInfo(target, is_dir=False),
    )

    with qtbot.assertNotEmitted(browser.currentPathChanged, wait=100):
        browser._handle_selection_changed(browser._model.index(str(tmp_path)), QModelIndex())

    assert browser.current_path() == target
