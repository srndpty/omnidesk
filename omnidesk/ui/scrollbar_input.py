"""スクロールバーのマウス操作を Windows Explorer に寄せる入力フィルタ。

Shift+左クリックでトラック（ページ領域）を押すと、つまみをその位置へジャンプさせ、
そのままドラッグを続けられるようにする。

ビューごとにスクロールバーが作り直されても取り付け漏れが起きないよう、個々の
スクロールバーではなくトップレベルの ``QWindow`` にフィルタを1つだけ置く。
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt
from PyQt6.QtGui import QMouseEvent, QWindow
from PyQt6.QtWidgets import QScrollBar, QStyle, QStyleOptionSlider, QWidget

logger = logging.getLogger(__name__)

_PRESS_TYPES = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)


def absolute_scroll_value(
    click_pos: int,
    groove_start: int,
    groove_length: int,
    slider_length: int,
    minimum: int,
    maximum: int,
    *,
    upside_down: bool = False,
) -> int:
    """つまみの中心が ``click_pos`` に来るスクロール値を返す。

    Qt の ``SH_ScrollBar_LeftClickAbsolutePosition`` と同じ計算。座標はすべて
    スクロール方向の軸（縦なら y、横なら x）で、スクロールバーのローカル座標。
    """
    if maximum <= minimum:
        return minimum
    span = max(0, groove_length - slider_length)
    offset = click_pos - groove_start - slider_length // 2
    return QStyle.sliderValueFromPosition(minimum, maximum, offset, span, upside_down)


def _style_option(bar: QScrollBar) -> QStyleOptionSlider:
    # initStyleOption() は protected で、C++ 側で生成されたスクロールバーからは
    # 呼べないため、QScrollBar::initStyleOption() と同じ内容を手で詰める。
    option = QStyleOptionSlider()
    option.initFrom(bar)
    option.subControls = QStyle.SubControl.SC_All
    option.activeSubControls = QStyle.SubControl.SC_None
    option.orientation = bar.orientation()
    option.minimum = bar.minimum()
    option.maximum = bar.maximum()
    option.sliderPosition = bar.sliderPosition()
    option.sliderValue = bar.value()
    option.singleStep = bar.singleStep()
    option.pageStep = bar.pageStep()
    option.upsideDown = bar.invertedAppearance()
    if bar.orientation() == Qt.Orientation.Horizontal:
        option.state |= QStyle.StateFlag.State_Horizontal
    return option


def jump_scroll_bar_to(bar: QScrollBar, local_pos: QPoint) -> bool:
    """``local_pos`` がページ領域なら、つまみをその位置へ移動して True を返す。

    つまみや矢印ボタン上の押下は通常のクリックとして扱わせるため何もしない。
    """
    style = bar.style()
    if style is None:
        return False
    option = _style_option(bar)
    control = QStyle.ComplexControl.CC_ScrollBar
    hit = style.hitTestComplexControl(control, option, local_pos, bar)
    if hit not in (QStyle.SubControl.SC_ScrollBarAddPage, QStyle.SubControl.SC_ScrollBarSubPage):
        return False
    groove = style.subControlRect(control, option, QStyle.SubControl.SC_ScrollBarGroove, bar)
    slider = style.subControlRect(control, option, QStyle.SubControl.SC_ScrollBarSlider, bar)
    if bar.orientation() == Qt.Orientation.Horizontal:
        value = absolute_scroll_value(
            local_pos.x(),
            groove.x(),
            groove.width(),
            slider.width(),
            bar.minimum(),
            bar.maximum(),
            upside_down=bar.invertedAppearance(),
        )
    else:
        value = absolute_scroll_value(
            local_pos.y(),
            groove.y(),
            groove.height(),
            slider.height(),
            bar.minimum(),
            bar.maximum(),
            upside_down=bar.invertedAppearance(),
        )
    bar.setValue(value)
    return True


class ScrollBarInputFilter(QObject):
    """トップレベル ``QWindow`` に入るマウス押下を見て、Shift+クリックのジャンプを補う。"""

    def __init__(self, window_widget: QWidget) -> None:
        super().__init__(window_widget)
        self._window_widget = window_widget

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if not isinstance(a1, QMouseEvent) or a1.type() not in _PRESS_TYPES:
            return False
        if a1.button() != Qt.MouseButton.LeftButton:
            return False
        if not a1.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return False
        pos = a1.position().toPoint()
        bar = self._window_widget.childAt(pos)
        if isinstance(bar, QScrollBar) and bar.isEnabled():
            # 先につまみを移動しておくと、続く押下がつまみ上に落ちてそのままドラッグになる。
            jump_scroll_bar_to(bar, bar.mapFrom(self._window_widget, pos))
        return False


def install_scroll_bar_input(window_widget: QWidget) -> ScrollBarInputFilter | None:
    """トップレベルウィジェットの ``QWindow`` にスクロールバー入力フィルタを取り付ける。"""
    window_widget.winId()  # QWindow を確実に生成させる
    handle: QWindow | None = window_widget.windowHandle()
    if handle is None:
        logger.warning("QWindow を取得できないため、スクロールバー入力の拡張を無効にします")
        return None
    input_filter = ScrollBarInputFilter(window_widget)
    handle.installEventFilter(input_filter)
    return input_filter


__all__ = [
    "ScrollBarInputFilter",
    "absolute_scroll_value",
    "install_scroll_bar_input",
    "jump_scroll_bar_to",
]
