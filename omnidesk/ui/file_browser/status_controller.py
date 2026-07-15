"""ステータス集計コントローラと互換Mixin。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QItemSelection, QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView

from ..file_browser_status import BrowserStatus, browser_status_from_counts, directory_item_counts
from .selection_restore_controller import SelectionRestoreController


class _DirectoryCountSignals(QObject):
    counted = pyqtSignal(str, int, int, int)


class _DirectoryCountJob(QRunnable):
    def __init__(self, path: Path, generation: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._generation = generation
        self.signals = _DirectoryCountSignals()

    def run(self) -> None:
        folder_count, file_count = directory_item_counts(self._path)
        self.signals.counted.emit(str(self._path), self._generation, folder_count, file_count)


class BrowserStatusController:
    """件数キャッシュ、非同期ジョブ世代、選択ステータスを管理する。"""

    def __init__(
        self,
        *,
        current_path: Callable[[], Path],
        selected_paths: Callable[[], list[Path]],
        active_view: Callable[[], QAbstractItemView],
        tile_view: QAbstractItemView,
        update_action_states: Callable[[], None],
        emit_status: Callable[[BrowserStatus], None],
        selection_restore: SelectionRestoreController,
        pool: QThreadPool,
    ) -> None:
        self._current_path = current_path
        self._selected_paths = selected_paths
        self._active_view = active_view
        self._tile_view = tile_view
        self._update_action_states = update_action_states
        self._emit_status = emit_status
        self._selection_restore = selection_restore
        self.folder_count = 0
        self.file_count = 0
        self.generation = 0
        self.jobs: dict[int, _DirectoryCountJob] = {}
        self.refresh_on_activate = False
        self.pool = pool

    def summary(self) -> BrowserStatus:
        return browser_status_from_counts(
            self.folder_count,
            self.file_count,
            self._selected_paths(),
        )

    def handle_selection_changed(
        self,
        selected: QItemSelection | None = None,
        deselected: QItemSelection | None = None,
    ) -> None:
        self.repaint_selection_delta(selected, deselected)
        self._update_action_states()

    def repaint_selection_delta(
        self,
        selected: QItemSelection | None,
        deselected: QItemSelection | None,
    ) -> None:
        view = self._active_view()
        viewport = view.viewport()
        assert viewport is not None
        if view is self._tile_view:
            viewport.update()
            return
        for selection in (selected, deselected):
            if selection is None:
                continue
            for index in selection.indexes():
                rect = view.visualRect(index.siblingAtColumn(0))
                if rect.isValid():
                    viewport.update(rect)

    def emit_changed(self, selected_paths: list[Path] | None = None) -> None:
        self._emit_status(
            browser_status_from_counts(self.folder_count, self.file_count, selected_paths)
        )

    def request_counts(self, path: Path, callback: Callable[[str, int, int, int], None]) -> None:
        self.refresh_on_activate = False
        self.generation += 1
        generation = self.generation
        job = _DirectoryCountJob(path, generation)
        job.signals.counted.connect(callback)
        self.jobs[generation] = job
        self.pool.start(job)

    def handle_counts_ready(
        self,
        path_text: str,
        generation: int,
        folder_count: int,
        file_count: int,
    ) -> None:
        self.jobs.pop(generation, None)
        path = Path(path_text)
        if generation != self.generation or path != self._current_path():
            return
        self.folder_count = folder_count
        self.file_count = file_count
        self.emit_changed(self._selected_paths())

    def update_counts(self) -> None:
        self.folder_count, self.file_count = directory_item_counts(self._current_path())

    def deactivate(self) -> None:
        """進行中の集計を無効化し、次回アクティブ化時の再取得を予約する。"""
        if self.jobs:
            self.refresh_on_activate = True
        self.generation += 1

    def resume(self, callback: Callable[[str, int, int, int], None]) -> None:
        """必要なら非アクティブ中に無効化した集計を再開する。"""
        if self.refresh_on_activate:
            self.request_counts(self._current_path(), callback)

    def shutdown(self) -> None:
        """終了後に集計結果が現在状態へ反映されないよう無効化する。"""
        self.generation += 1
        self.refresh_on_activate = False
        self.jobs.clear()

    def schedule_selection_restore(self) -> None:
        self._selection_restore.schedule()


class FileBrowserStatusMixin:
    if TYPE_CHECKING:
        _status_controller: BrowserStatusController
        statusChanged: Any

    def status_summary(self) -> BrowserStatus:
        return self._status_controller.summary()

    def _handle_selection_changed(
        self,
        selected: QItemSelection | None = None,
        deselected: QItemSelection | None = None,
    ) -> None:
        self._status_controller.handle_selection_changed(selected, deselected)

    def _repaint_selection_delta(
        self,
        selected: QItemSelection | None,
        deselected: QItemSelection | None,
    ) -> None:
        self._status_controller.repaint_selection_delta(selected, deselected)

    def _emit_status_changed(self, selected_paths: list[Path] | None = None) -> None:
        self._status_controller.emit_changed(selected_paths)

    def _schedule_select_pending_or_first_row(self) -> None:
        self._status_controller.schedule_selection_restore()

    def _request_status_item_counts(self, path: Path) -> None:
        self._status_controller.request_counts(path, self._handle_status_item_counts_ready)

    def _handle_status_item_counts_ready(
        self,
        path_text: str,
        generation: int,
        folder_count: int,
        file_count: int,
    ) -> None:
        self._status_controller.handle_counts_ready(path_text, generation, folder_count, file_count)

    def _update_status_item_counts(self) -> None:
        self._status_controller.update_counts()

    def _deactivate_status_item_counts(self) -> None:
        self._status_controller.deactivate()

    def _resume_status_item_counts(self) -> None:
        self._status_controller.resume(self._handle_status_item_counts_ready)

    def _shutdown_status_item_counts(self) -> None:
        self._status_controller.shutdown()

    @property
    def _status_folder_count(self) -> int:
        return self._status_controller.folder_count

    @_status_folder_count.setter
    def _status_folder_count(self, value: int) -> None:
        self._status_controller.folder_count = value

    @property
    def _status_file_count(self) -> int:
        return self._status_controller.file_count

    @_status_file_count.setter
    def _status_file_count(self, value: int) -> None:
        self._status_controller.file_count = value

    @property
    def _status_count_generation(self) -> int:
        return self._status_controller.generation

    @_status_count_generation.setter
    def _status_count_generation(self, value: int) -> None:
        self._status_controller.generation = value

    @property
    def _status_count_jobs(self) -> dict[int, _DirectoryCountJob]:
        return self._status_controller.jobs

    @_status_count_jobs.setter
    def _status_count_jobs(self, value: dict[int, _DirectoryCountJob]) -> None:
        self._status_controller.jobs = value

    @property
    def _status_count_refresh_on_activate(self) -> bool:
        return self._status_controller.refresh_on_activate

    @_status_count_refresh_on_activate.setter
    def _status_count_refresh_on_activate(self, value: bool) -> None:
        self._status_controller.refresh_on_activate = value

    @property
    def _status_count_pool(self) -> QThreadPool:
        return self._status_controller.pool

    @_status_count_pool.setter
    def _status_count_pool(self, value: QThreadPool) -> None:
        self._status_controller.pool = value
