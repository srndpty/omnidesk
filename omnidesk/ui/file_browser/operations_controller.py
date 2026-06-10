"""File-operation orchestration for the file browser tab."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, QThreadPool
from PyQt6.QtWidgets import QAbstractItemView, QInputDialog, QMessageBox

from ..file_browser_drop import has_blocked_self_move
from ..file_browser_navigation import same_navigation_path
from ..file_operation_jobs import FileOperationJob
from ..file_operations import (
    FileOperationRequest,
    FileOperationResult,
    create_file,
    create_folder,
    delete_paths,
    perform_copy_or_move,
    perform_copy_or_move_with_result,
    rename_path,
    resolve_destination,
)

logger = logging.getLogger(__name__)


class FileBrowserOperationsMixin:
    def _rename_selected(self) -> None:
        paths = self._selected_paths()
        if len(paths) != 1:
            return
        original = paths[0]
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=original.name)
        if not ok or not new_name or new_name == original.name:
            return
        target, error = rename_path(original, new_name)
        if error:
            QMessageBox.warning(self, "Rename failed", error)
            return
        if target is None:
            return
        self._mark_changed_directories([original.parent, target.parent])
        self._refresh_and_select(target)

    def _create_new_file(self) -> None:
        if not self._current_path.exists():
            return
        name, ok = QInputDialog.getText(self, "New File", "File name:", text="New File.txt")
        if not ok or not name.strip():
            return
        target, error = create_file(self._current_path, name.strip())
        if error:
            QMessageBox.warning(self, "Create file failed", error)
            return
        if target is None:
            return
        self._mark_directory_changed(self._current_path)
        self._refresh_and_select(target)

    def _create_new_folder(self) -> None:
        if not self._current_path.exists():
            return
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text="New Folder")
        if not ok or not name.strip():
            return
        target, error = create_folder(self._current_path, name.strip())
        if error:
            QMessageBox.warning(self, "Create folder failed", error)
            return
        if target is None:
            return
        self._mark_directory_changed(self._current_path)
        self._refresh_and_select(target, preserve_selection=False)

    def _select_path(
        self,
        path: Path,
        scroll_hint: QAbstractItemView.ScrollHint = QAbstractItemView.ScrollHint.EnsureVisible,
        *,
        defer_settle: bool = True,
    ) -> bool:
        index = self._model.index(str(path))
        if not index.isValid():
            return False
        view = self._active_view()
        selection_model = view.selectionModel()
        if selection_model:
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
        view.scrollTo(index, scroll_hint)
        if defer_settle and scroll_hint == QAbstractItemView.ScrollHint.PositionAtCenter:
            self._defer_settled_scroll(path, scroll_hint)
        return True

    def _refresh_and_select(
        self,
        path: Path,
        *,
        preserve_selection: bool = True,
    ) -> None:
        self._pending_selection_path = path
        self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        old_preserve_selection = self._preserve_selection_on_refresh
        self._preserve_selection_on_refresh = preserve_selection
        try:
            self.refresh()
        finally:
            self._preserve_selection_on_refresh = old_preserve_selection
        self._select_pending_path_if_ready()

    def _select_pending_path_if_ready(self) -> bool:
        pending = self._pending_selection_path
        if pending is None:
            return False
        if not self._select_path(pending):
            return False
        if self._deferred_refresh_target is not None:
            return True
        self._pending_selection_path = None
        self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        return True

    def _paste_into_current(self) -> None:
        if not self._clipboard:
            return
        paths = self._clipboard["paths"]
        if not paths:
            return
        move = self._clipboard["mode"] == "move"
        result = self._perform_copy_or_move_with_result(paths, self._current_path, move=move)
        self._mark_changed_directories(result.changed_dirs)
        if move:
            self._set_clipboard(None)
        else:
            self._update_action_states()
        self.refresh()

    def _delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        select_after_delete = self._selection_path_before_deleted_items(paths)
        if (
            QMessageBox.question(
                self,
                "Move to Trash",
                f"Move {len(paths)} item(s) to Trash?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        errors = delete_paths(paths)
        if errors:
            QMessageBox.warning(self, "Move to Trash failed", "\n".join(errors))
        self._mark_changed_directories([path.parent for path in paths if not path.exists()])
        self._pending_selection_path = select_after_delete
        self.refresh()
        self._update_action_states()

    def _perform_copy_or_move(
        self, sources: list[Path], dest_dir: Path, *, move: bool
    ) -> list[str]:
        errors = perform_copy_or_move(sources, dest_dir, move=move)
        if errors:
            QMessageBox.warning(self, "Operation issues", "\n".join(errors))
        return errors

    def _perform_copy_or_move_with_result(
        self, sources: list[Path], dest_dir: Path, *, move: bool
    ) -> FileOperationResult:
        result = perform_copy_or_move_with_result(sources, dest_dir, move=move)
        if result.errors:
            QMessageBox.warning(self, "Operation issues", "\n".join(result.errors))
        return result

    def _start_file_operation(
        self,
        request: FileOperationRequest,
        *,
        select_after: list[Path] | None = None,
    ) -> FileOperationJob:
        job = FileOperationJob(request)

        def handle_finished(result: object) -> None:
            with suppress(ValueError):
                self._file_operation_jobs.remove(job)
            if not isinstance(result, FileOperationResult):
                return
            if result.cancelled:
                return
            self._handle_file_operation_finished(result, select_after=select_after)

        job.signals.finished.connect(handle_finished)
        self._file_operation_jobs.append(job)
        QThreadPool.globalInstance().start(job)
        return job

    def _handle_file_operation_finished(
        self,
        result: FileOperationResult,
        *,
        select_after: list[Path] | None = None,
    ) -> None:
        if result.cancelled:
            return
        self._mark_changed_directories(result.changed_dirs)
        if result.errors:
            QMessageBox.warning(self, "Operation issues", "\n".join(result.errors))
        if not result.errors and select_after:
            self._pending_selection_path = next(
                (path for path in select_after if path.exists()),
                None,
            )
        self.refresh()
        self._select_pending_path_if_ready()

    def _resolve_destination(self, dest_dir: Path, name: str, move: bool) -> Path:
        return resolve_destination(dest_dir, name, move)

    def _mark_current_directory_changed(self) -> None:
        self._current_directory_has_local_changes = True

    def _mark_directory_changed(self, directory: Path) -> None:
        if same_navigation_path(directory, self._current_path):
            self._mark_current_directory_changed()
            return
        self._model.invalidate_folder_thumbnail_preview(directory)

    def _mark_changed_directories(self, directories: list[Path]) -> None:
        seen: list[Path] = []
        for directory in directories:
            if any(same_navigation_path(directory, known) for known in seen):
                continue
            seen.append(directory)
            self._mark_directory_changed(directory)

    @staticmethod
    def _is_within(path: Path, potential_parent: Path) -> bool:
        try:
            return path.resolve().is_relative_to(potential_parent.resolve())
        except Exception:
            return False

    def _handle_external_drop(
        self,
        paths: list[Path],
        target_dir: Path,
        move: bool,
        *,
        select_after: list[Path] | None = None,
    ) -> bool:
        if not target_dir.exists():
            QMessageBox.warning(self, "Drop failed", f"Destination {target_dir} does not exist.")
            return False
        if move and has_blocked_self_move(paths, target_dir):
            logger.info(
                "Blocked moving a folder into itself: paths=%s target=%s", paths, target_dir
            )
            return False
        result = self._perform_copy_or_move_with_result(paths, target_dir, move=move)
        self._mark_changed_directories(result.changed_dirs)
        if not result.errors and select_after:
            self._pending_selection_path = next(
                (path for path in select_after if path.exists()),
                None,
            )
        self.refresh()
        self._select_pending_path_if_ready()
        return not bool(result.errors)

    def selection_replacement_for_removed_paths(self, paths: list[Path]) -> Path | None:
        return self._selection_path_before_deleted_items(paths)

    def restore_selection_after_removed_paths(
        self,
        removed_paths: list[Path],
        replacement: Path | None,
    ) -> None:
        if replacement is None:
            return
        if not any(
            path.parent == self._current_path and not path.exists() for path in removed_paths
        ):
            return
        self._pending_selection_path = replacement
        self.refresh()
        self._select_pending_path_if_ready()
        self.focus_view()
