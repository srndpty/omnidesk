"""並べ替えとrefresh後の選択復元を管理するコントローラ。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtWidgets import QHeaderView

from ..file_browser_navigation import refresh_sort_action, same_navigation_path
from .sort_model import SortedFileSystemModel
from .views import _FileTileView, _FileTreeView


class SortRefreshController:
    """ソート状態、遅延タイマー、refresh選択復元を一か所に保持する。"""

    def __init__(
        self,
        parent: QObject,
        *,
        model: SortedFileSystemModel,
        header: QHeaderView,
        tree_view: _FileTreeView,
        tile_view: _FileTileView,
        selected_path: Callable[[], Path | None],
        select_path: Callable[[Path], bool],
    ) -> None:
        self._model = model
        self._header = header
        self._tree_view = tree_view
        self._tile_view = tile_view
        self._selected_path = selected_path
        self._select_path = select_path
        self._active = False
        self._retries = 0
        self._selection_path: Path | None = None
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self.apply_refresh_sort)

    def begin_refresh_sort(self, selected_path: Path | None) -> None:
        self._selection_path = selected_path
        self._active = True
        self._retries = 10

    def set_sort_mode(self, mode: str) -> bool:
        if mode not in ("name", "extension"):
            return False
        if self._model.sort_mode() == mode and self._header.sortIndicatorSection() == 0:
            return False
        self._tree_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._model.set_sort_mode(mode)
        return True

    def sort_mode(self) -> str:
        return self._model.sort_mode()

    def sort_current_directory(self, *, reason: str) -> None:
        column = self._header.sortIndicatorSection()
        if column < 0:
            column = 0
        order = self._header.sortIndicatorOrder()
        _ = reason
        self._model.sort(column, order)
        self._tile_view.scheduleDelayedItemsLayout()

    def schedule_refresh_sort(self) -> None:
        if not self._active or self._retries <= 0:
            return
        if not self._timer.isActive():
            self._timer.start()

    def apply_refresh_sort(self) -> None:
        retries_before_attempt = self._retries
        if not self._active or retries_before_attempt <= 0:
            self._active = False
            return
        self._retries -= 1
        self.sort_current_directory(reason="refresh-deferred")
        restore_succeeded = (
            self._selection_path
            and self._selection_path.exists()
            and self.can_restore_refresh_selection(self._selection_path)
            and self._select_path(self._selection_path)
        )
        action = refresh_sort_action(
            active=self._active,
            retries_before_attempt=retries_before_attempt,
            restore_succeeded=bool(restore_succeeded),
        )
        if action == "selected":
            self._selection_path = None
            self._retries = 0
            self._active = False
            return
        if action in {"inactive", "exhausted"}:
            self._active = False
            return
        if action == "retry":
            self.schedule_refresh_sort()

    def can_restore_refresh_selection(self, path: Path) -> bool:
        current = self._selected_path()
        if current is None or same_navigation_path(current, path):
            return True
        self.cancel()
        return False

    def cancel(self) -> None:
        self._timer.stop()
        self._selection_path = None
        self._retries = 0
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, value: bool) -> None:
        self._active = value

    @property
    def retries(self) -> int:
        return self._retries

    @retries.setter
    def retries(self, value: int) -> None:
        self._retries = value

    @property
    def selection_path(self) -> Path | None:
        return self._selection_path

    @selection_path.setter
    def selection_path(self, value: Path | None) -> None:
        self._selection_path = value
