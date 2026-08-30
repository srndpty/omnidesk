"""バッチレイアウトのやり直しで、スクロール位置が飛ばないように保つ仕組み。

``_FileTileView`` は :attr:`QListView.LayoutMode.Batched` を使う（大量ファイルの
フォルダでGUIスレッドを止めないため）。このモードでは行が増減するたびに
レイアウトが最初からやり直され、途中の段階ではスクロールバーの最大値が
実際より小さくなる。現在値はその小さい最大値へ丸められ、レイアウトが
終わって最大値が戻っても、値は戻らない。

そのため、外部アプリがフォルダ内のファイルを移動・削除しただけで、
見ていた位置が上へ飛んでいた（再生中の動画を移動したときに顕著）。

ここでは行が増減する直前の位置を覚え、レイアウトが進んで最大値が戻った
ところで書き戻す。``scrollTo()`` のような明示的なスクロールが入ったら、
そちらを優先して保留は捨てる。
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QScrollBar

# レイアウトが何度かに分けて進むあいだだけ書き戻したい。これを過ぎたら、
# もう自分たちの知らない理由で動いた位置とみなして手を引く。
RESTORE_WINDOW_MS = 2_000


def restored_scroll_value(pending: int | None, current: int, maximum: int) -> int | None:
    """書き戻すべきスクロール位置を返す。戻す必要がなければ ``None``。

    まだ最大値が保留位置に届いていない（レイアウトが途中）ときは、書き戻しても
    また丸められるだけなので何もしない。
    """
    if pending is None or pending == current:
        return None
    if maximum < pending:
        return None
    return pending


class BatchedLayoutScrollKeeper(QObject):
    """行の増減をまたいで、縦スクロール位置を保つ。"""

    def __init__(self, scroll_bar: QScrollBar, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scroll_bar = scroll_bar
        self._pending: int | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RESTORE_WINDOW_MS)
        self._timer.timeout.connect(self.cancel)
        scroll_bar.rangeChanged.connect(self._handle_range_changed)
        scroll_bar.sliderPressed.connect(self.cancel)
        scroll_bar.actionTriggered.connect(lambda _action: self.cancel())

    @property
    def pending_value(self) -> int | None:
        return self._pending

    def remember(self) -> None:
        """いまのスクロール位置を、レイアウトのやり直しに備えて覚える。"""
        value = self._scroll_bar.value()
        if value <= 0:
            self.cancel()
            return
        self._pending = value
        self._timer.start()

    def cancel(self) -> None:
        self._pending = None
        self._timer.stop()

    def _handle_range_changed(self, _minimum: int, maximum: int) -> None:
        target = restored_scroll_value(self._pending, self._scroll_bar.value(), maximum)
        if target is None:
            return
        self._scroll_bar.setValue(target)
