"""Thumbnail scheduling and visible-index helpers for the file browser tab."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

import logging

from PyQt6.QtCore import QModelIndex, QPoint
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QListView, QTreeView

from ..file_browser_visible import index_identity, tile_probe_points, tile_probe_step

logger = logging.getLogger(__name__)


class FileBrowserThumbnailMixin:
    def activate(self) -> None:
        """Start visible-item thumbnail work when this tab becomes active."""
        if self._is_active:
            return
        logger.debug("Activating tab for %s", self._current_path)
        self._is_active = True
        self._restart_thumbnail_requests()
        if self._status_count_refresh_on_activate:
            self._request_status_item_counts(self._current_path)

    def deactivate(self) -> None:
        """Stop visible-item thumbnail work when this tab becomes inactive."""
        if not self._is_active:
            return
        logger.debug("Deactivating tab for %s", self._current_path)
        self._is_active = False
        self.cancel_inactive_tab_work()

    def cancel_inactive_tab_work(self) -> None:
        """Cancel work that is only useful while this tab is visible."""
        self._thumbnail_scheduler.cancel()
        self._model.cancel_background_work()
        if self._status_count_jobs:
            self._status_count_refresh_on_activate = True
        self._status_count_generation += 1

    def cancel_all_work_for_shutdown(self) -> None:
        """Cancel all work owned by this tab during shutdown or disposal."""
        self.cancel_inactive_tab_work()
        self._selection_restore_timer.stop()
        self._settled_scroll_timer.stop()
        self._refresh_sort_timer.stop()
        self._deferred_refresh_timer.stop()
        self._deferred_refresh_target = None
        for job in self._file_operation_jobs:
            job.cancel()
        self._file_operation_jobs.clear()

    def cancel_background_work(self) -> None:
        """Deprecated: shutdown-only cancellation. Do not use for tab deactivation."""
        self.cancel_all_work_for_shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        self.cancel_all_work_for_shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        """ウィンドウサイズが変更されたときに呼び出される"""
        # 親クラスの元のリサイズ処理を必ず呼び出す
        # スクロール時と同じタイマーを開始し、可視範囲のサムネイル要求をスケジュールする
        if self._is_active:  # アクティブなタブだけがリサイズに応答
            self._restart_thumbnail_requests()
            logger.debug("Resize event restarted thumbnail requests")
            return
        super().resizeEvent(event)

    def _on_layout_changed(self) -> None:
        """Restart visible thumbnail requests after model layout changes."""
        self._schedule_settled_scroll()
        self._schedule_refresh_sort()
        self._restart_thumbnail_requests()

    def _on_rows_inserted(self, parent: QModelIndex, first: int, last: int) -> None:
        _ = (parent, first, last)
        self._schedule_settled_scroll()
        self._schedule_refresh_sort()

    def _on_scroll(self) -> None:
        if not hasattr(self, "_thumbnail_scheduler"):
            return
        self._thumbnail_scheduler.handle_scroll()

    def _restart_thumbnail_requests(self) -> None:
        """Manually trigger a re-evaluation of visible items for thumbnail requests."""
        if not hasattr(self, "_thumbnail_scheduler"):
            return
        self._thumbnail_scheduler.restart()

    def _request_settled_thumbnails(self) -> None:
        if not hasattr(self, "_thumbnail_scheduler"):
            return
        self._set_thumbnail_scrolling(False)
        self._request_visible_thumbnails(scrolling=False)

    def _request_visible_thumbnails(self, *, scrolling: bool = False) -> None:
        if not hasattr(self, "_thumbnail_scheduler"):
            return
        self._thumbnail_scheduler.request_visible(scrolling=scrolling)

    def _set_thumbnail_scrolling(self, scrolling: bool) -> None:
        self._is_scrolling_for_thumbnails = scrolling

    def _request_visible_thumbnail_batch(self, scrolling: bool) -> int:
        view = self._active_view()
        if not view:
            return 0

        visible_indexes: list[QModelIndex] = []
        if isinstance(view, QTreeView):
            visible_indexes = self._visible_tree_indexes(view)
        elif isinstance(view, QListView):
            visible_indexes = self._visible_tile_indexes(view)

        request_limit = 6
        requested = self._model.set_visible_thumbnail_targets(
            visible_indexes,
            request_limit=request_limit,
            allow_folder_preview=not scrolling,
        )
        return requested

    def _visible_tree_indexes(self, view: QTreeView) -> list[QModelIndex]:
        indexes: list[QModelIndex] = []
        viewport = view.viewport()
        height = max(1, view.sizeHintForRow(0))
        y = 0
        seen_rows: set[int] = set()
        while y < viewport.height():
            index = view.indexAt(QPoint(0, y))
            if index.isValid() and index.row() not in seen_rows:
                seen_rows.add(index.row())
                indexes.append(index.siblingAtColumn(0))
            y += height
        bottom = view.indexAt(QPoint(0, max(0, viewport.height() - 1)))
        if bottom.isValid() and bottom.row() not in seen_rows:
            indexes.append(bottom.siblingAtColumn(0))
        return indexes

    def _visible_tile_indexes(self, view: QListView) -> list[QModelIndex]:
        indexes: list[QModelIndex] = []
        viewport = view.viewport()
        rect = viewport.rect()
        # QListView::indexAt only returns an item when the probe point is inside
        # the painted item rect. A tile-sized stride can skip every item if the
        # probes fall in gutters, so use a small viewport-local stride instead
        # of scanning model rows.
        step = tile_probe_step(view.iconSize().width())
        seen: set[tuple[int, int, int]] = set()

        for point in tile_probe_points(rect, step):
            index = view.indexAt(point)
            if index.isValid():
                key = index_identity(index.row(), index.column(), index.internalId())
                if key not in seen:
                    seen.add(key)
                    indexes.append(index.siblingAtColumn(0))
        return indexes

    def _handle_thumbnail_updated(self, index: QModelIndex) -> None:
        """Force repaint when a thumbnail icon is ready."""
        for view in (self._tile_view, self._tree_view):
            rect = view.visualRect(index)
            if rect.isValid():
                view.viewport().update(rect)
