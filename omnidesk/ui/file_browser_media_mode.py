"""Media-mode helpers for file browser tabs."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize


def calculate_grid_size(
    edge: int, line_spacing: int, *, text_lines: int = 2, padding: int = 24
) -> QSize:
    """Return a stable tile grid size for icon edge and font metrics."""
    text_height = line_spacing * text_lines
    return QSize(edge + padding, edge + padding + text_height)


def media_mode_button_text(is_media_mode: bool) -> tuple[str, str]:
    """Return button text and tooltip for the current media mode."""
    if is_media_mode:
        return "List View", "Switch to list view (details)"
    return "Tile View", "Switch to tile view (thumbnails)"


def is_media_heavy_directory(
    directory: Path,
    extensions: set[str],
    *,
    ratio_threshold: float,
    min_count: int,
    scan_limit: int,
) -> bool:
    """Return whether a directory has enough media files to prefer tile mode."""
    total_files = 0
    media_files = 0
    try:
        iterator = directory.iterdir()
    except OSError:
        return False
    try:
        for entry in iterator:
            try:
                is_file = entry.is_file()
            except OSError:
                continue
            if is_file:
                total_files += 1
                if entry.suffix.lower() in extensions:
                    media_files += 1
            if total_files >= scan_limit:
                break
    except OSError:
        return False

    if media_files == 0:
        return False
    if total_files <= min_count:
        return True
    if media_files < min_count:
        return False
    return media_files / total_files >= ratio_threshold
