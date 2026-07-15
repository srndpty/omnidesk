"""ファイルブラウザの選択復元状態を管理するコントローラ。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, QObject, QTimer
from PyQt6.QtWidgets import QAbstractItemView

from ..file_browser_selection import pending_selection_action
from .sort_model import SortedFileSystemModel

_ActiveView = Callable[[], QAbstractItemView]
_PathCheck = Callable[[], bool]
_Refresh = Callable[[bool], None]
_DeferScroll = Callable[[Path, QAbstractItemView.ScrollHint], None]
_ApplySelection = Callable[[Path, QAbstractItemView.ScrollHint | None], bool]


class SelectionRestoreController:
    """保留中の選択パス、復元タイマー、選択適用を一か所に閉じ込める。"""

    def __init__(
        self,
        parent: QObject,
        *,
        model: SortedFileSystemModel,
        active_view: _ActiveView,
        apply_selection: _ApplySelection,
        defer_scroll: _DeferScroll,
        has_current_selection: _PathCheck,
        select_first_row: Callable[[], None],
        has_deferred_refresh: _PathCheck,
        refresh: _Refresh,
    ) -> None:
        self._model = model
        self._active_view = active_view
        self._apply_selection = apply_selection
        self._defer_scroll = defer_scroll
        self._has_current_selection = has_current_selection
        self._select_first_row = select_first_row
        self._has_deferred_refresh = has_deferred_refresh
        self._refresh = refresh
        self._pending_path: Path | None = None
        self._scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.select_pending_or_first_row)

    @property
    def pending_path(self) -> Path | None:
        return self._pending_path

    @pending_path.setter
    def pending_path(self, path: Path | None) -> None:
        self._pending_path = path

    @property
    def scroll_hint(self) -> QAbstractItemView.ScrollHint:
        return self._scroll_hint

    @scroll_hint.setter
    def scroll_hint(self, hint: QAbstractItemView.ScrollHint) -> None:
        self._scroll_hint = hint

    def select_path(
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
            self._defer_scroll(path, scroll_hint)
        return True

    def refresh_and_select(self, path: Path, *, preserve_selection: bool = True) -> None:
        self._pending_path = path
        self._scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        self._refresh(preserve_selection)
        self.select_pending_path_if_ready()

    def select_pending_path_if_ready(self) -> bool:
        pending = self._pending_path
        if pending is None or not self._apply_selection(pending, None):
            return False
        if self._has_deferred_refresh():
            return True
        self.clear_pending()
        return True

    def schedule(self) -> None:
        self._timer.stop()
        self._timer.start(0)

    def select_pending_or_first_row(self) -> None:
        pending = self._pending_path
        pending_exists = bool(pending and pending.exists())
        selected_pending = bool(
            pending and pending_exists and self._apply_selection(pending, self._scroll_hint)
        )
        action = pending_selection_action(
            pending,
            pending_exists=pending_exists,
            selected_in_current_directory=self._has_current_selection(),
            pending_select_succeeded=selected_pending,
        )
        if action == "selected_pending":
            self.clear_pending()
            return
        if action in {"wait_for_pending", "keep_current"}:
            return
        self.clear_pending()
        self._select_first_row()

    def clear_pending(self) -> None:
        self._pending_path = None
        self._scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible

    def is_scheduled(self) -> bool:
        return self._timer.isActive()

    def cancel(self) -> None:
        self._timer.stop()
        self.clear_pending()
