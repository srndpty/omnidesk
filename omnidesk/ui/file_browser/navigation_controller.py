"""Navigation, refresh, selection restore, and view-mode orchestration."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, QModelIndex, QSize, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QKeyEvent
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QMessageBox

from ..file_browser_helpers import deletion_replacement_path
from ..file_browser_media_mode import (
    calculate_grid_size,
    is_media_heavy_directory,
    media_mode_button_text,
)
from ..file_browser_navigation import (
    directory_fingerprint,
    directory_fingerprint_changed,
    navigation_history_step,
    navigation_target,
    path_to_focus_after_go_up,
    same_navigation_path,
    should_record_history,
)
from ..file_browser_selection import has_selection_path_in_directory, pending_selection_action

logger = logging.getLogger(__name__)


class FileBrowserNavigationMixin:
    def navigate_to(self, path: Path, *, from_history: bool = False) -> bool:
        """Display the given directory as the current root."""
        if not path.exists():
            QMessageBox.warning(self, "Cannot navigate", f"{path} does not exist.")
            return False

        current = self._current_path
        target = navigation_target(path)
        target_is_current = self._has_loaded_root and same_navigation_path(current, target)
        should_invalidate_current_preview = (
            self._has_loaded_root
            and not target_is_current
            and (
                self._current_directory_has_local_changes
                or directory_fingerprint_changed(current, self._current_directory_fingerprint)
            )
        )
        if should_invalidate_current_preview:
            self._model.invalidate_folder_thumbnail_preview(current)
        if self._has_loaded_root and should_record_history(
            current, target, from_history=from_history
        ):
            self._navigation_history.append(current)
            self._forward_history.clear()

        self._current_path = target
        if not target_is_current:
            self._current_directory_has_local_changes = False
        if not target_is_current or self._current_directory_fingerprint is None:
            self._current_directory_fingerprint = directory_fingerprint(target)
        self._has_loaded_root = True
        self._path_edit.setText(str(target))

        root_index = self._model.setRootPath(str(target))
        self._tree_view.setRootIndex(root_index)
        self._tile_view.setRootIndex(root_index)
        self._update_media_mode(target, select_default=False)
        self._configure_header_sections()
        self._apply_name_column_width()
        self._connect_selection_signals()
        self.directoryChanged.emit(target)

        deferred_selection = from_history and self._pending_selection_path is not None
        if deferred_selection:
            self._schedule_select_pending_or_first_row()
        else:
            self._select_pending_or_first_row()

        self._restart_thumbnail_requests()  # ナビゲート後にサムネイル要求を再開
        self._update_navigation_button_states()
        logger.debug("Navigated to %s and restarted thumbnail requests", target)
        return True

    def current_path(self) -> Path:
        return self._current_path

    def refresh(self) -> None:
        """Refresh the current directory view."""
        # 明示的な refresh では、以前失敗したサムネイル（一時的にロックされていた
        # ファイルなど）を再試行する。これをしないとモデルの寿命の間ずっと空のままになる。
        self._model.forget_failed_thumbnails()
        selected = self._selected_index_path()
        preserve_selection = self._preserve_selection_on_refresh
        if (
            preserve_selection
            and selected
            and self._pending_selection_path is None
            and has_selection_path_in_directory(selected, self._current_path)
        ):
            self._pending_selection_path = selected
            self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        self._refresh_selection_path = self._pending_selection_path or selected
        self._refresh_sort_active = True
        self._refresh_sort_retries = 10
        target = self._current_path
        if self._reset_root_before_refresh(target):
            self._deferred_refresh_target = target
            self._deferred_refresh_timer.start()
            return
        self._complete_refresh(target)

    def _complete_deferred_refresh(self) -> None:
        target = self._deferred_refresh_target
        self._deferred_refresh_target = None
        if target is None:
            return
        self._complete_refresh(target)

    def _complete_refresh(self, target: Path) -> None:
        if target != self._current_path:
            self._refresh_sort_active = False
            self._refresh_sort_retries = 0
            return
        self.navigate_to(target)
        self._select_pending_path_if_ready()
        self._sort_current_directory(reason="refresh-immediate")
        self._schedule_refresh_sort()

    def _reset_root_before_refresh(self, target: Path) -> bool:
        parent = target.parent
        if parent == target or not parent.exists():
            return False
        self._model.setRootPath(str(parent))
        return True

    def go_back(self) -> None:
        """Navigate to the previous directory in this tab's history."""
        step = navigation_history_step(
            self._navigation_history,
            self._forward_history,
            self._current_path,
            direction="back",
        )
        if step is None:
            return
        previous_path = self._current_path
        old_pending = self._pending_selection_path
        old_scroll_hint = self._pending_selection_scroll_hint
        if has_selection_path_in_directory(previous_path, step.target):
            self._pending_selection_path = previous_path
            self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.PositionAtCenter
        if self.navigate_to(step.target, from_history=True):
            self._navigation_history = step.back_history
            self._forward_history = step.forward_history
            self._update_navigation_button_states()
        else:
            self._pending_selection_path = old_pending
            self._pending_selection_scroll_hint = old_scroll_hint

    def go_forward(self) -> None:
        """Navigate to the next directory in this tab's history."""
        step = navigation_history_step(
            self._navigation_history,
            self._forward_history,
            self._current_path,
            direction="forward",
        )
        if step is None:
            return
        if self.navigate_to(step.target, from_history=True):
            self._navigation_history = step.back_history
            self._forward_history = step.forward_history
            self._update_navigation_button_states()

    def go_up(self) -> None:
        """Navigate to the parent directory."""

        target = path_to_focus_after_go_up(self._current_path)
        if target is None:
            return
        parent, path_to_focus = target
        old_pending = self._pending_selection_path
        old_scroll_hint = self._pending_selection_scroll_hint
        self._pending_selection_path = path_to_focus
        self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.PositionAtCenter
        if not self.navigate_to(parent, from_history=True):
            self._pending_selection_path = old_pending
            self._pending_selection_scroll_hint = old_scroll_hint

    def focus_view(self) -> None:
        self._active_view().setFocus(Qt.FocusReason.OtherFocusReason)

    def set_name_column_width(self, width: int | None) -> None:
        """Apply a new preferred width to the name column."""
        if not width or width <= 0:
            return
        if width == self._name_column_width:
            return
        self._name_column_width = width
        self._apply_name_column_width()

    def name_column_width(self) -> int:
        return self._name_column_width

    # ------------------------------------------------------------------

    def _on_directory_loaded(self, _: str) -> None:
        self._request_status_item_counts(self._current_path)
        self._update_media_mode(self._current_path, select_default=False)

        deferred_selection = self._selection_restore_timer.isActive()
        if not deferred_selection:
            self._select_pending_or_first_row()

        self._configure_header_sections()
        self._apply_name_column_width()

    def _handle_index_activated(self, index: QModelIndex) -> None:
        file_info = self._model.fileInfo(index)
        target = Path(file_info.absoluteFilePath())
        if file_info.isDir():
            self.navigate_to(target)
        else:
            self._open_file(target)

    def _handle_section_resized(self, logical_index: int, _: int, new_size: int) -> None:
        if self._media_icon_mode:
            return
        if logical_index != 0:
            return
        if new_size <= 0 or new_size == self._name_column_width:
            return
        self._name_column_width = new_size
        self.nameColumnWidthChanged.emit(new_size)

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        logger.debug("keyPressEvent key=%s modifiers=%s", event.key(), event.modifiers())
        # Ctrl+Enter で選択中のフォルダを新しいタブで開く
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            logger.debug("Ctrl+Enter detected")
            selected = self._selected_index_path()
            if selected and selected.is_dir():
                self.requestOpenInNewTab.emit(selected)
                return
        super().keyPressEvent(event)

    def _active_view(self) -> QAbstractItemView:
        if self._media_icon_mode:
            return self._tile_view
        return self._tree_view

    def _apply_name_column_width(self) -> None:
        if self._media_icon_mode:
            return
        if self._name_column_width > 0:
            self._header.resizeSection(0, self._name_column_width)

    def _sort_current_directory(self, *, reason: str) -> None:
        column = self._header.sortIndicatorSection()
        if column < 0:
            column = 0
        order = self._header.sortIndicatorOrder()
        _ = reason
        self._model.sort(column, order)
        self._tile_view.scheduleDelayedItemsLayout()

    def _schedule_refresh_sort(self) -> None:
        if not self._refresh_sort_active or self._refresh_sort_retries <= 0:
            return
        if not self._refresh_sort_timer.isActive():
            self._refresh_sort_timer.start()

    def _apply_refresh_sort(self) -> None:
        if not self._refresh_sort_active or self._refresh_sort_retries <= 0:
            self._refresh_sort_active = False
            return
        self._refresh_sort_retries -= 1
        self._sort_current_directory(reason="refresh-deferred")
        if (
            self._refresh_selection_path
            and self._refresh_selection_path.exists()
            and self._can_restore_refresh_selection(self._refresh_selection_path)
            and self._select_path(self._refresh_selection_path)
        ):
            self._refresh_selection_path = None
            self._refresh_sort_retries = 0
            self._refresh_sort_active = False
            return
        if self._refresh_sort_retries <= 0:
            self._refresh_sort_active = False
        self._schedule_refresh_sort()

    def _can_restore_refresh_selection(self, path: Path) -> bool:
        current = self._selected_index_path()
        if current is None:
            return True
        if same_navigation_path(current, path):
            return True
        self._refresh_selection_path = None
        self._refresh_sort_retries = 0
        self._refresh_sort_active = False
        return False

    def _defer_settled_scroll(
        self,
        path: Path,
        scroll_hint: QAbstractItemView.ScrollHint,
    ) -> None:
        self._settled_scroll_path = path
        self._settled_scroll_hint = scroll_hint
        self._settled_scroll_retries = 8
        self._schedule_settled_scroll()

    def _schedule_settled_scroll(self) -> None:
        if self._settled_scroll_path is None or self._settled_scroll_retries <= 0:
            return
        if not self._settled_scroll_timer.isActive():
            self._settled_scroll_timer.start()

    def _apply_settled_scroll(self) -> None:
        path = self._settled_scroll_path
        if path is None or self._settled_scroll_retries <= 0:
            return
        self._settled_scroll_retries -= 1
        if not path.exists():
            self._settled_scroll_path = None
            return
        if not self._can_apply_settled_scroll(path):
            return
        self._select_path(path, self._settled_scroll_hint, defer_settle=False)
        self._schedule_settled_scroll()

    def _can_apply_settled_scroll(self, path: Path) -> bool:
        current = self._selected_index_path()
        if current is None:
            return True
        if same_navigation_path(current, path):
            return True
        self._settled_scroll_path = None
        self._settled_scroll_retries = 0
        return False

    def _configure_header_sections(self) -> None:
        if self._media_icon_mode:
            return
        count = self._header.count()
        if count == 0:
            return
        self._header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for section in range(1, count):
            self._header.setSectionResizeMode(section, QHeaderView.ResizeMode.ResizeToContents)

    def _selected_index_path(self) -> Path | None:
        selection_model = self._active_view().selectionModel()
        if not selection_model:
            return None
        index = selection_model.currentIndex()
        if not index.isValid():
            return None
        file_info = self._model.fileInfo(index)
        return Path(file_info.absoluteFilePath())

    def _select_first_row(self) -> None:
        view = self._active_view()
        model = view.model()
        if not model:
            return
        selection_model = view.selectionModel()
        root_index = view.rootIndex()
        if selection_model and root_index.isValid():
            first_index = model.index(0, 0, root_index)
            if first_index.isValid():
                selection_model.setCurrentIndex(
                    first_index,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )

    def _select_pending_or_first_row(self) -> None:
        pending = self._pending_selection_path
        pending_scroll_hint = self._pending_selection_scroll_hint
        pending_exists = bool(pending and pending.exists())
        selected_pending = bool(
            pending and pending_exists and self._select_path(pending, pending_scroll_hint)
        )
        action = pending_selection_action(
            pending,
            pending_exists=pending_exists,
            selected_in_current_directory=self._has_current_selection_in_current_directory(),
            pending_select_succeeded=selected_pending,
        )
        if action == "selected_pending":
            self._pending_selection_path = None
            self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
            return
        if action == "wait_for_pending":
            return
        if action == "keep_current":
            return
        self._pending_selection_path = None
        self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        self._select_first_row()

    def _has_current_selection_in_current_directory(self) -> bool:
        selection_model = self._active_view().selectionModel()
        if not selection_model:
            return False
        index = selection_model.currentIndex()
        if not index.isValid():
            return False
        return has_selection_path_in_directory(
            Path(self._model.filePath(index)), self._current_path
        )

    def _selection_path_before_deleted_items(self, deleted_paths: list[Path]) -> Path | None:
        view = self._active_view()
        model = view.model()
        selection_model = view.selectionModel()
        if not model or not selection_model:
            return None

        root_index = view.rootIndex()
        selected_rows = {
            index.siblingAtColumn(0).row()
            for index in selection_model.selectedRows() or selection_model.selectedIndexes()
            if index.isValid()
        }
        if not selected_rows:
            return None

        ordered_paths: list[Path] = []
        row_count = model.rowCount(root_index)
        for row in range(row_count):
            index = model.index(row, 0, root_index)
            if index.isValid():
                ordered_paths.append(Path(self._model.filePath(index)))

        deleted = {path.resolve() for path in deleted_paths}
        return deletion_replacement_path(ordered_paths, selected_rows, deleted)

    def _connect_selection_signals(self) -> None:
        view = self._active_view()
        selection_model = view.selectionModel()
        if not selection_model:
            return
        if self._bound_selection_model is selection_model:
            return
        if self._bound_selection_model is not None:
            with suppress(TypeError):
                self._bound_selection_model.currentChanged.disconnect(self._handle_current_changed)
            with suppress(TypeError):
                self._bound_selection_model.selectionChanged.disconnect(
                    self._handle_selection_changed
                )
        self._bound_selection_model = selection_model
        selection_model.currentChanged.connect(self._handle_current_changed)
        selection_model.selectionChanged.connect(self._handle_selection_changed)
        self._update_action_states()

    def _handle_current_changed(self, current: QModelIndex, _: QModelIndex) -> None:
        file_info = self._model.fileInfo(current)
        if file_info.isDir():
            self.directoryChanged.emit(Path(file_info.absoluteFilePath()))

    def _open_file(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------------
    def _update_media_mode(self, directory: Path, *, select_default: bool = True) -> None:
        # should_enable = self._is_media_heavy(directory)
        should_enable = True  # 常にサムネイルモードにする
        if should_enable != self._media_icon_mode:
            self._media_icon_mode = should_enable
            self._apply_media_mode(select_default=select_default)
        elif self._media_icon_mode:
            self._apply_media_mode(select_default=select_default)

    def _apply_media_mode(self, *, select_default: bool = True) -> None:
        if self._media_icon_mode:
            icon_edge = 160
            self._model.set_thumbnail_edge(icon_edge)
            self._tile_view.setIconSize(QSize(icon_edge, icon_edge))
            self._tile_view.setGridSize(self._calculate_grid_size(icon_edge))
            self._view_stack.setCurrentWidget(self._tile_view)
        else:
            self._model.set_thumbnail_edge(96)
            self._tree_view.setIconSize(QSize(32, 32))
            self._view_stack.setCurrentWidget(self._tree_view)
        self._update_view_toggle_button()
        self._connect_selection_signals()
        if select_default:
            self._select_pending_or_first_row()

    def _handle_view_toggle_clicked(self) -> None:
        target = not self._media_icon_mode
        self._manual_media_mode = target
        self._media_icon_mode = target
        self._apply_media_mode()

    def _update_view_toggle_button(self) -> None:
        text, tooltip = media_mode_button_text(self._media_icon_mode)
        self._toggle_view_button.setText(text)
        self._toggle_view_button.setToolTip(tooltip)

    def _calculate_grid_size(self, edge: int) -> QSize:
        fm = self._tile_view.fontMetrics()
        return calculate_grid_size(edge, fm.lineSpacing())

    def _is_media_heavy(self, directory: Path) -> bool:
        return is_media_heavy_directory(
            directory,
            self._model.media_extensions,
            ratio_threshold=self.MEDIA_RATIO_THRESHOLD,
            min_count=self.MEDIA_MIN_COUNT,
            scan_limit=self.MEDIA_SCAN_LIMIT,
        )
