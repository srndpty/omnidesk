"""レイアウト確定後の選択位置復元を管理するコントローラ。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QAbstractItemView

from ..file_browser_navigation import same_navigation_path, settled_scroll_action

_SelectPath = Callable[[Path, QAbstractItemView.ScrollHint, bool], bool]
_SelectedPath = Callable[[], Path | None]


class SettledScrollController:
    """遅延スクロールの状態とタイマーをタブから分離して保持する。"""

    def __init__(
        self,
        parent: QObject,
        *,
        select_path: _SelectPath,
        selected_path: _SelectedPath,
    ) -> None:
        self._select_path = select_path
        self._selected_path = selected_path
        self._path: Path | None = None
        self._scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        self._retries = 0
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self.apply)

    @property
    def path(self) -> Path | None:
        return self._path

    @path.setter
    def path(self, value: Path | None) -> None:
        self._path = value

    @property
    def scroll_hint(self) -> QAbstractItemView.ScrollHint:
        return self._scroll_hint

    @scroll_hint.setter
    def scroll_hint(self, value: QAbstractItemView.ScrollHint) -> None:
        self._scroll_hint = value

    @property
    def retries(self) -> int:
        return self._retries

    @retries.setter
    def retries(self, value: int) -> None:
        self._retries = value

    def defer(self, path: Path, scroll_hint: QAbstractItemView.ScrollHint) -> None:
        self._path = path
        self._scroll_hint = scroll_hint
        self._retries = 8
        self.schedule()

    def schedule(self) -> None:
        if self._path is None or self._retries <= 0:
            return
        if not self._timer.isActive():
            self._timer.start()

    def apply(self) -> None:
        path = self._path
        retries_before_attempt = self._retries
        if path is None or retries_before_attempt <= 0:
            return
        self._retries -= 1
        path_exists = path.exists()
        can_apply = path_exists and self.can_apply(path)
        action = settled_scroll_action(
            pending_path=path,
            retries_before_attempt=retries_before_attempt,
            path_exists=path_exists,
            can_apply=can_apply,
        )
        if action == "path_missing":
            self._path = None
            return
        if action in {"inactive", "blocked"}:
            return
        self._select_path(path, self._scroll_hint, False)
        self.schedule()

    def can_apply(self, path: Path) -> bool:
        current = self._selected_path()
        if current is None or same_navigation_path(current, path):
            return True
        self._path = None
        self._retries = 0
        return False

    def cancel(self) -> None:
        self._timer.stop()
        self._path = None
        self._retries = 0
