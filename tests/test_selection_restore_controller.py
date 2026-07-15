"""選択復元コントローラの状態遷移テスト。"""

from pathlib import Path

from PyQt6.QtCore import QObject

from omnidesk.ui.file_browser.selection_restore_controller import SelectionRestoreController


def _controller(mocker, parent: QObject, **overrides) -> SelectionRestoreController:
    defaults = {
        "model": mocker.Mock(),
        "active_view": mocker.Mock(),
        "apply_selection": mocker.Mock(return_value=False),
        "defer_scroll": mocker.Mock(),
        "has_current_selection": mocker.Mock(return_value=False),
        "select_first_row": mocker.Mock(),
        "has_deferred_refresh": mocker.Mock(return_value=False),
        "refresh": mocker.Mock(),
    }
    defaults.update(overrides)
    return SelectionRestoreController(parent, **defaults)


def test_refresh_and_select_keeps_pending_path_until_model_is_ready(
    mocker,
    qtbot,
    tmp_path: Path,
) -> None:
    target = tmp_path / "created.txt"
    target.write_text("created", encoding="utf-8")
    refresh = mocker.Mock()
    parent = QObject()
    controller = _controller(mocker, parent, refresh=refresh)

    controller.refresh_and_select(target, preserve_selection=False)

    refresh.assert_called_once_with(False)
    assert controller.pending_path == target


def test_select_pending_path_clears_state_after_success(mocker, qtbot, tmp_path: Path) -> None:
    target = tmp_path / "created.txt"
    target.write_text("created", encoding="utf-8")
    apply_selection = mocker.Mock(return_value=True)
    parent = QObject()
    controller = _controller(mocker, parent, apply_selection=apply_selection)
    controller.pending_path = target

    assert controller.select_pending_path_if_ready()

    apply_selection.assert_called_once_with(target, None)
    assert controller.pending_path is None
