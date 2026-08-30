from __future__ import annotations

from PyQt6.QtWidgets import QScrollBar

from omnidesk.ui.file_browser.scroll_keeper import (
    BatchedLayoutScrollKeeper,
    restored_scroll_value,
)


def test_restored_scroll_value_waits_until_range_is_back() -> None:
    """レイアウトが途中（最大値が足りない）うちは書き戻さない。"""
    assert restored_scroll_value(1000, current=500, maximum=600) is None


def test_restored_scroll_value_restores_when_range_allows() -> None:
    assert restored_scroll_value(1000, current=500, maximum=1500) == 1000


def test_restored_scroll_value_ignores_unneeded_cases() -> None:
    assert restored_scroll_value(None, current=500, maximum=1500) is None
    assert restored_scroll_value(1000, current=1000, maximum=1500) is None


def _scroll_bar(qtbot, maximum: int) -> QScrollBar:
    bar = QScrollBar()
    qtbot.addWidget(bar)
    bar.setRange(0, maximum)
    return bar


def test_keeper_restores_position_after_range_shrinks_and_returns(qtbot) -> None:
    bar = _scroll_bar(qtbot, 1000)
    keeper = BatchedLayoutScrollKeeper(bar)
    bar.setValue(800)

    keeper.remember()
    bar.setRange(0, 300)  # レイアウトのやり直しで一時的に縮む
    assert bar.value() == 300
    bar.setRange(0, 1000)

    assert bar.value() == 800


def test_keeper_ignores_top_position_and_explicit_cancel(qtbot) -> None:
    bar = _scroll_bar(qtbot, 1000)
    keeper = BatchedLayoutScrollKeeper(bar)

    bar.setValue(0)
    keeper.remember()
    assert keeper.pending_value is None

    bar.setValue(800)
    keeper.remember()
    keeper.cancel()
    bar.setRange(0, 300)
    bar.setRange(0, 1000)

    assert bar.value() == 300
