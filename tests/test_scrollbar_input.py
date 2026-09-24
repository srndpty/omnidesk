from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QWindow
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QHBoxLayout, QListWidget, QScrollBar, QWidget
from pytestqt.qtbot import QtBot

from omnidesk.theme import DARK_STYLESHEET
from omnidesk.ui.file_browser_tab import FileBrowserTab
from omnidesk.ui.scrollbar_input import (
    absolute_scroll_value,
    install_scroll_bar_input,
    jump_scroll_bar_to,
)

LEFT = Qt.MouseButton.LeftButton
NO_MOD = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


def test_absolute_scroll_value_centers_slider_on_click() -> None:
    # 溝 0..400、つまみ 100px、値域 0..300 → つまみの中心が 250 なら上端 200 = 値 200
    assert absolute_scroll_value(250, 0, 400, 100, 0, 300) == 200


def test_absolute_scroll_value_clamps_to_range() -> None:
    assert absolute_scroll_value(-50, 0, 400, 100, 0, 300) == 0
    assert absolute_scroll_value(1000, 0, 400, 100, 0, 300) == 300


def test_absolute_scroll_value_handles_empty_range() -> None:
    assert absolute_scroll_value(200, 0, 400, 400, 5, 5) == 5


class _Harness(QWidget):
    """余白付きレイアウトにスクロールバー付きリストを置いた最小ウィンドウ。"""

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.list = QListWidget(self)
        self.list.addItems([f"item {i}" for i in range(500)])
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        layout.addWidget(self.list)
        self.resize(400, 300)

    @property
    def bar(self) -> QScrollBar:
        bar = self.list.verticalScrollBar()
        assert bar is not None
        return bar

    def bar_rect(self) -> tuple[int, int, int, int]:
        top_left = self.bar.mapTo(self, QPoint(0, 0))
        return top_left.x(), top_left.y(), self.bar.width(), self.bar.height()


@pytest.fixture
def harness(qtbot: QtBot) -> tuple[_Harness, QWindow]:
    widget = _Harness()
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    assert install_scroll_bar_input(widget) is not None
    handle = widget.windowHandle()
    assert handle is not None
    return widget, handle


def test_shift_click_directly_on_track_jumps_to_position(
    harness: tuple[_Harness, QWindow],
) -> None:
    widget, handle = harness
    bar = widget.bar
    bar.setValue(0)
    x, y, w, h = widget.bar_rect()
    middle = QPoint(x + w // 2, y + h // 2)

    QTest.mousePress(handle, LEFT, SHIFT, middle)
    QTest.mouseRelease(handle, LEFT, SHIFT, middle)

    ratio = bar.value() / bar.maximum()
    assert 0.35 < ratio < 0.65


def test_plain_click_directly_on_track_only_pages(harness: tuple[_Harness, QWindow]) -> None:
    widget, handle = harness
    bar = widget.bar
    bar.setValue(0)
    x, y, w, h = widget.bar_rect()
    middle = QPoint(x + w // 2, y + h // 2)

    QTest.mousePress(handle, LEFT, NO_MOD, middle)
    QTest.mouseRelease(handle, LEFT, NO_MOD, middle)

    assert bar.value() == bar.pageStep()


def test_shift_drag_after_jump_keeps_moving_slider(harness: tuple[_Harness, QWindow]) -> None:
    widget, handle = harness
    bar = widget.bar
    bar.setValue(0)
    x, y, w, h = widget.bar_rect()
    start = QPoint(x + w // 2, y + h // 2)

    QTest.mousePress(handle, LEFT, SHIFT, start)
    jumped = bar.value()
    QTest.mouseMove(handle, start + QPoint(0, 40))
    QTest.mouseRelease(handle, LEFT, SHIFT, start + QPoint(0, 40))

    assert bar.value() > jumped


def test_jump_scroll_bar_to_ignores_slider_and_arrows(harness: tuple[_Harness, QWindow]) -> None:
    widget, _handle = harness
    bar = widget.bar
    bar.setValue(0)

    assert not jump_scroll_bar_to(bar, QPoint(bar.width() // 2, 2))  # 上矢印
    assert bar.value() == 0


@pytest.mark.parametrize("view_attr", ["_tree_view", "_tile_view"])
def test_file_browser_scroll_bar_touches_right_edge(qtbot: QtBot, view_attr: str) -> None:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.setStyleSheet(DARK_STYLESHEET)
    view = getattr(tab, view_attr)
    tab._view_stack.setCurrentWidget(view)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    tab.resize(600, 400)
    tab.show()
    qtbot.waitExposed(tab)

    bar = view.verticalScrollBar()
    right = bar.mapTo(tab, QPoint(bar.width(), 0)).x()
    # 最大化時に画面の一番右でも掴めるよう、右余白・右枠線を挟まない
    assert right == tab.width()


def test_dark_theme_tab_pane_has_no_right_border() -> None:
    pane = DARK_STYLESHEET.split("QTabWidget::pane {", 1)[1].split("}", 1)[0]
    assert "border-right: 0px;" in pane
