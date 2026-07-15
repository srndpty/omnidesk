from __future__ import annotations

from pathlib import Path

from omnidesk.ui.file_browser.status_controller import BrowserStatusController


def _controller(mocker, current_path: Path):
    pool = mocker.Mock()
    emit_status = mocker.Mock()
    controller = BrowserStatusController(
        current_path=lambda: current_path,
        selected_paths=lambda: [],
        active_view=mocker.Mock(),
        tile_view=mocker.Mock(),
        update_action_states=mocker.Mock(),
        emit_status=emit_status,
        selection_restore=mocker.Mock(),
        pool=pool,
    )
    return controller, pool, emit_status


def test_stale_generation_result_is_discarded(mocker, tmp_path: Path) -> None:
    controller, _pool, emit_status = _controller(mocker, tmp_path)
    controller.generation = 2

    controller.handle_counts_ready(str(tmp_path), 1, 3, 4)

    assert (controller.folder_count, controller.file_count) == (0, 0)
    emit_status.assert_not_called()


def test_inactive_count_is_refreshed_on_resume(mocker, qtbot, tmp_path: Path) -> None:
    controller, pool, _emit_status = _controller(mocker, tmp_path)
    callback = mocker.Mock()
    controller.request_counts(tmp_path, callback)

    controller.deactivate()
    inactive_generation = controller.generation
    controller.resume(callback)

    assert inactive_generation == 2
    assert controller.generation == 3
    assert controller.refresh_on_activate is False
    assert pool.start.call_count == 2


def test_shutdown_discards_jobs_and_late_results(mocker, qtbot, tmp_path: Path) -> None:
    controller, _pool, emit_status = _controller(mocker, tmp_path)
    controller.request_counts(tmp_path, mocker.Mock())
    running_generation = controller.generation

    controller.shutdown()
    controller.handle_counts_ready(str(tmp_path), running_generation, 3, 4)

    assert controller.jobs == {}
    assert controller.refresh_on_activate is False
    emit_status.assert_not_called()
