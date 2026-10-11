"""削除後の分割レイアウト中は、確定前の画面を描画しない。"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QModelIndex, QObject, QPersistentModelIndex, QTimer
from PyQt6.QtWidgets import QAbstractItemView, QListView

logger = logging.getLogger(__name__)


class LayoutUpdateGuard(QObject):
    """表示を保留し、最後の行とスクロール位置が確定してから再開する。"""

    def __init__(
        self,
        view: QListView,
        scroll: Callable[[QModelIndex, QAbstractItemView.ScrollHint], None],
    ) -> None:
        super().__init__(view)
        self._view = view
        self._scroll = scroll
        self._active = False
        self._pending_scroll: tuple[QPersistentModelIndex, QAbstractItemView.ScrollHint] | None = (
            None
        )
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._try_finish)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(2000)
        self._deadline.timeout.connect(self._expire)

    def begin(self, *_args: object) -> None:
        """Qtが選択やスクロール範囲を変更する前に、現在の表示を保つ。"""
        if self._active or not self._view.isVisible() or not self._view.updatesEnabled():
            return
        bar = self._view.verticalScrollBar()
        if bar is None or bar.value() == 0:
            return
        self._active = True
        self._view.setUpdatesEnabled(False)
        self._timer.start()
        self._deadline.start()

    def defer_scroll(self, index: QModelIndex, hint: QAbstractItemView.ScrollHint) -> bool:
        if not self._active:
            return False
        self._pending_scroll = (QPersistentModelIndex(index), hint)
        return True

    def _try_finish(self) -> None:
        model = self._view.model()
        if model is not None:
            root = self._view.rootIndex()
            count = model.rowCount(root)
            # Batchedでは未配置の行の矩形が空になる。最終行まで配置されるのを待つ。
            if count and self._view.visualRect(model.index(count - 1, 0, root)).isEmpty():
                return
        self.finish()

    def _expire(self) -> None:
        logger.warning("削除後のレイアウト確定待ちが時間切れになりました")
        self.finish()

    def finish(self, *, apply_scroll: bool = True) -> None:
        """表示を再開する。ディレクトリ切り替え時には保留位置を捨てる。"""
        self._timer.stop()
        self._deadline.stop()
        pending = self._pending_scroll
        self._pending_scroll = None
        if not self._active:
            return
        self._active = False
        try:
            if apply_scroll and pending is not None and pending[0].isValid():
                self._scroll(QModelIndex(pending[0]), pending[1])
        finally:
            self._view.setUpdatesEnabled(True)
