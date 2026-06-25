"""SortedFileSystemModel をタブ経由で動かす結合テスト。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt

from omnidesk.ui.file_browser_tab import FileBrowserTab


def _make_files(directory: Path) -> None:
    (directory / "sub").mkdir()
    for name in ("b.txt", "a.png", "c.txt", "d.png"):
        (directory / name).write_text("x", encoding="utf-8")


def _visible_names(tab: FileBrowserTab) -> list[str]:
    model = tab._model
    root = model.index(str(tab.current_path()))
    names = []
    for row in range(model.rowCount(root)):
        index = model.index(row, 0, root)
        names.append(index.data(Qt.ItemDataRole.DisplayRole))
    return names


def _wait_for_entries(qtbot, tab: FileBrowserTab, expected: int) -> None:
    qtbot.waitUntil(lambda: len(_visible_names(tab)) >= expected, timeout=3000)


def test_default_sort_is_name_with_folders_first(qtbot, tmp_path: Path) -> None:
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    assert tab.sort_mode() == "name"
    names = _visible_names(tab)
    assert names[0] == "sub"  # フォルダが先頭
    assert names[1:] == ["a.png", "b.txt", "c.txt", "d.png"]


def test_extension_sort_groups_by_extension(qtbot, tmp_path: Path) -> None:
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    tab.set_sort_mode("extension")
    qtbot.waitUntil(
        lambda: _visible_names(tab) == ["sub", "a.png", "d.png", "b.txt", "c.txt"],
        timeout=3000,
    )


def test_sort_mode_can_toggle_back_to_name(qtbot, tmp_path: Path) -> None:
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    tab.set_sort_mode("extension")
    tab.set_sort_mode("name")
    assert tab.sort_mode() == "name"
    qtbot.waitUntil(
        lambda: _visible_names(tab) == ["sub", "a.png", "b.txt", "c.txt", "d.png"],
        timeout=3000,
    )


def test_build_sort_menu_reflects_current_mode(qtbot, tmp_path: Path) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)

    tab.set_sort_mode("extension")
    menu = tab.build_sort_menu(tab)
    actions = menu.actions()
    labels = {action.text(): action.isChecked() for action in actions}
    assert labels == {"名前順": False, "拡張子順": True}


def test_file_path_and_file_info_map_through_proxy(qtbot, tmp_path: Path) -> None:
    target = tmp_path / "a.png"
    _make_files(tmp_path)
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(tmp_path)
    _wait_for_entries(qtbot, tab, 5)

    index = tab._model.index(str(target))
    assert index.isValid()
    assert Path(tab._model.filePath(index)) == target
    assert tab._model.fileInfo(index).fileName() == "a.png"
