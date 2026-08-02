"""File-operation orchestration for the file browser tab."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QAbstractItemView, QInputDialog, QMessageBox, QWidget

from ..file_browser_drop import has_blocked_self_move
from ..file_browser_navigation import same_navigation_path
from ..file_operation_jobs import FileOperationJob, FileOperationSignals
from ..file_operations import (
    FileOperationRequest,
    FileOperationResult,
    clip_child_name,
    create_file,
    create_folder,
    is_plain_child_name,
    name_exceeds_limits,
    rename_path,
    resolve_destination,
)
from ..qt_lifetime import is_alive
from .clipboard import _ClipboardPayload
from .selection_restore_controller import SelectionRestoreController
from .sort_model import SortedFileSystemModel

logger = logging.getLogger(__name__)


def _ellipsize_for_dialog(text: str, limit: int = 300) -> str:
    """Middle-ellipsize text so a pasted multi-thousand-char name cannot blow
    up the confirmation dialog. Only affects display, not the applied name."""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return f"{text[:head]}\n…\n{text[-tail:]}"


_OperationsMixinBase = QWidget if TYPE_CHECKING else object


class FileBrowserOperationsMixin(_OperationsMixinBase):
    if TYPE_CHECKING:
        # FileBrowserTab本体や他Mixinが用意する属性/メソッドの型宣言。
        _clipboard: _ClipboardPayload | None
        _current_directory_has_local_changes: bool
        _current_path: Path
        _delete_confirmation_open: bool
        _deferred_refresh_target: Path | None
        _file_operation_completions: dict[
            int,
            tuple[list[Path] | None, str, Callable[[FileOperationResult], None] | None],
        ]
        _file_operation_jobs: list[FileOperationJob]
        _file_operation_job_seq: int
        _file_operation_signals: FileOperationSignals
        _inline_rename_seed: tuple[Path, str | None] | None
        _model: SortedFileSystemModel
        _pending_selection_path: Path | None
        _pending_selection_scroll_hint: QAbstractItemView.ScrollHint
        _preserve_selection_on_refresh: bool
        _selection_restore_controller: SelectionRestoreController

        def _active_view(self) -> QAbstractItemView: ...

        def _selected_paths(self) -> list[Path]: ...

        def _selection_path_before_deleted_items(self, paths: list[Path]) -> Path | None: ...

        def _set_clipboard(self, payload: _ClipboardPayload | None) -> None: ...

        def _update_action_states(self) -> None: ...

        def focus_view(self) -> None: ...

        def refresh(self) -> None: ...

    def _rename_selected(self) -> None:
        paths = self._selected_paths()
        if len(paths) != 1:
            return
        self._begin_inline_edit(paths[0])

    def _begin_inline_edit(self, path: Path, *, seed_text: str | None = None) -> bool:
        """Open the in-place rename editor on ``path``.

        ``seed_text`` pre-fills the editor (used when returning to edit state
        after the user declined a name that had to be shortened).
        """
        view = self._active_view()
        selection_model = view.selectionModel()
        index = view.currentIndex()
        if not index.isValid() or selection_model is None or not selection_model.isSelected(index):
            index = self._model.index(str(path))
        if not index.isValid():
            return False
        index = index.siblingAtColumn(0)
        view.setCurrentIndex(index)
        view.scrollTo(index)
        self._inline_rename_seed = (path, seed_text) if seed_text is not None else None
        opened = view.edit(index)
        if not opened:
            # Editor never opened, so the seed would otherwise leak into a later
            # unrelated rename of the same path.
            self._inline_rename_seed = None
        return bool(opened)

    def _consume_rename_seed(self, path: Path) -> str | None:
        """Return any pending editor seed text for ``path``.

        The seed is one-shot: it is cleared on every consume attempt so a stale
        value can never resurface in a later, unrelated rename.
        """
        seed = getattr(self, "_inline_rename_seed", None)
        if seed is None:
            return None
        self._inline_rename_seed = None
        seed_path, text = seed
        return text if seed_path == path else None

    def _apply_rename(self, original: Path, new_name: str) -> None:
        """Rename ``original`` to ``new_name`` and refresh the selection.

        Shared by the in-place rename editor; keeps the conflict reporting and
        directory-change bookkeeping in one place.
        """
        if not new_name or new_name == original.name:
            return
        # Validate the raw input before clipping: a too-long name that also
        # contains a path separator (e.g. a mis-pasted path) must be rejected as
        # such, not silently turned into a different name by the clip. Skipping
        # the clip lets rename_path() report the separator error.
        if is_plain_child_name(new_name) and name_exceeds_limits(original.parent, new_name):
            clipped = clip_child_name(
                original.parent, new_name, keep_extension=not original.is_dir()
            )
            if not self._confirm_name_clip(new_name, clipped):
                # User declined the shortened name: reopen the editor with their
                # text so they can adjust it instead of losing the rename.
                self._begin_inline_edit(original, seed_text=new_name)
                return
            logger.info("Clipped rename target from %r to %r", new_name, clipped)
            new_name = clipped
        target, error = rename_path(original, new_name)
        if error:
            QMessageBox.warning(self, "Rename failed", error)
            return
        if target is None:
            return
        self._mark_changed_directories([original.parent, target.parent])
        self._refresh_and_select(target)

    def _confirm_name_clip(self, requested: str, clipped: str) -> bool:
        """Ask whether to rename using the shortened name. True == proceed."""
        answer = QMessageBox.question(
            self,
            "Name too long",
            "The name is too long for the filesystem and will be shortened.\n\n"
            f"Entered:\n{_ellipsize_for_dialog(requested)}\n\n"
            f"Shortened:\n{_ellipsize_for_dialog(clipped)}\n\n"
            "Rename using the shortened name?",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        return answer == QMessageBox.StandardButton.Ok

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
        return self._selection_restore_controller.select_path(
            path,
            scroll_hint,
            defer_settle=defer_settle,
        )

    def _refresh_and_select(
        self,
        path: Path,
        *,
        preserve_selection: bool = True,
    ) -> None:
        self._selection_restore_controller.refresh_and_select(
            path,
            preserve_selection=preserve_selection,
        )

    def _refresh_for_selection_restore(self, preserve_selection: bool) -> None:
        """選択復元中だけrefreshの選択保持方針を差し替える。"""
        old_preserve_selection = self._preserve_selection_on_refresh
        self._preserve_selection_on_refresh = preserve_selection
        try:
            self.refresh()
        finally:
            self._preserve_selection_on_refresh = old_preserve_selection

    def _select_pending_path_if_ready(self) -> bool:
        return self._selection_restore_controller.select_pending_path_if_ready()

    def _paste_into_current(self) -> None:
        if not self._clipboard:
            return
        paths = self._clipboard["paths"]
        if not paths:
            return
        move = self._clipboard["mode"] == "move"
        if move:
            # 非同期化により、完了前にもう一度貼り付けられる余地ができた。
            # 同じ移動を二重に投入しないよう、切り取りのクリップボードは
            # ジョブ投入と同時に空にする。
            self._set_clipboard(None)
        else:
            self._update_action_states()
        self._start_copy_or_move(paths, self._current_path, move=move)

    def _delete_selected(self) -> None:
        if self._delete_confirmation_open:
            logger.warning("表示中の削除確認に対する重複要求を無視しました")
            return
        paths = self._selected_paths()
        if not paths:
            return
        select_after_delete = self._selection_path_before_deleted_items(paths)
        logger.info("削除確認を表示します: count=%d", len(paths))
        self._delete_confirmation_open = True
        try:
            answer = QMessageBox.question(
                self,
                "Move to Trash",
                f"Move {len(paths)} item(s) to Trash?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
        finally:
            self._delete_confirmation_open = False
        logger.info(
            "削除確認が閉じられました: accepted=%s", answer == QMessageBox.StandardButton.Yes
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_file_operation(
            FileOperationRequest(paths, None, "delete"),
            select_after=[select_after_delete] if select_after_delete is not None else None,
            error_title="Move to Trash failed",
        )

    def _start_copy_or_move(
        self,
        sources: list[Path],
        dest_dir: Path,
        *,
        move: bool,
        select_after: list[Path] | None = None,
        on_finished: Callable[[FileOperationResult], None] | None = None,
    ) -> FileOperationJob:
        """コピー／移動をワーカースレッドで開始する。

        以前はGUIスレッドで ``shutil`` を直接呼んでいたため、大きなファイルの
        ドラッグ&ドロップや貼り付けでウィンドウが完全に無応答になっていた。
        削除と同じジョブ経路へ載せ、完了通知でUIを更新する。
        """
        return self._start_file_operation(
            FileOperationRequest(list(sources), dest_dir, "move" if move else "copy"),
            select_after=select_after,
            on_finished=on_finished,
        )

    def _start_file_operation(
        self,
        request: FileOperationRequest,
        *,
        select_after: list[Path] | None = None,
        error_title: str = "Operation issues",
        on_finished: Callable[[FileOperationResult], None] | None = None,
    ) -> FileOperationJob:
        self._file_operation_job_seq += 1
        job_id = self._file_operation_job_seq
        job = FileOperationJob(request, self._file_operation_signals, job_id)
        self._file_operation_completions[job_id] = (select_after, error_title, on_finished)
        self._file_operation_jobs.append(job)
        pool = QThreadPool.globalInstance()
        assert pool is not None
        pool.start(job)
        return job

    def _handle_file_operation_job_finished(self, job_id: int, result: object) -> None:
        """共有シグナルから届いた完了通知を、対応するジョブへ振り分ける。

        ジョブ実行中にタブが閉じられていると、ここへ届く頃にはC++側の
        ウィジェットが破棄されている。触れると ``RuntimeError`` になるため、
        生存を確認してから状態を更新する。
        """
        if not is_alive(self):
            return
        completion = self._file_operation_completions.pop(job_id, None)
        self._file_operation_jobs = [
            job for job in self._file_operation_jobs if job.job_id != job_id
        ]
        if completion is None:
            return
        if not isinstance(result, FileOperationResult) or result.cancelled:
            return
        select_after, error_title, on_finished = completion
        self._handle_file_operation_finished(
            result,
            select_after=select_after,
            error_title=error_title,
        )
        if on_finished is not None:
            on_finished(result)

    def _handle_file_operation_finished(
        self,
        result: FileOperationResult,
        *,
        select_after: list[Path] | None = None,
        error_title: str = "Operation issues",
    ) -> None:
        if result.cancelled:
            return
        self._mark_changed_directories(result.changed_dirs)
        if result.errors:
            QMessageBox.warning(self, error_title, "\n".join(result.errors))
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
            logger.debug(
                "包含判定のパス解決に失敗しました: path=%s potential_parent=%s",
                path,
                potential_parent,
                exc_info=True,
            )
            return False

    def _handle_external_drop(
        self,
        paths: list[Path],
        target_dir: Path,
        move: bool,
        *,
        select_after: list[Path] | None = None,
        on_finished: Callable[[FileOperationResult], None] | None = None,
    ) -> bool:
        """ドロップされたパスのコピー／移動を開始する。

        転送はワーカースレッドで行うため、戻り値は「ジョブを開始できたか」で
        あって成否ではない。完了後の処理が必要な場合は ``on_finished`` を使う。
        """
        if not target_dir.exists():
            QMessageBox.warning(self, "Drop failed", f"Destination {target_dir} does not exist.")
            return False
        if move and has_blocked_self_move(paths, target_dir):
            logger.info(
                "Blocked moving a folder into itself: paths=%s target=%s", paths, target_dir
            )
            return False
        self._start_copy_or_move(
            paths,
            target_dir,
            move=move,
            select_after=select_after,
            on_finished=on_finished,
        )
        return True

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
