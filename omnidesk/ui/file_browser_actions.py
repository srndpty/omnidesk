"""Pure action-state helpers for the file browser tab."""

from __future__ import annotations


def file_action_states(
    selected_count: int,
    *,
    clipboard_has_paths: bool,
    current_path_exists: bool,
) -> dict[str, bool]:
    """Return enabled states for file browser actions."""
    has_selection = selected_count > 0
    return {
        "copy": has_selection,
        "cut": has_selection,
        "delete": has_selection,
        "rename": selected_count == 1,
        "paste": clipboard_has_paths and current_path_exists,
        "new_file": current_path_exists,
        "new_folder": current_path_exists,
    }
