"""Status summary and directory-count jobs for the file browser tab."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QItemSelection, QObject, QRunnable, pyqtSignal

from ..file_browser_status import (
    BrowserStatus,
    browser_status_from_counts,
    directory_item_counts,
)


class _DirectoryCountSignals(QObject):
    counted = pyqtSignal(str, int, int, int)  # path, generation, folders, files


class _DirectoryCountJob(QRunnable):
    def __init__(self, path: Path, generation: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._generation = generation
        self.signals = _DirectoryCountSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        folder_count, file_count = directory_item_counts(self._path)
        self.signals.counted.emit(
            str(self._path),
            self._generation,
            folder_count,
            file_count,
        )


class FileBrowserStatusMixin:
    def status_summary(self) -> BrowserStatus:
        return browser_status_from_counts(
            self._status_folder_count,
            self._status_file_count,
            self._selected_paths(),
        )

    def _handle_selection_changed(
        self, selected: QItemSelection | None = None, deselected: QItemSelection | None = None
    ) -> None:
        self._repaint_selection_delta(selected, deselected)
        self._update_action_states()

    def _repaint_selection_delta(
        self, selected: QItemSelection | None, deselected: QItemSelection | None
    ) -> None:
        view = self._active_view()
        viewport = view.viewport()
        if view is self._tile_view:
            viewport.update()
            return
        for selection in (selected, deselected):
            if selection is None:
                continue
            for index in selection.indexes():
                source = index.siblingAtColumn(0)
                rect = view.visualRect(source)
                if rect.isValid():
                    viewport.update(rect)

    def _emit_status_changed(self, selected_paths: list[Path] | None = None) -> None:
        self.statusChanged.emit(
            browser_status_from_counts(
                self._status_folder_count,
                self._status_file_count,
                selected_paths,
            )
        )

    def _schedule_select_pending_or_first_row(self) -> None:
        self._selection_restore_timer.stop()
        self._selection_restore_timer.start(0)

    def _request_status_item_counts(self, path: Path) -> None:
        self._status_count_refresh_on_activate = False
        self._status_count_generation += 1
        generation = self._status_count_generation
        job = _DirectoryCountJob(path, generation)
        job.signals.counted.connect(self._handle_status_item_counts_ready)
        self._status_count_jobs[generation] = job
        self._status_count_pool.start(job)

    def _handle_status_item_counts_ready(
        self,
        path_text: str,
        generation: int,
        folder_count: int,
        file_count: int,
    ) -> None:
        self._status_count_jobs.pop(generation, None)
        path = Path(path_text)
        if generation != self._status_count_generation or path != self._current_path:
            return
        self._status_folder_count = folder_count
        self._status_file_count = file_count
        self._emit_status_changed(self._selected_paths())

    def _update_status_item_counts(self) -> None:
        self._status_folder_count, self._status_file_count = directory_item_counts(
            self._current_path
        )
