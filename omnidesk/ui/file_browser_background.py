"""Small controllers for FileBrowserTab background scheduling."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QTimer


class FileBrowserThumbnailScheduler:
    """Coordinate visible-thumbnail request timers for one browser tab."""

    def __init__(
        self,
        *,
        request_timer: QTimer,
        scroll_settle_timer: QTimer,
        idle_batch_timer: QTimer,
        is_active: Callable[[], bool],
        set_scrolling: Callable[[bool], None],
        request_visible: Callable[[bool], int],
    ) -> None:
        self._request_timer = request_timer
        self._scroll_settle_timer = scroll_settle_timer
        self._idle_batch_timer = idle_batch_timer
        self._is_active = is_active
        self._set_scrolling = set_scrolling
        self._request_visible = request_visible

    def handle_scroll(self) -> None:
        if not self._is_active():
            return
        self._set_scrolling(True)
        self._idle_batch_timer.stop()
        if not self._request_timer.isActive():
            self._request_timer.start()
        self._scroll_settle_timer.start()

    def restart(self) -> None:
        if not self._is_active():
            return
        self._request_timer.stop()
        self._request_timer.start()
        self._scroll_settle_timer.start()
        self._idle_batch_timer.stop()

    def request_settled(self) -> None:
        self._set_scrolling(False)
        self.request_visible(scrolling=False)

    def request_visible(self, *, scrolling: bool) -> int:
        requested = self._request_visible(scrolling)
        if not scrolling and requested > 0 and self._is_active():
            self._idle_batch_timer.start()
        return requested

    def cancel(self) -> None:
        self._request_timer.stop()
        self._scroll_settle_timer.stop()
        self._idle_batch_timer.stop()
