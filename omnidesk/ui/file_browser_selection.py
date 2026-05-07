"""Selection helpers for file browser tabs."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRect


def rubber_band_intersecting_rows(
    selection_rect: QRect,
    viewport_rect: QRect,
    row_rects: list[tuple[int, QRect]],
) -> set[int]:
    """Return row numbers whose visible row rect intersects the rubber band."""
    selected_rows: set[int] = set()
    for row, row_rect in row_rects:
        if row_rect.intersects(viewport_rect) and selection_rect.intersects(row_rect):
            selected_rows.add(row)
    return selected_rows


def rubber_band_target_rows(
    current_indexes: set[tuple[int, int]],
    previous_indexes: set[tuple[int, int]],
    *,
    control_pressed: bool,
) -> set[int]:
    """Return final row selection for rubber-band drag state."""
    if control_pressed:
        return {row for row, _column in previous_indexes.symmetric_difference(current_indexes)}
    return {row for row, _column in current_indexes}


def pending_selection_action(
    pending_path: Path | None,
    *,
    pending_exists: bool,
    selected_in_current_directory: bool,
    pending_select_succeeded: bool,
) -> str:
    """
    Decide how pending selection state should be handled.

    Returns one of:
    - "selected_pending": pending was selected and should be cleared.
    - "wait_for_pending": pending exists but is not selectable yet.
    - "select_first": no usable selection exists.
    - "keep_current": current selection is still valid.
    """
    if pending_path is not None:
        if pending_exists and pending_select_succeeded:
            return "selected_pending"
        if pending_exists:
            return "wait_for_pending"
        return "select_first"
    if selected_in_current_directory:
        return "keep_current"
    return "select_first"


def has_selection_path_in_directory(path: Path, directory: Path) -> bool:
    """Return whether path exists and is directly inside directory."""
    try:
        return path.exists() and path.parent.resolve() == directory.resolve()
    except Exception:
        return False
