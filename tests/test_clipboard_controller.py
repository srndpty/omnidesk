from __future__ import annotations

from pathlib import Path

from omnidesk.ui.file_browser.clipboard import ClipboardController


def _controller(mocker, selected_paths=None):
    update_actions = mocker.Mock()
    controller = ClipboardController(
        model=mocker.Mock(),
        tree_view=mocker.Mock(),
        tile_view=mocker.Mock(),
        selected_paths=selected_paths or (lambda: []),
        update_action_states=update_actions,
    )
    return controller, update_actions


def test_set_payload_repaints_old_and_new_paths_and_updates_actions(
    mocker,
    tmp_path: Path,
) -> None:
    previous = tmp_path / "previous.txt"
    current = tmp_path / "current.txt"
    controller, update_actions = _controller(mocker)
    repaint = mocker.patch.object(controller, "repaint_paths")
    controller.set_payload({"paths": [previous], "mode": "copy"})
    repaint.reset_mock()
    update_actions.reset_mock()

    controller.set_payload({"paths": [current], "mode": "move"})

    repaint.assert_called_once_with(
        {controller.normalise_path(previous), controller.normalise_path(current)}
    )
    assert controller.path_set == {controller.normalise_path(current)}
    update_actions.assert_called_once_with()


def test_paths_from_payload_normalizes_and_deduplicates(mocker, tmp_path: Path) -> None:
    controller, _update_actions = _controller(mocker)
    path = tmp_path / "folder" / ".." / "sample.txt"

    assert controller.paths_from_payload({"paths": [path, path], "mode": "copy"}) == {
        controller.normalise_path(path)
    }


def test_copy_and_cut_selected_update_payload(mocker, tmp_path: Path) -> None:
    selected = [tmp_path / "sample.txt"]
    controller, update_actions = _controller(mocker, lambda: selected)
    repaint = mocker.patch.object(controller, "repaint_paths")

    controller.copy_selected()
    assert controller.payload == {"paths": selected, "mode": "copy"}
    controller.cut_selected()
    assert controller.payload == {"paths": selected, "mode": "move"}
    assert repaint.call_count == 2
    assert update_actions.call_count == 2
