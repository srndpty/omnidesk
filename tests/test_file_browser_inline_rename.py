"""Tests for in-place rename editor behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QItemSelectionModel, QRect
from PyQt6.QtWidgets import QAbstractItemView

from omnidesk.ui.file_browser.delegates import (
    _basename_selection_length,
    _InlineRenameLineEdit,
    _InlineRenameTextEdit,
)
from omnidesk.ui.file_browser_tab import FileBrowserTab


@pytest.mark.parametrize(
    ("name", "is_dir", "expected"),
    [
        ("photo.png", False, len("photo")),
        ("archive.tar.gz", False, len("archive.tar")),
        ("README", False, len("README")),
        (".gitignore", False, len(".gitignore")),
        ("My.Folder", True, len("My.Folder")),
        ("plain_folder", True, len("plain_folder")),
    ],
)
def test_basename_selection_length(name: str, is_dir: bool, expected: int) -> None:
    assert _basename_selection_length(name, is_dir) == expected


def test_inline_rename_extends_selection_by_word_to_the_right(qtbot) -> None:
    editor = _InlineRenameLineEdit()
    qtbot.addWidget(editor)
    editor.setText("hello world foo")
    editor.setSelection(0, len("hello"))

    editor._begin_word_drag()
    editor._extend_word_selection(len("hello world fo"))

    assert editor.selectedText() == "hello world foo"


def test_inline_rename_extends_selection_by_word_to_the_left(qtbot) -> None:
    editor = _InlineRenameLineEdit()
    qtbot.addWidget(editor)
    editor.setText("hello world foo")
    start = len("hello ")
    editor.setSelection(start, len("world"))

    editor._begin_word_drag()
    editor._extend_word_selection(0)

    assert editor.selectedText() == "hello world"


def test_inline_rename_drag_without_double_click_does_not_word_select(qtbot) -> None:
    editor = _InlineRenameLineEdit()
    qtbot.addWidget(editor)
    editor.setText("hello world foo")
    editor.setSelection(0, len("hello"))

    # No _begin_word_drag(): a plain drag must not snap to word boundaries.
    assert editor._word_drag is False


def test_text_editor_roundtrip_and_basename_selection(qtbot) -> None:
    editor = _InlineRenameTextEdit()
    qtbot.addWidget(editor)

    editor.set_rename_value("photo.png")
    editor.select_basename(_basename_selection_length("photo.png", is_dir=False))

    assert editor.rename_value() == "photo.png"
    assert editor.textCursor().selectedText() == "photo"


def test_text_editor_overflows_tile_width_for_long_name(qtbot) -> None:
    editor = _InlineRenameTextEdit()
    qtbot.addWidget(editor)
    editor.show()

    tile = QRect(100, 50, 160, 200)
    viewport = QRect(0, 0, 1000, 600)
    editor.set_rename_value(
        "6619776_城ヶ崎 美embedded Jougasaki Mika_02_Voice_extremely_long_name.mp4"
    )
    editor.configure_geometry(tile, viewport, text_top=180)

    geometry = editor.geometry()
    # The box widens past a single tile but stays inside the viewport.
    assert geometry.width() > tile.width()
    assert geometry.left() >= viewport.left()
    assert geometry.right() <= viewport.right()


def test_text_editor_width_is_clamped_to_viewport(qtbot) -> None:
    editor = _InlineRenameTextEdit()
    qtbot.addWidget(editor)
    editor.show()

    tile = QRect(0, 0, 160, 200)
    viewport = QRect(0, 0, 400, 600)
    editor.set_rename_value("x" * 500)
    editor.configure_geometry(tile, viewport, text_top=180)

    assert editor.width() <= viewport.width()


def test_text_editor_height_is_clamped_to_viewport(qtbot) -> None:
    editor = _InlineRenameTextEdit()
    qtbot.addWidget(editor)
    editor.show()

    tile = QRect(0, 0, 120, 100)
    viewport = QRect(0, 0, 120, 150)
    editor.set_rename_value(" ".join(["wordy_segment"] * 40))
    editor.configure_geometry(tile, viewport, text_top=100)

    assert editor.geometry().bottom() <= viewport.bottom()


def test_text_editor_enter_emits_commit(qtbot) -> None:
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent

    editor = _InlineRenameTextEdit()
    qtbot.addWidget(editor)
    editor.set_rename_value("name.txt")

    committed: list[bool] = []
    editor.committed.connect(lambda: committed.append(True))
    editor.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    )

    assert committed == [True]
    # Enter must not insert a newline into the (single-line) filename.
    assert editor.rename_value() == "name.txt"


def test_rename_selected_opens_inline_editor(qtbot, tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    with qtbot.waitSignal(tab._model.directoryLoaded, timeout=5000):
        tab.navigate_to(tmp_path)

    index = tab._model.index(str(tmp_path / "file.txt"))
    assert index.isValid()
    view = tab._active_view()
    view.setCurrentIndex(index)
    view.selectionModel().select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    tab._rename_selected()

    assert view.state() == QAbstractItemView.State.EditingState
