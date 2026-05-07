"""Selection helpers for file browser tabs."""

from __future__ import annotations

from pathlib import Path


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
