from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl

from omnidesk.ui.tab_bar_helpers import (
    local_paths_from_urls,
    tab_drop_action,
    wheel_scroll_request,
)


def test_wheel_scroll_request_uses_dominant_angle_delta() -> None:
    assert wheel_scroll_request(240, 120, 0, 0) == (True, 2)
    assert wheel_scroll_request(0, -360, 0, 0) == (False, 3)
    assert wheel_scroll_request(60, 0, 0, 0) is None


def test_wheel_scroll_request_falls_back_to_pixel_delta() -> None:
    assert wheel_scroll_request(0, 0, 180, 20) == (True, 3)
    assert wheel_scroll_request(0, 0, 0, -120) == (False, 2)
    assert wheel_scroll_request(0, 0, 0, 20) is None


def test_tab_drop_action_uses_ctrl_for_copy() -> None:
    assert tab_drop_action(Qt.KeyboardModifier.NoModifier) == Qt.DropAction.MoveAction
    assert tab_drop_action(Qt.KeyboardModifier.ControlModifier) == Qt.DropAction.CopyAction


def test_local_paths_from_urls_filters_non_local_urls(tmp_path: Path) -> None:
    local = tmp_path / "file.txt"
    urls = [
        QUrl.fromLocalFile(str(local)),
        QUrl("https://example.com/file.txt"),
    ]

    assert local_paths_from_urls(urls) == [local]
