"""Tests for FileBrowserThumbnailScheduler."""

from __future__ import annotations

from unittest.mock import MagicMock, call

from omnidesk.ui.file_browser_background import FileBrowserThumbnailScheduler


def _make_scheduler(
    *,
    is_active: bool = True,
    request_visible_return: int = 1,
) -> tuple[FileBrowserThumbnailScheduler, MagicMock, MagicMock, MagicMock]:
    """Return (scheduler, request_timer, scroll_settle_timer, idle_batch_timer)."""
    request_timer = MagicMock()
    request_timer.isActive.return_value = False
    scroll_settle_timer = MagicMock()
    idle_batch_timer = MagicMock()
    set_scrolling = MagicMock()
    request_visible = MagicMock(return_value=request_visible_return)

    scheduler = FileBrowserThumbnailScheduler(
        request_timer=request_timer,
        scroll_settle_timer=scroll_settle_timer,
        idle_batch_timer=idle_batch_timer,
        is_active=lambda: is_active,
        set_scrolling=set_scrolling,
        request_visible=request_visible,
    )
    return scheduler, request_timer, scroll_settle_timer, idle_batch_timer


class TestHandleScroll:
    def test_marks_scrolling_and_starts_timers(self) -> None:
        scheduler, request_timer, scroll_settle_timer, idle_batch_timer = _make_scheduler()
        set_scrolling = MagicMock()
        scheduler._set_scrolling = set_scrolling

        scheduler.handle_scroll()

        set_scrolling.assert_called_once_with(True)
        idle_batch_timer.stop.assert_called_once()
        request_timer.start.assert_called_once()
        scroll_settle_timer.start.assert_called_once()

    def test_does_nothing_when_inactive(self) -> None:
        scheduler, request_timer, scroll_settle_timer, idle_batch_timer = _make_scheduler(
            is_active=False
        )
        set_scrolling = MagicMock()
        scheduler._set_scrolling = set_scrolling

        scheduler.handle_scroll()

        set_scrolling.assert_not_called()
        idle_batch_timer.stop.assert_not_called()
        request_timer.start.assert_not_called()

    def test_does_not_restart_already_active_request_timer(self) -> None:
        scheduler, request_timer, scroll_settle_timer, idle_batch_timer = _make_scheduler()
        request_timer.isActive.return_value = True

        scheduler.handle_scroll()

        request_timer.start.assert_not_called()
        scroll_settle_timer.start.assert_called_once()


class TestRestart:
    def test_stops_then_starts_request_timer(self) -> None:
        scheduler, request_timer, scroll_settle_timer, idle_batch_timer = _make_scheduler()

        scheduler.restart()

        request_timer.stop.assert_called_once()
        request_timer.start.assert_called_once()
        assert request_timer.mock_calls == [call.stop(), call.start()]

    def test_starts_scroll_settle_and_stops_idle(self) -> None:
        scheduler, request_timer, scroll_settle_timer, idle_batch_timer = _make_scheduler()

        scheduler.restart()

        scroll_settle_timer.start.assert_called_once()
        idle_batch_timer.stop.assert_called_once()
        idle_batch_timer.start.assert_not_called()

    def test_does_nothing_when_inactive(self) -> None:
        scheduler, request_timer, scroll_settle_timer, idle_batch_timer = _make_scheduler(
            is_active=False
        )

        scheduler.restart()

        request_timer.stop.assert_not_called()
        request_timer.start.assert_not_called()


class TestRequestSettled:
    def test_clears_scrolling_and_calls_request_visible(self) -> None:
        scheduler, _, _, idle_batch_timer = _make_scheduler(request_visible_return=3)
        set_scrolling = MagicMock()
        scheduler._set_scrolling = set_scrolling

        scheduler.request_settled()

        set_scrolling.assert_called_once_with(False)
        idle_batch_timer.start.assert_called_once()


class TestRequestVisible:
    def test_starts_idle_timer_when_items_requested_and_not_scrolling(self) -> None:
        scheduler, _, _, idle_batch_timer = _make_scheduler(request_visible_return=5)

        scheduler.request_visible(scrolling=False)

        idle_batch_timer.start.assert_called_once()

    def test_does_not_start_idle_timer_when_scrolling(self) -> None:
        scheduler, _, _, idle_batch_timer = _make_scheduler(request_visible_return=5)

        scheduler.request_visible(scrolling=True)

        idle_batch_timer.start.assert_not_called()

    def test_does_not_start_idle_timer_when_no_items(self) -> None:
        scheduler, _, _, idle_batch_timer = _make_scheduler(request_visible_return=0)

        scheduler.request_visible(scrolling=False)

        idle_batch_timer.start.assert_not_called()

    def test_does_not_start_idle_timer_when_inactive(self) -> None:
        scheduler, _, _, idle_batch_timer = _make_scheduler(
            is_active=False, request_visible_return=5
        )

        scheduler.request_visible(scrolling=False)

        idle_batch_timer.start.assert_not_called()

    def test_returns_count_from_underlying_callable(self) -> None:
        scheduler, _, _, _ = _make_scheduler(request_visible_return=7)

        result = scheduler.request_visible(scrolling=True)

        assert result == 7


class TestCancel:
    def test_stops_all_three_timers(self) -> None:
        scheduler, request_timer, scroll_settle_timer, idle_batch_timer = _make_scheduler()

        scheduler.cancel()

        request_timer.stop.assert_called_once()
        scroll_settle_timer.stop.assert_called_once()
        idle_batch_timer.stop.assert_called_once()
