from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QModelIndex, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QIcon, QKeyEvent, QKeySequence, QWheelEvent
from PyQt6.QtWidgets import QListView, QWidget

import omnidesk.ui.column_browser as column_browser_module
import omnidesk.ui.column_browser_model as column_browser_model_module
import omnidesk.ui.column_browser_operations as column_browser_operations_module
from omnidesk.ui.column_browser import (
    EMPTY_PLACEHOLDER,
    LOADING_PLACEHOLDER,
    ColumnBrowser,
    clamp_scroll_maximum,
    column_placeholder_text,
    is_same_or_ancestor_path,
    normalize_directory_key,
    paste_destination,
    viewport_right_to_content_right,
)
from omnidesk.ui.column_browser_helpers import ERROR_PLACEHOLDER
from omnidesk.ui.column_browser_model import (
    _ColumnFileSystemModel,
    _DirectoryEntry,
    _DirectoryNode,
    _DirectoryScanJob,
    _ScanToken,
    _sort_entries,
)


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


def test_go_up_moves_from_displayed_root_not_selection(qtbot, tmp_path: Path) -> None:
    base = tmp_path / "base"
    sub = base / "sub"
    sub.mkdir(parents=True)
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(base)

    # Selecting a subfolder updates current_path to the selection...
    browser._handle_selection_changed(browser._model.index(str(sub)), QModelIndex())
    assert browser.current_path() == sub

    # ...but go_up must move up from the displayed base (base -> tmp_path),
    # not from the selection (which would re-root to the already-shown base).
    browser.go_up()

    assert browser.current_path() == tmp_path


