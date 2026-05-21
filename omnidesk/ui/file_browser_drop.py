"""Small helpers for file browser drag-and-drop decisions."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl


def local_paths_from_urls(urls: list[QUrl]) -> list[Path]:
    """Return local filesystem paths from a list of drag URLs."""
    return [Path(url.toLocalFile()) for url in urls if url.isLocalFile()]


def drop_action_for_modifiers(modifiers: Qt.KeyboardModifier) -> Qt.DropAction:
    """Use Ctrl as an explicit copy modifier, otherwise move."""
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        return Qt.DropAction.CopyAction
    return Qt.DropAction.MoveAction


def should_move_from_drop_action(
    drop_action: Qt.DropAction,
    modifiers: Qt.KeyboardModifier,
) -> bool:
    """Return whether a drop should move rather than copy."""
    return (
        drop_action in (Qt.DropAction.MoveAction, Qt.DropAction.TargetMoveAction)
        and not modifiers & Qt.KeyboardModifier.ControlModifier
    )


def drop_target_directory(
    current_path: Path,
    item_path: Path | None,
    *,
    item_is_dir: bool,
) -> Path:
    """Resolve the destination directory for a drop target item."""
    if item_path is None:
        return current_path
    if item_is_dir:
        return item_path
    return item_path.parent


def blocks_self_move(source: Path, target_dir: Path) -> bool:
    """Return whether moving source into target_dir would move it into itself."""
    try:
        src_resolved = source.resolve()
        dest_resolved = target_dir.resolve()
    except Exception:
        return False
    return src_resolved == dest_resolved or dest_resolved.is_relative_to(src_resolved)


def has_blocked_self_move(paths: list[Path], target_dir: Path) -> bool:
    """Return whether any source path would be moved into itself."""
    return any(blocks_self_move(path, target_dir) for path in paths)
