"""分割レイアウト中の表示保留と、解除時のスクロールを検証する。"""

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QAbstractItemView, QListView

from omnidesk.ui.file_browser.layout_update_guard import LayoutUpdateGuard


def _guard(qtbot):
    view = QListView()
    qtbot.addWidget(view)
    model = QStandardItemModel(view)
    for number in range(100):
        model.appendRow(QStandardItem(str(number)))
    view.setModel(model)
    view.show()
    qtbot.waitExposed(view)
    view.scrollTo(model.index(50, 0))
    assert view.verticalScrollBar().value() > 0
    scrolls = []
    guard = LayoutUpdateGuard(view, lambda index, hint: scrolls.append((index.row(), hint)))
    return view, model, guard, scrolls


def test_guard_waits_for_last_row_and_applies_latest_persistent_scroll(qtbot, monkeypatch):
    view, model, guard, scrolls = _guard(qtbot)
    guard.begin()
    assert not view.updatesEnabled()
    hint = QAbstractItemView.ScrollHint.PositionAtCenter
    assert guard.defer_scroll(model.index(40, 0), hint)
    assert guard.defer_scroll(model.index(50, 0), hint)
    model.removeRow(0)
    monkeypatch.setattr(view, "visualRect", lambda index: QRect())
    guard._try_finish()
    assert not view.updatesEnabled()
    assert scrolls == []

    monkeypatch.setattr(view, "visualRect", lambda index: QRect(0, 0, 10, 10))
    guard._try_finish()
    assert scrolls == [(49, hint)]
    assert view.updatesEnabled()
    assert not guard.defer_scroll(model.index(10, 0), hint)


def test_navigation_discards_pending_scroll_and_restores_updates(qtbot):
    view, model, guard, scrolls = _guard(qtbot)
    guard.begin()
    guard.defer_scroll(model.index(50, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
    guard.finish(apply_scroll=False)
    assert view.updatesEnabled()
    assert scrolls == []
    assert not guard._timer.isActive()
    assert not guard._deadline.isActive()


def test_guard_does_not_enable_updates_disabled_by_another_owner(qtbot):
    view, _, guard, _ = _guard(qtbot)
    view.setUpdatesEnabled(False)
    guard.begin()
    guard.finish()
    assert not view.updatesEnabled()


def test_guard_ignores_top_and_hidden_views(qtbot):
    view, model, guard, _ = _guard(qtbot)
    view.verticalScrollBar().setValue(0)
    guard.begin()
    assert view.updatesEnabled()
    assert not guard._timer.isActive()
    view.scrollTo(model.index(50, 0))
    view.hide()
    guard.begin()
    assert view.updatesEnabled()
    assert not guard._timer.isActive()


def test_guard_recovers_on_timeout_with_a_deleted_scroll_target(qtbot):
    view, model, guard, scrolls = _guard(qtbot)
    guard.begin()
    guard.defer_scroll(model.index(50, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
    model.removeRow(50)
    guard._expire()
    assert view.updatesEnabled()
    assert scrolls == []
    guard.finish()
    assert view.updatesEnabled()
