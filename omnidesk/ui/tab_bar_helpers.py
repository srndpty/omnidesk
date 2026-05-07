"""Helpers for tab bar event handling."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl


def wheel_scroll_request(
    angle_x: int,
    angle_y: int,
    pixel_x: int,
    pixel_y: int,
) -> tuple[bool, int] | None:
    """Return tab strip scroll direction and count from wheel deltas."""
    if angle_x != 0 or angle_y != 0:
        delta = angle_x if abs(angle_x) >= abs(angle_y) else angle_y
        steps = int(delta / 120)
        if steps == 0:
            return None
        return delta > 0, abs(steps)

    px = pixel_x if abs(pixel_x) >= abs(pixel_y) else pixel_y
    steps = int(px / 60)
    if steps == 0:
        return None
    return px > 0, abs(steps)


def tab_drop_action(modifiers: Qt.KeyboardModifier) -> Qt.DropAction:
    """Return copy when Ctrl is pressed, otherwise move."""
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        return Qt.DropAction.CopyAction
    return Qt.DropAction.MoveAction


def local_paths_from_urls(urls: list[QUrl]) -> list[Path]:
    """Return local filesystem paths from a URL list."""
    return [Path(url.toLocalFile()) for url in urls if url.isLocalFile()]
