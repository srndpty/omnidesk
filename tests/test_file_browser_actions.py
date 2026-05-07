from __future__ import annotations

from omnidesk.ui.file_browser_actions import file_action_states


def test_file_action_states_without_selection_or_existing_directory() -> None:
    assert file_action_states(
        0,
        clipboard_has_paths=False,
        current_path_exists=False,
    ) == {
        "copy": False,
        "cut": False,
        "delete": False,
        "rename": False,
        "paste": False,
        "new_file": False,
        "new_folder": False,
    }


def test_file_action_states_single_selection_enables_rename_and_file_actions() -> None:
    assert file_action_states(
        1,
        clipboard_has_paths=True,
        current_path_exists=True,
    ) == {
        "copy": True,
        "cut": True,
        "delete": True,
        "rename": True,
        "paste": True,
        "new_file": True,
        "new_folder": True,
    }


def test_file_action_states_multiple_selection_disables_rename() -> None:
    states = file_action_states(
        2,
        clipboard_has_paths=True,
        current_path_exists=True,
    )

    assert states["copy"]
    assert states["cut"]
    assert states["delete"]
    assert not states["rename"]
