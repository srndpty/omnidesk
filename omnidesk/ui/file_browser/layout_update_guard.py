"""削除後の分割レイアウト中は、確定前の画面を描画しない。

以前は ``setUpdatesEnabled(False)`` で描画を止めていた。しかしそれだと、保留中に
親ウィジェット側の再描画（ステータスバーの更新に伴うウィンドウ全体のレイアウト
など）が入ったとき、親は背景を塗るのに保留中のビューは描画を飛ばされ、一覧の
領域が背景色のまま残る（削除の直後に一覧が一瞬空になる現象）。

そこで保留の開始時にビューポートとスクロールバーの見た目を撮っておき、保留中の
描画要求にはその画像で応える（凍結）。誰が再描画を起こしても、見えるのは確定前の
画面ではなく保留を始めたときの画面になる。

凍結中は画面と内部状態（配置途中のレイアウト、丸められたスクロール値）が
食い違うので、ユーザー操作は次のように扱う。

* ホイールとキー入力は溜めておき、保留を解いてから順に再生する。丸められた
  スクロール値を基準に動かすと、解除後に意図しない位置へ飛ぶため。再生は選択
  復元のスクロールより後なので、ユーザーの操作が最後に勝つ。
* クリックは捨てる。見えている絵（古い配置）と当たり判定（新しい配置）が
  一致しないので、別の項目を選んでしまう。押下を捨てたら、対になる離しも捨てる。

ウィジェットの大きさや倍率が撮影時と変わったら、画像は使えないので凍結だけを
やめる（スクロール位置の保留は続ける）。通常のリサイズと同じ見え方になる。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QPersistentModelIndex, QTimer
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QListView, QWidget

logger = logging.getLogger(__name__)

# 凍結中に溜めて、解除後に再生する入力。
_REPLAYED_INPUT = (QEvent.Type.Wheel, QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
# 凍結中は捨てる入力（古い絵の上でのクリック）。
_DISCARDED_PRESS = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)


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
        self._queued_input: list[tuple[QObject, QEvent]] = []
        self._discarding_release = False
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

    @property
    def freezing(self) -> bool:
        """ビューポートを撮影した画像で描いている最中か。"""
        return self._view.viewport() in self._frozen

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
        if self._frozen:
            # キー入力はビュー本体に届くので、ビューでも受ける。
            self._view.installEventFilter(self)
        self._timer.start()
        self._deadline.start()

    def defer_scroll(self, index: QModelIndex, hint: QAbstractItemView.ScrollHint) -> bool:
        if not self._active:
            return False
        self._pending_scroll = (QPersistentModelIndex(index), hint)
        return True

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802
        if watched is None or event is None or not self._frozen:
            return False
        event_type = event.type()
        if event_type == QEvent.Type.Paint:
            return self._paint_frozen(watched)
        if event_type in _REPLAYED_INPUT:
            clone = event.clone()
            if clone is not None:
                self._queued_input.append((watched, clone))
            return True
        if event_type in _DISCARDED_PRESS:
            logger.debug("レイアウト確定待ちの間のクリックを無視しました")
            self._discarding_release = True
            return True
        if event_type == QEvent.Type.MouseButtonRelease and self._discarding_release:
            self._discarding_release = False
            return True
        return False

    def _paint_frozen(self, watched: QObject) -> bool:
        pixmap = self._frozen.get(watched) if isinstance(watched, QWidget) else None
        if pixmap is None:
            return False
        assert isinstance(watched, QWidget)
        if (
            pixmap.deviceIndependentSize().toSize() != watched.size()
            or pixmap.devicePixelRatio() != watched.devicePixelRatioF()
        ):
            # 大きさや倍率が変わった。画像は合わないので、通常の描画へ戻す。
            logger.debug("レイアウト確定待ちの間にサイズが変わったため凍結を解きました")
            self._thaw()
            return False
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
        self._view.removeEventFilter(self)
        for widget in frozen:
            widget.removeEventFilter(self)
            widget.update()

    def _replay_queued_input(self) -> None:
        queued = self._queued_input
        self._queued_input = []
        self._discarding_release = False
        for receiver, event in queued:
            QApplication.sendEvent(receiver, event)

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
        """表示を再開する。ディレクトリ切り替え時には保留位置と溜めた入力を捨てる。"""
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
            if apply_scroll:
                self._replay_queued_input()
            else:
                self._queued_input = []
                self._discarding_release = False
