"""削除後の分割レイアウト中は、確定前の画面を描画しない。

以前は ``setUpdatesEnabled(False)`` で描画を止めていた。しかしそれだと、保留中に
親ウィジェット側の再描画（ステータスバーの更新に伴うウィンドウ全体のレイアウト
など）が入ったとき、親は背景を塗るのに保留中のビューは描画を飛ばされ、一覧の
領域が背景色のまま残る（削除の直後に一覧が一瞬空になる現象）。

そこで保留の開始時にビューポートとスクロールバーの見た目を撮っておき、保留中の
描画要求にはその画像で応える。誰が再描画を起こしても、見えるのは確定前の画面
ではなく保留を始めたときの画面になる。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QTimer
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QListView, QWidget

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
        # 保留中に代わりに描く画像。キーは描画を差し替えるウィジェット。
        self._frozen: dict[QWidget, QPixmap] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._try_finish)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(2000)
        self._deadline.timeout.connect(self._expire)

    @property
    def active(self) -> bool:
        """表示を保留している最中か。"""
        return self._active

    def begin(self, *_args: object) -> None:
        """Qtが選択やスクロール範囲を変更する前に、現在の表示を保つ。"""
        # 描画を止めているのが別の持ち主なら、そちらに任せる。
        if self._active or not self._view.isVisible() or not self._view.updatesEnabled():
            return
        bar = self._view.verticalScrollBar()
        if bar is None or bar.value() == 0:
            return
        self._active = True
        self._freeze(self._view.viewport())
        self._freeze(bar)
        self._timer.start()
        self._deadline.start()

    def defer_scroll(self, index: QModelIndex, hint: QAbstractItemView.ScrollHint) -> bool:
        if not self._active:
            return False
        self._pending_scroll = (QPersistentModelIndex(index), hint)
        return True

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        if event is None or event.type() != QEvent.Type.Paint:
            return False
        pixmap = self._frozen.get(watched) if isinstance(watched, QWidget) else None
        if pixmap is None:
            return False
        assert isinstance(watched, QWidget)
        painter = QPainter(watched)
        try:
            painter.drawPixmap(0, 0, pixmap)
        finally:
            painter.end()
        return True

    def _freeze(self, widget: QWidget | None) -> None:
        if widget is None or not widget.isVisible():
            return
        # 撮影は描画を伴うので、差し替えを有効にする前に行う。
        self._frozen[widget] = widget.grab()
        widget.installEventFilter(self)

    def _thaw(self) -> None:
        frozen = self._frozen
        self._frozen = {}
        for widget in frozen:
            widget.removeEventFilter(self)
            widget.update()

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
            self._thaw()