def test_set_root_path_clears_stale_navigation_state(qtbot, tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    child = first / "child"
    child.mkdir(parents=True)
    second.mkdir()
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(first)
    child_index = browser._model.index(str(child))
    browser._view.setCurrentIndex(child_index)
    browser._view.set_focused_column_root(child_index)

    browser.set_root_path(second)

    assert not browser._view.currentIndex().isValid()
    assert browser._view.paste_directory() is None


def test_ensure_relevant_columns_visible_shows_hidden_path_column(qtbot, tmp_path) -> None:
    # QColumnView が現在パス上の列を hidden のまま取り残しても、明示的に可視化する。
    child = tmp_path / "child"
    child.mkdir()
    (child / "grandchild").mkdir()
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.show()
    qtbot.waitExposed(browser)
    browser.set_root_path(tmp_path)
    qtbot.waitUntil(
        lambda: any(column.rootIndex().isValid() for column in browser._view.column_views()),
        timeout=1000,
    )

    browser._current_path = tmp_path
    columns = [c for c in browser._view.column_views() if c.rootIndex().isValid()]
    assert columns
    target = columns[0]
    target.hide()
    assert target.isVisible() is False

    browser._ensure_relevant_columns_visible()

    assert target.isVisible() is True


def test_ensure_relevant_columns_visible_skips_unrelated_columns(qtbot, tmp_path) -> None:
    # 現在パスに無関係な（祖先でない）列は触らない。
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.show()
    qtbot.waitExposed(browser)
    browser.set_root_path(tmp_path)
    qtbot.waitUntil(
        lambda: any(column.rootIndex().isValid() for column in browser._view.column_views()),
        timeout=1000,
    )

    # current_path を実在しない無関係パスにして、root 列が祖先扱いされないようにする。
    browser._current_path = tmp_path.parent / "unrelated" / "deep"
    columns = [c for c in browser._view.column_views() if c.rootIndex().isValid()]
    assert columns
    target = columns[0]
    target.hide()

    browser._ensure_relevant_columns_visible()

    assert target.isVisible() is False


def test_set_root_path_cancels_scans_in_other_subtrees(qtbot, tmp_path, monkeypatch) -> None:
    # 別ツリーへ移ると、旧ツリーで走っているスキャンがキャンセルされ、スキャンプール
    # を占有し続けないこと。これがないと巨大フォルダのスキャンが新ルートのスキャンを
    # ブロックし、列が展開されない。
    deep = tmp_path / "a" / "deep"
    deep.mkdir(parents=True)
    other = tmp_path / "b"
    other.mkdir()
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    # スキャンを実際には走らせず「loading」のまま留めてスレッド占有を再現する。
    monkeypatch.setattr(browser._model._scan_pool, "start", lambda _job: None)

    browser.set_root_path(deep)
    deep_index = browser._model.index(str(deep))
    browser._model.rowCount(deep_index)
    deep_node = browser._model._node_from_index(deep_index)
    assert deep_node is not None
    assert deep_node.loading is True

    browser.set_root_path(other)

    assert deep_node.loading is False
    assert deep_node.scan_token is None


def test_handle_selection_changed_ignores_invalid_index(qtbot) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    original = browser.current_path()

    browser._handle_selection_changed(QModelIndex(), QModelIndex())

    assert browser.current_path() == original


def test_refresh_and_focus_view_delegate_to_child_widgets(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    refreshed: list[object] = []
    focused: list[object] = []
    monkeypatch.setattr(
        browser._model, "refresh", lambda index: refreshed.append(index), raising=False
    )
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


def test_refresh_falls_back_to_resetting_root_when_model_has_no_refresh(
    qtbot, tmp_path: Path
) -> None:
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


def test_handle_selection_changed_updates_file_without_emitting(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
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


def test_deeper_directory_selection_waits_for_column_range_change(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    base = tmp_path / "base"
    child = base / "child"
    child.mkdir(parents=True)
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(base)
    scheduled: list[object] = []
    monkeypatch.setattr(browser, "_schedule_settle", lambda: scheduled.append(object()))
    monkeypatch.setattr(
        browser._model,
        "fileInfo",
        lambda _index: _FakeFileInfo(child, is_dir=True),
    )

    browser._handle_selection_changed(browser._model.index(str(child)), QModelIndex())

    assert scheduled == []
    assert browser._pending_reveal is True
    assert browser._last_depth == len(child.parts)


def test_pending_reveal_is_consumed_even_without_range_change(monkeypatch, qtbot) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    settled: list[bool] = []
    monkeypatch.setattr(browser, "_settle_columns", lambda *, reveal: settled.append(reveal))

    browser._schedule_reveal()
    qtbot.wait(20)

    assert settled == [True]
    assert browser._pending_reveal is False
    assert browser._reveal_token is None


def test_shallower_directory_selection_schedules_dead_space_settle(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    base = tmp_path / "base"
    child = base / "child"
    child.mkdir(parents=True)
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(base)
    browser._last_depth = len(child.parts)
    scheduled: list[object] = []
    monkeypatch.setattr(browser, "_schedule_settle", lambda: scheduled.append(object()))
    monkeypatch.setattr(
        browser._model,
        "fileInfo",
        lambda _index: _FakeFileInfo(base, is_dir=True),
    )

    browser._handle_selection_changed(browser._model.index(str(base)), QModelIndex())

    assert len(scheduled) == 1
    assert browser._pending_reveal is False
    assert browser._last_depth == len(base.parts)


def test_column_placeholder_text_distinguishes_empty_from_loading() -> None:
    assert column_placeholder_text(row_count=3, loaded=True) is None
    assert column_placeholder_text(row_count=3, loaded=False) is None
    assert column_placeholder_text(row_count=0, loaded=True) == EMPTY_PLACEHOLDER
    assert column_placeholder_text(row_count=0, loaded=False) == LOADING_PLACEHOLDER


def test_clamp_scroll_maximum_only_covers_visible_columns() -> None:
    # Content narrower than the viewport leaves nothing to scroll.
    assert clamp_scroll_maximum(content_right=600, viewport_width=900) == 0
    # Wider content stays scrollable by exactly the overflow.
    assert clamp_scroll_maximum(content_right=1280, viewport_width=900) == 380


def test_viewport_right_to_content_right_preserves_horizontal_offset() -> None:
    assert viewport_right_to_content_right(scroll_value=0, viewport_right=900) == 900
    assert viewport_right_to_content_right(scroll_value=640, viewport_right=900) == 1540


def test_is_same_or_ancestor_path() -> None:
    assert is_same_or_ancestor_path("/tmp/root", "/tmp/root")
    assert is_same_or_ancestor_path("/tmp/root", "/tmp/root/child")
    assert not is_same_or_ancestor_path("/tmp/root", "/tmp/root-sibling")
    assert not is_same_or_ancestor_path("", "/tmp/root")


def test_settle_columns_keeps_existing_offset_for_viewport_relative_columns(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    class _Column:
        def __init__(self, *, x: int, width: int, visible: bool) -> None:
            self._x = x
            self._width = width
            self._visible = visible

        def rootIndex(self) -> QModelIndex:  # noqa: N802
            return browser._model.index(str(tmp_path))

        def isVisible(self) -> bool:  # noqa: N802
            return self._visible

        def x(self) -> int:
            return self._x

        def width(self) -> int:
            return self._width

        def viewport(self):
            return self

        def update(self) -> None:
            return None

        def show(self) -> None:
            return None

    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    hbar = browser._view.horizontalScrollBar()
    browser._settling = True
    hbar.setRange(0, 1000)
    hbar.setValue(640)
    browser._settling = False
    viewport_width = browser._view.viewport().width()
    monkeypatch.setattr(
        browser._view,
        "column_views",
        lambda: [
            _Column(x=0, width=viewport_width, visible=True),
            _Column(x=1600, width=viewport_width, visible=False),
        ],
    )

    browser._settle_columns(reveal=False)

    assert hbar.maximum() == 640
    assert hbar.value() == 640


def test_shallower_settle_animates_left_before_clamping_scroll_range(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    class _Column:
        def __init__(self, *, x: int, width: int) -> None:
            self._x = x
            self._width = width

        def rootIndex(self) -> QModelIndex:  # noqa: N802
            return browser._model.index(str(tmp_path))

        def isVisible(self) -> bool:  # noqa: N802
            return True

        def x(self) -> int:
            return self._x

        def width(self) -> int:
            return self._width

        def viewport(self):
            return self

        def update(self) -> None:
            return None

        def show(self) -> None:
            return None

    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.resize(420, 300)
    browser.set_root_path(tmp_path)
    hbar = browser._view.horizontalScrollBar()
    browser._settling = True
    hbar.setRange(0, 1000)
    hbar.setValue(640)
    browser._settling = False
    viewport_width = browser._view.viewport().width()
    monkeypatch.setattr(
        browser._view,
        "column_views",
        lambda: [_Column(x=-640, width=viewport_width)],
    )

    browser._settle_columns(reveal=False)

    assert browser._horizontal_scroll_animation is not None
    assert hbar.maximum() == 1000
    qtbot.waitUntil(lambda: browser._horizontal_scroll_animation is None, timeout=1000)
    assert hbar.value() == 0
    assert hbar.maximum() == 0


def test_shallower_selection_restores_qt_scroll_jump_before_animation(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    class _Column:
        def __init__(self, *, x: int, width: int) -> None:
            self._x = x
            self._width = width

        def rootIndex(self) -> QModelIndex:  # noqa: N802
            return browser._model.index(str(tmp_path))

        def isVisible(self) -> bool:  # noqa: N802
            return True

        def x(self) -> int:
            return self._x

        def width(self) -> int:
            return self._width

        def viewport(self):
            return self

        def update(self) -> None:
            return None

        def show(self) -> None:
            return None

    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.resize(420, 300)
    browser.set_root_path(tmp_path)
    hbar = browser._view.horizontalScrollBar()
    browser._previous_horizontal_scroll_value = 640
    browser._last_horizontal_scroll_value = 0
    browser._previous_horizontal_scroll_maximum = 1000
    browser._last_horizontal_scroll_maximum = 0
    hbar.setRange(0, 0)
    hbar.setValue(0)
    viewport_width = browser._view.viewport().width()
    monkeypatch.setattr(
        browser._view,
        "column_views",
        lambda: [_Column(x=-640, width=viewport_width)],
    )

    browser._restore_horizontal_scroll_before_shallow_animation()
    assert hbar.value() == 640
    assert hbar.maximum() == 1000

    browser._settle_columns(reveal=False)

    assert browser._horizontal_scroll_animation is not None
    qtbot.waitUntil(lambda: browser._horizontal_scroll_animation is None, timeout=1000)
    assert hbar.value() == 0
    assert hbar.maximum() == 0


def test_reveal_settle_can_use_pending_hidden_columns(monkeypatch, qtbot, tmp_path: Path) -> None:
    class _Column:
        def __init__(self, *, root: Path, x: int, width: int, visible: bool) -> None:
            self._root = root
            self._x = x
            self._width = width
            self._visible = visible

        def rootIndex(self) -> QModelIndex:  # noqa: N802
            return browser._model.index(str(self._root))

        def isVisible(self) -> bool:  # noqa: N802
            return self._visible

        def x(self) -> int:
            return self._x

        def width(self) -> int:
            return self._width

        def viewport(self):
            return self

        def update(self) -> None:
            return None

        def show(self) -> None:
            return None

    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    selected = tmp_path / "selected"
    stale = tmp_path / "stale"
    selected.mkdir()
    stale.mkdir()
    browser.set_root_path(tmp_path)
    browser._current_path = selected
    browser.resize(420, 300)
    hbar = browser._view.horizontalScrollBar()
    browser._settling = True
    hbar.setRange(0, 0)
    browser._settling = False
    viewport_width = browser._view.viewport().width()
    monkeypatch.setattr(
        browser._view,
        "column_views",
        lambda: [
            _Column(root=tmp_path, x=0, width=viewport_width, visible=True),
            _Column(root=selected, x=viewport_width, width=viewport_width, visible=False),
            _Column(root=stale, x=viewport_width * 4, width=viewport_width, visible=False),
        ],
    )

    browser._settle_columns(reveal=True)

    assert hbar.maximum() == viewport_width
    assert hbar.value() == viewport_width


def test_deep_folder_selection_reveals_new_column(qtbot, tmp_path: Path) -> None:
    current = tmp_path
    chain: list[Path] = []
    for depth in range(5):
        current = current / f"level-{depth}"
        current.mkdir()
        chain.append(current)
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.resize(420, 300)
    browser.show()
    qtbot.waitExposed(browser)
    browser.set_root_path(tmp_path)
    qtbot.waitUntil(lambda: browser._model.index(str(chain[0])).isValid(), timeout=1000)

    previous_max = browser._view.horizontalScrollBar().maximum()
    for path in chain:
        qtbot.waitUntil(lambda path=path: browser._model.index(str(path)).isValid(), timeout=1000)
        browser._view.setCurrentIndex(browser._model.index(str(path)))
        qtbot.waitUntil(
            lambda: browser._view.horizontalScrollBar().value()
            == browser._view.horizontalScrollBar().maximum(),
            timeout=1000,
        )
        hbar = browser._view.horizontalScrollBar()
        assert hbar.maximum() >= previous_max
        assert hbar.value() == hbar.maximum()
        previous_max = hbar.maximum()


def test_set_root_path_keeps_initial_column_left_aligned(qtbot, tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / f"image-{index}.jpg").write_text("image", encoding="utf-8")
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.resize(120, 120)
    browser.show()
    qtbot.waitExposed(browser)

    browser.set_root_path(tmp_path)
    qtbot.wait(20)
    browser.resize(740, 300)
    qtbot.wait(20)

    hbar = browser._view.horizontalScrollBar()
    visible_columns = [
        column
        for column in browser._view.column_views()
        if column.isVisible() and column.rootIndex().isValid()
    ]
    assert hbar.value() == 0
    assert visible_columns
    assert visible_columns[0].x() == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows path normalization semantics")
def test_normalize_directory_key_is_case_and_separator_insensitive() -> None:
    assert normalize_directory_key("C:/Foo/Bar") == normalize_directory_key("C:\\foo\\bar")
    assert normalize_directory_key("/a/b/../b") == normalize_directory_key("/a/b")


def test_model_reports_directories_as_expandable_and_files_as_leaves(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    a_file = tmp_path / "file.txt"
    a_file.write_text("x", encoding="utf-8")
    browser.set_root_path(tmp_path)

    dir_index = browser._model.index(str(empty_dir))
    file_index = browser._model.index(str(a_file))

    assert browser._model.hasChildren(dir_index) is True
    assert browser._model.hasChildren(file_index) is False


def test_columns_are_virtualized_for_large_directories(qtbot, tmp_path: Path) -> None:
    # 数万件のフォルダでフリーズしないよう、列は uniform item sizes と Batched
    # レイアウトで仮想化されている必要がある。
    for index in range(5):
        (tmp_path / f"file-{index}.txt").write_text("x", encoding="utf-8")
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.show()
    qtbot.waitExposed(browser)
    browser.set_root_path(tmp_path)
    qtbot.waitUntil(
        lambda: any(column.rootIndex().isValid() for column in browser._view.column_views()),
        timeout=1000,
    )

    columns = [column for column in browser._view.column_views() if column.rootIndex().isValid()]
    assert columns
    for column in columns:
        assert column.uniformItemSizes() is True
        assert column.layoutMode() == QListView.LayoutMode.Batched


def test_column_model_keeps_standard_icons(qtbot, tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)

    folder_icon = browser._model.data(
        browser._model.index(str(folder)),
        Qt.ItemDataRole.DecorationRole,
    )
    file_icon = browser._model.data(
        browser._model.index(str(file_path)),
        Qt.ItemDataRole.DecorationRole,
    )

    assert isinstance(folder_icon, QIcon)
    assert not folder_icon.isNull()
    assert isinstance(file_icon, QIcon)
    assert not file_icon.isNull()


def test_failed_directory_scan_does_not_retry_forever(qtbot, tmp_path, monkeypatch) -> None:
    # アクセス不能ディレクトリで rowCount→再スキャンの無限ループにならないこと。
    target = tmp_path / "denied"
    target.mkdir()

    def boom(_path):
        raise OSError("Access is denied")

    monkeypatch.setattr(column_browser_model_module.os, "scandir", boom)
    model = _ColumnFileSystemModel()
    index = model.setRootPath(str(target))

    with qtbot.waitSignal(model.directoryLoaded, timeout=2000):
        model.rowCount(index)

    node = model._node_from_index(index)
    assert node is not None
    assert node.error is not None
    assert node.loaded is True
    generation_after_failure = node.scan_generation

    for _ in range(5):
        model.rowCount(index)

    # 失敗後は loaded=True なので再スキャンが走らず、世代も増えない。
    assert node.scan_generation == generation_after_failure
    assert model.directory_error(index) is not None


def test_failed_scan_shows_error_placeholder() -> None:
    assert column_placeholder_text(row_count=0, loaded=True, error="denied") == ERROR_PLACEHOLDER
    # 中身があれば error があってもプレースホルダは出さない。
    assert column_placeholder_text(row_count=3, loaded=True, error="denied") is None


def test_cancelled_old_scan_does_not_remove_new_job_reference(qtbot, tmp_path, monkeypatch) -> None:
    # generation 1 開始 → cancel → generation 2 開始 → generation 1 の finished を遅れて
    # 流しても、新しい job 参照（_jobs / scan_token）が壊れないこと。
    target = tmp_path / "dir"
    target.mkdir()
    model = _ColumnFileSystemModel()
    monkeypatch.setattr(model._scan_pool, "start", lambda _job: None)
    index = model.setRootPath(str(target))
    node = model._node_from_index(index)
    assert node is not None
    key = normalize_directory_key(str(target))

    model._start_scan(node)
    token_a = node.scan_token
    assert token_a in model._jobs

    model._cancel_scan(node)
    model._start_scan(node)
    token_b = node.scan_token
    assert token_b is not token_a
    assert token_b in model._jobs

    # generation 1（token_a）の finished が遅れて到着。
    model._handle_scan_finished((key, 1, token_a, True, 0, time.perf_counter(), None))

    assert token_a not in model._jobs
    assert token_b in model._jobs
    assert node.scan_token is token_b
    assert node.loading is True


def test_index_for_unknown_child_under_loaded_parent_does_not_mutate_children(
    qtbot, tmp_path
) -> None:
    # loaded 親に対して未知 path の index を引いても、通知なしに children が増えないこと。
    model = _ColumnFileSystemModel()
    index = model.setRootPath(str(tmp_path))
    node = model._node_from_index(index)
    assert node is not None
    node.loaded = True
    assert node.children == []

    unknown = tmp_path / "ghost.txt"
    child_index = model.index(str(unknown))

    assert node.children == []
    assert model.filePath(child_index) == str(unknown)


def test_set_resolve_symlinks_controls_scan_policy(qtbot, tmp_path, monkeypatch) -> None:
    target = tmp_path / "dir"
    target.mkdir()

    for resolve in (False, True):
        model = _ColumnFileSystemModel()
        started_jobs: list[object] = []
        monkeypatch.setattr(model._scan_pool, "start", started_jobs.append)
        model.setResolveSymlinks(resolve)
        index = model.setRootPath(str(target))

        model.rowCount(index)

        assert started_jobs
        job = started_jobs[-1]
        assert isinstance(job, _DirectoryScanJob)
        assert job._follow_symlinks is resolve


def test_cancel_scan_try_takes_queued_job(qtbot, tmp_path, monkeypatch) -> None:
    # キャンセル時、まだ起動していない queued job は pool から tryTake で取り除き、
    # _jobs からも消すこと（巨大フォルダの順番待ちが新ルートを塞がないように）。
    target = tmp_path / "dir"
    target.mkdir()
    model = _ColumnFileSystemModel()
    taken: list[object] = []
    monkeypatch.setattr(model._scan_pool, "start", lambda _job: None)
    monkeypatch.setattr(model._scan_pool, "tryTake", lambda job: bool(taken.append(job)) or True)
    index = model.setRootPath(str(target))
    node = model._node_from_index(index)
    assert node is not None
    model._start_scan(node)
    token = node.scan_token
    assert token is not None
    job = model._jobs[token]

    model._cancel_scan(node)

    assert taken == [job]
    assert token not in model._jobs
    assert node.scan_token is None
    assert node.loading is False


def test_cancelled_job_does_not_call_scandir(qtbot, tmp_path, monkeypatch) -> None:
    # 起動前にキャンセル済みの job は os.scandir に入る前に早期終了すること。
    target = tmp_path / "dir"
    target.mkdir()

    def boom(_path):
        raise AssertionError("scandir must not run for a pre-cancelled job")

    monkeypatch.setattr(column_browser_model_module.os, "scandir", boom)
    token = _ScanToken()
    token.cancelled = True
    job = _DirectoryScanJob(
        target, normalize_directory_key(str(target)), 1, token, follow_symlinks=False
    )
    finished: list[object] = []
    job.signals.finished.connect(finished.append)

    job.run()

    assert finished, "cancelled job should still emit finished"


def test_large_directory_loads_all_entries_without_duplicates(qtbot, tmp_path) -> None:
    # 多数のエントリ（複数バッチ）でも、全件が一意な連番 row で読み込まれること。
    # child_keys による O(1) 重複判定が線形探索の O(n²) を置き換えている回帰確認。
    count = 1000
    for index in range(count):
        (tmp_path / f"f{index:05d}.txt").write_text("x", encoding="utf-8")
    model = _ColumnFileSystemModel()
    root_index = model.setRootPath(str(tmp_path))

    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.rowCount(root_index)

    node = model._node_from_index(root_index)
    assert node is not None
    assert model.rowCount(root_index) == count
    assert len(node.children) == count
    assert len(node.child_keys) == count
    assert [child.row for child in node.children] == list(range(count))


def test_duplicate_batch_does_not_insert_duplicate_rows(qtbot, tmp_path, monkeypatch) -> None:
    target = tmp_path / "dir"
    target.mkdir()
    model = _ColumnFileSystemModel()
    monkeypatch.setattr(model._scan_pool, "start", lambda _job: None)
    root_index = model.setRootPath(str(target))
    node = model._node_from_index(root_index)
    assert node is not None
    model._start_scan(node)
    key = normalize_directory_key(str(target))
    entries = [_DirectoryEntry(path=str(target / "a.txt"), name="a.txt", is_dir=False)]

    model._handle_scan_batch((key, node.scan_generation, node.scan_token, entries))
    assert model.rowCount(root_index) == 1

    # 同一バッチを再投入しても重複行が増えないこと。
    model._handle_scan_batch((key, node.scan_generation, node.scan_token, entries))
    assert model.rowCount(root_index) == 1
    assert len(node.child_keys) == 1


def test_parent_refresh_invalidates_direct_child_cache(qtbot, tmp_path, monkeypatch) -> None:
    target = tmp_path / "dir"
    child_path = target / "child"
    child_path.mkdir(parents=True)
    model = _ColumnFileSystemModel()
    monkeypatch.setattr(model._scan_pool, "start", lambda _job: None)
    root_index = model.setRootPath(str(target))
    node = model._node_from_index(root_index)
    assert node is not None
    model._start_scan(node)
    key = normalize_directory_key(str(target))
    child_key = normalize_directory_key(str(child_path))
    model._handle_scan_batch(
        (
            key,
            node.scan_generation,
            node.scan_token,
            [_DirectoryEntry(path=str(child_path), name="child", is_dir=True)],
        )
    )
    child = node.children[0]
    grandchild = _DirectoryNode(child_path / "old.txt", parent=child, is_dir=False)
    child.children.append(grandchild)
    child.child_keys.add(normalize_directory_key(str(grandchild.path)))
    child.loaded = True
    child.error = "stale"

    model.refresh(root_index)

    assert child_key not in node.child_keys
    assert child.loaded is False
    assert child.error is None
    assert child.children == []
    assert child.child_keys == set()


def test_scan_batch_invalidates_reused_child_when_type_changes(
    qtbot, tmp_path, monkeypatch
) -> None:
    target = tmp_path / "dir"
    child_path = target / "child"
    child_path.mkdir(parents=True)
    model = _ColumnFileSystemModel()
    monkeypatch.setattr(model._scan_pool, "start", lambda _job: None)
    root_index = model.setRootPath(str(target))
    node = model._node_from_index(root_index)
    assert node is not None
    child = model._ensure_node(child_path, is_dir=True)
    child.loaded = True
    child.error = "stale"
    grandchild = _DirectoryNode(child_path / "old.txt", parent=child, is_dir=False)
    child.children.append(grandchild)
    child.child_keys.add(normalize_directory_key(str(grandchild.path)))
    model._start_scan(node)
    key = normalize_directory_key(str(target))

    model._handle_scan_batch(
        (
            key,
            node.scan_generation,
            node.scan_token,
            [_DirectoryEntry(path=str(child_path), name="child", is_dir=False)],
        )
    )

    assert node.children == [child]
    assert child.is_dir is False
    assert child.loaded is False
    assert child.error is None
    assert child.children == []
    assert child.child_keys == set()


def test_scan_batch_updates_child_keys_inside_insert_rows(qtbot, tmp_path, monkeypatch) -> None:
    target = tmp_path / "dir"
    target.mkdir()
    model = _ColumnFileSystemModel()
    monkeypatch.setattr(model._scan_pool, "start", lambda _job: None)
    root_index = model.setRootPath(str(target))
    node = model._node_from_index(root_index)
    assert node is not None
    model._start_scan(node)
    key = normalize_directory_key(str(target))
    child_key = normalize_directory_key(str(target / "a.txt"))
    seen_during_insert: list[bool] = []
    original_begin = model.beginInsertRows

    def begin_insert(parent: QModelIndex, first: int, last: int) -> None:
        seen_during_insert.append(child_key in node.child_keys)
        original_begin(parent, first, last)

    monkeypatch.setattr(model, "beginInsertRows", begin_insert)

    model._handle_scan_batch(
        (
            key,
            node.scan_generation,
            node.scan_token,
            [_DirectoryEntry(path=str(target / "a.txt"), name="a.txt", is_dir=False)],
        )
    )

    assert seen_during_insert == [False]
    assert child_key in node.child_keys


def test_model_reports_attached_and_detached_indexes(qtbot, tmp_path) -> None:
    target = tmp_path / "dir"
    child_path = target / "child"
    target.mkdir()
    model = _ColumnFileSystemModel()
    root_index = model.setRootPath(str(target))
    detached_child = model.index(str(child_path))

    assert model._is_attached_index(root_index) is True
    assert model._is_attached_index(detached_child) is False


def test_column_model_sorts_directories_first_with_windows_natural_order() -> None:
    entries = [
        _DirectoryEntry(path="alpha.txt", name="alpha.txt", is_dir=False),
        _DirectoryEntry(path="file10", name="file10", is_dir=True),
        _DirectoryEntry(path="file2", name="file2", is_dir=True),
        _DirectoryEntry(path="_data.txt", name="_data.txt", is_dir=False),
        _DirectoryEntry(path="_data", name="_data", is_dir=True),
        _DirectoryEntry(path="file10.txt", name="file10.txt", is_dir=False),
        _DirectoryEntry(path="file2.txt", name="file2.txt", is_dir=False),
    ]

    assert [entry.name for entry in _sort_entries(entries)] == [
        "_data",
        "file2",
        "file10",
        "_data.txt",
        "alpha.txt",
        "file2.txt",
        "file10.txt",
    ]


def test_column_creation_does_not_force_synchronous_fetch_more(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    child = tmp_path / "child"
    child.mkdir()
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    child_index = browser._model.index(str(child))
    calls: list[QModelIndex] = []
    monkeypatch.setattr(browser._model, "fetchMore", calls.append)

    column = browser._view.createColumn(child_index)

    assert column.rootIndex() == child_index
    assert calls == []


def test_file_selection_does_not_show_empty_preview_column(qtbot, tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    image = folder / "image.jpg"
    image.write_text("image", encoding="utf-8")
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.resize(900, 400)
    browser.show()
    qtbot.waitExposed(browser)
    browser.set_root_path(tmp_path)

    browser._view.setCurrentIndex(browser._model.index(str(folder)))
    qtbot.wait(10)
    browser._view.setCurrentIndex(browser._model.index(str(image)))
    qtbot.wait(10)

    visible_roots = {
        Path(browser._model.filePath(column.rootIndex()))
        for column in browser._view.column_views()
        if column.isVisible() and column.rootIndex().isValid()
    }
    preview = browser._view.previewWidget()
    leaf_artifacts = [
        view
        for view in browser._view.findChildren(column_browser_module.QAbstractItemView)
        if type(view).__name__ != "_ColumnListView"
    ]
    assert image not in visible_roots
    assert preview is None or not preview.isVisible()
    assert all(not view.isVisible() and view.width() == 0 for view in leaf_artifacts)


def test_leaf_preview_artifact_suppression_uses_given_index(qtbot, tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    image = tmp_path / "image.jpg"
    image.write_text("image", encoding="utf-8")
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    artifact = column_browser_module.QListView(browser._view)
    artifact.show()
    artifact.viewport().show()

    browser._view.setCurrentIndex(browser._model.index(str(folder)))
    browser._view.suppress_leaf_preview_artifacts(browser._model.index(str(image)))

    assert artifact.isHidden()
    assert artifact.width() == 0


def test_delayed_leaf_preview_suppression_requires_current_path(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    browser._view.setCurrentIndex(browser._model.index(str(second)))
    calls: list[QModelIndex] = []
    monkeypatch.setattr(browser._view, "suppress_leaf_preview_artifacts", calls.append)

    browser._suppress_leaf_preview_if_current(str(first))

    assert calls == []

    browser._suppress_leaf_preview_if_current(str(second))

    assert len(calls) == 1
    assert normalize_directory_key(browser._model.filePath(calls[0])) == normalize_directory_key(
        str(second)
    )


def test_restore_preview_artifact_constraints_shows_suppressed_view(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    artifact = column_browser_module.QListView(browser._view)
    artifact.setFixedWidth(0)
    artifact.hide()
    artifact.viewport().hide()

    browser._view.restore_preview_artifact_constraints()

    assert artifact.minimumWidth() == 0
    assert artifact.maximumWidth() == 16777215
    assert not artifact.isHidden()
    assert not artifact.viewport().isHidden()


def test_alt_up_shortcut_navigates_to_parent(qtbot, tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(child)

    # Bound on the whole widget (children context) so it also fires while the
    # address bar holds focus, not only when the column view does.
    assert browser._up_shortcut is not None
    assert browser._up_shortcut.key() == QKeySequence("Alt+Up")
    assert browser._up_shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut

    browser._up_shortcut.activated.emit()

    assert browser.current_path() == parent


def test_parented_column_browser_can_keep_local_alt_up_shortcut(qtbot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    browser = ColumnBrowser(parent)

    assert browser._up_shortcut is not None


def test_column_browser_can_disable_local_alt_up_shortcut(qtbot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    browser = ColumnBrowser(parent, enable_local_shortcuts=False)

    assert browser._up_shortcut is None


def test_delete_key_emits_delete_request(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)

    with qtbot.waitSignal(browser._view.deleteRequested, timeout=1000):
        event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Delete,
            Qt.KeyboardModifier.NoModifier,
        )
        browser._view.keyPressEvent(event)
    assert event.isAccepted()


def test_delete_selected_confirms_then_trashes(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    victim = tmp_path / "victim.txt"
    victim.write_text("bye", encoding="utf-8")
    browser.set_root_path(tmp_path)
    monkeypatch.setattr(browser, "_selected_paths", lambda: [victim])
    monkeypatch.setattr(
        column_browser_module.QMessageBox,
        "question",
        lambda *args, **kwargs: column_browser_module.QMessageBox.StandardButton.Yes,
    )
    trashed: list[list[Path]] = []
    monkeypatch.setattr(
        column_browser_operations_module, "delete_paths", lambda paths: trashed.append(paths) or []
    )

    browser._delete_selected()

    assert trashed == [[victim]]


def test_delete_selected_aborts_when_declined(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    victim = tmp_path / "victim.txt"
    victim.write_text("stay", encoding="utf-8")
    browser.set_root_path(tmp_path)
    monkeypatch.setattr(browser, "_selected_paths", lambda: [victim])
    monkeypatch.setattr(
        column_browser_module.QMessageBox,
        "question",
        lambda *args, **kwargs: column_browser_module.QMessageBox.StandardButton.No,
    )
    called: list[object] = []
    monkeypatch.setattr(
        column_browser_operations_module, "delete_paths", lambda paths: called.append(paths) or []
    )

    browser._delete_selected()

    assert called == []


def test_is_directory_loaded_reflects_model_scan_state(qtbot, tmp_path: Path) -> None:
    # loaded 状態の正の状態源は model（node.loaded）のみ。スキャン前は未読み込み、
    # スキャン完了後に読み込み済みになる。
    (tmp_path / "child").mkdir()
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    index = browser._model.index(str(tmp_path))

    assert browser._is_directory_loaded(index) is False
    with qtbot.waitSignal(browser._model.directoryLoaded, timeout=2000):
        browser._model.rowCount(index)
    assert browser._is_directory_loaded(index) is True


def test_refresh_does_not_use_stale_loaded_dirs(qtbot, tmp_path: Path) -> None:
    # 一度 loaded になったディレクトリを refresh すると、再スキャン中は未読み込み扱いに
    # 戻ること（古い loaded 状態が残って「空」と誤表示しないこと）。
    (tmp_path / "child").mkdir()
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    index = browser._model.index(str(tmp_path))
    with qtbot.waitSignal(browser._model.directoryLoaded, timeout=2000):
        browser._model.rowCount(index)
    assert browser._is_directory_loaded(index) is True

    browser._model.refresh(index)

    assert browser._is_directory_loaded(index) is False


def test_paste_destination_resolves_dir_and_file(tmp_path: Path) -> None:
    a_dir = tmp_path / "dir"
    a_dir.mkdir()
    a_file = tmp_path / "file.txt"
    a_file.write_text("x", encoding="utf-8")

    assert paste_destination(a_dir) == a_dir
    assert paste_destination(a_file) == tmp_path


def test_ctrl_keys_emit_clipboard_requests(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    cases = {
        Qt.Key.Key_C: browser._view.copyRequested,
        Qt.Key.Key_X: browser._view.cutRequested,
        Qt.Key.Key_V: browser._view.pasteRequested,
    }
    for key, signal in cases.items():
        with qtbot.waitSignal(signal, timeout=1000):
            event = QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.ControlModifier)
            assert browser._view.handle_shortcut_key(event) is True


def test_paste_falls_back_to_current_path_when_no_column_context(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    source = tmp_path / "src.txt"
    source.write_text("payload", encoding="utf-8")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    browser.set_root_path(tmp_path)
    monkeypatch.setattr(browser, "_selected_paths", lambda: [source])

    browser._copy_selected()
    assert browser._clipboard == {"paths": [source], "mode": "copy"}

    browser._current_path = dest_dir
    browser._paste_into_selection()

    assert (dest_dir / "src.txt").read_text(encoding="utf-8") == "payload"
    assert source.exists()  # copy keeps the original
    assert browser._clipboard is not None  # copy clipboard survives for re-paste


def test_paste_uses_focused_empty_column_root(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    source = tmp_path / "src.txt"
    source.write_text("payload", encoding="utf-8")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    browser.set_root_path(tmp_path)
    monkeypatch.setattr(browser, "_selected_paths", lambda: [source])

    browser._copy_selected()
    browser._view.set_focused_column_root(browser._model.index(str(dest_dir)))
    assert browser._view.paste_directory() == dest_dir
    browser._paste_into_selection()

    assert (dest_dir / "src.txt").read_text(encoding="utf-8") == "payload"
    assert not (tmp_path / "src - Copy 1.txt").exists()


def test_move_paste_keeps_clipboard_when_errors(monkeypatch, qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    source = tmp_path / "src.txt"
    source.write_text("payload", encoding="utf-8")
    browser.set_root_path(tmp_path)
    browser._clipboard = {"paths": [source], "mode": "move"}
    monkeypatch.setattr(
        column_browser_operations_module,
        "perform_copy_or_move",
        lambda _paths, _dest, *, move: ["failed"],
    )
    monkeypatch.setattr(column_browser_module.QMessageBox, "warning", lambda *args: None)

    browser._paste_into_selection()

    assert browser._clipboard == {"paths": [source], "mode": "move"}


def test_cut_then_paste_moves_and_clears_clipboard(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    source = tmp_path / "movable.txt"
    source.write_text("payload", encoding="utf-8")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    browser.set_root_path(tmp_path)

    browser._clipboard = {"paths": [source], "mode": "move"}
    browser._current_path = dest_dir
    browser._paste_into_selection()

    assert (dest_dir / "movable.txt").read_text(encoding="utf-8") == "payload"
    assert not source.exists()  # move removes the original
    assert browser._clipboard is None  # move clipboard is consumed


def test_paste_without_clipboard_is_noop(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)

    browser._paste_into_selection()  # must not raise

    assert list(tmp_path.iterdir()) == []


def test_active_directory_is_parent_of_selection(qtbot, tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)

    browser._view.setCurrentIndex(browser._model.index(str(sub)))

    # The active column is the one holding the selection, i.e. its parent dir.
    assert browser._view.active_directory() == tmp_path


def test_copy_folder_pastes_sibling_instead_of_into_itself(qtbot, tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "inside.txt").write_text("x", encoding="utf-8")
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    browser._view.setCurrentIndex(browser._model.index(str(folder)))

    browser._clipboard = {"paths": [folder], "mode": "copy"}
    browser._paste_into_selection()

    # Windows-style auto-rename into the same (parent) folder, not recursion.
    assert (tmp_path / "folder - Copy 1").is_dir()
    assert not (folder / "folder").exists()


def test_active_column_highlight_follows_selection(qtbot, tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.show()  # columns must be visible for the active-column match
    qtbot.waitExposed(browser)
    browser.set_root_path(tmp_path)

    browser._view.update_active_column(browser._model.index(str(sub)))

    active = [
        column for column in browser._view.column_views() if column.property("activeColumn") is True
    ]
    # Exactly the column that displays the selection's parent is marked active.
    assert len(active) == 1
    assert Path(browser._model.filePath(active[0].rootIndex())) == tmp_path


def test_active_column_survives_root_rebuild(qtbot, tmp_path: Path) -> None:
    first = tmp_path / "first"
    (first / "child").mkdir(parents=True)
    second = tmp_path / "second"
    (second / "child").mkdir(parents=True)
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.show()
    qtbot.waitExposed(browser)

    browser.set_root_path(first)
    browser._view.update_active_column(browser._model.index(str(first / "child")))
    assert browser._view._active_column is not None

    # Navigating to a new root tears the old columns down; the next selection
    # must not touch the now-deleted previous active column.
    browser.set_root_path(second)
    qtbot.wait(10)  # let the deleteLater on the old columns run
    browser._view.update_active_column(browser._model.index(str(second / "child")))


def test_shift_wheel_scrolls_horizontally(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)
    # Hold the settle pass off so it does not collapse this synthetic range
    # (with no real overflow it would otherwise clamp the maximum to 0).
    browser._settling = True
    hbar = browser._view.horizontalScrollBar()
    hbar.setRange(0, 500)
    hbar.setValue(200)

    event = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ShiftModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )

    assert browser._view.handle_shift_wheel(event) is True
    assert hbar.value() == 320  # 200 - (-120)


def test_plain_wheel_is_not_consumed_as_horizontal(qtbot, tmp_path: Path) -> None:
    browser = ColumnBrowser()
    qtbot.addWidget(browser)
    browser.set_root_path(tmp_path)

    event = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )

    assert browser._view.handle_shift_wheel(event) is False
