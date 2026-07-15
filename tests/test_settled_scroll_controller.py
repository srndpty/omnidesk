"""レイアウト確定後スクロールコントローラの部品テスト。"""

from pathlib import Path

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QAbstractItemView

from omnidesk.ui.file_browser.settled_scroll_controller import SettledScrollController


def test_settled_scroll_controller_applies_and_reschedules(qtbot, tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("target", encoding="utf-8")
    selected: list[tuple[Path, QAbstractItemView.ScrollHint, bool]] = []
    parent = QObject()
    controller = SettledScrollController(
        parent,
        select_path=lambda path, hint, defer: selected.append((path, hint, defer)) or True,
        selected_path=lambda: None,
    )

    controller.defer(target, QAbstractItemView.ScrollHint.PositionAtCenter)
    controller.apply()

    assert selected == [(target, QAbstractItemView.ScrollHint.PositionAtCenter, False)]
    assert controller.path == target
    assert controller.retries == 7
    controller.cancel()


def test_settled_scroll_controller_stops_for_new_user_selection(
    qtbot,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    selected_by_user = tmp_path / "selected.txt"
    target.write_text("target", encoding="utf-8")
    selected_by_user.write_text("selected", encoding="utf-8")
    selected: list[Path] = []
    parent = QObject()
    controller = SettledScrollController(
        parent,
        select_path=lambda path, _hint, _defer: selected.append(path) or True,
        selected_path=lambda: selected_by_user,
    )

    controller.path = target
    controller.retries = 3
    controller.apply()

    assert selected == []
    assert controller.path is None
    assert controller.retries == 0
