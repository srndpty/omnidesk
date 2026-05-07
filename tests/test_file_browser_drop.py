from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl

from omnidesk.ui.file_browser_drop import (
    blocks_self_move,
    drop_action_for_modifiers,
    drop_target_directory,
    has_blocked_self_move,
    local_paths_from_urls,
    should_move_from_drop_action,
)


def test_local_paths_from_urls_filters_non_local_urls(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    urls = [
        QUrl.fromLocalFile(str(file_path)),
        QUrl("https://example.test/file.txt"),
    ]

    assert local_paths_from_urls(urls) == [file_path]


def test_drop_action_for_modifiers_uses_ctrl_for_copy() -> None:
    assert drop_action_for_modifiers(Qt.KeyboardModifier.NoModifier) == Qt.DropAction.MoveAction
    assert (
        drop_action_for_modifiers(Qt.KeyboardModifier.ControlModifier) == Qt.DropAction.CopyAction
    )


def test_should_move_from_drop_action_respects_ctrl_override() -> None:
    assert should_move_from_drop_action(
        Qt.DropAction.MoveAction,
        Qt.KeyboardModifier.NoModifier,
    )
    assert not should_move_from_drop_action(
        Qt.DropAction.MoveAction,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert not should_move_from_drop_action(
        Qt.DropAction.CopyAction,
        Qt.KeyboardModifier.NoModifier,
    )


def test_drop_target_directory_uses_directory_or_file_parent(tmp_path: Path) -> None:
    current = tmp_path / "current"
    folder = tmp_path / "folder"
    file_path = folder / "file.txt"

    assert drop_target_directory(current, None, item_is_dir=False) == current
    assert drop_target_directory(current, folder, item_is_dir=True) == folder
    assert drop_target_directory(current, file_path, item_is_dir=False) == folder


def test_blocks_self_move_detects_same_or_child_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    other = tmp_path / "other"
    nested.mkdir(parents=True)
    other.mkdir()

    assert blocks_self_move(source, source)
    assert blocks_self_move(source, nested)
    assert not blocks_self_move(source, other)
    assert has_blocked_self_move([other, source], nested)


def test_blocks_self_move_treats_unresolvable_paths_as_not_blocked(
    monkeypatch, tmp_path: Path
) -> None:
    def raise_os_error(self) -> Path:
        raise OSError("unresolvable")

    monkeypatch.setattr(Path, "resolve", raise_os_error)

    assert not blocks_self_move(tmp_path / "source", tmp_path / "target")
