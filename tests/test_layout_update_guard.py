"""分割レイアウト中の表示保留と、解除時のスクロールを検証する。"""

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QKeyEvent, QStandardItem, QStandardItemModel, QWheelEvent
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QListView, QVBoxLayout, QWidget

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
    assert guard.active
    hint = QAbstractItemView.ScrollHint.PositionAtCenter
    assert guard.defer_scroll(model.index(40, 0), hint)
    assert guard.defer_scroll(model.index(50, 0), hint)
    model.removeRow(0)
    monkeypatch.setattr(view, "visualRect", lambda index: QRect())
    guard._try_finish()
    assert guard.active
    assert scrolls == []

    monkeypatch.setattr(view, "visualRect", lambda index: QRect(0, 0, 10, 10))
    guard._try_finish()
    assert scrolls == [(49, hint)]
    assert not guard.active
    assert not guard.defer_scroll(model.index(10, 0), hint)


def test_guard_paints_the_frozen_image_even_when_the_parent_repaints(qtbot):
    """保留中に親が再描画しても、一覧の領域は保留開始時の画像のまま。

    描画を止める方式では、親が背景を塗ったあとにビューの描画が飛ばされ、
    一覧の領域が空に見えていた。
    """
    parent = QWidget()
    qtbot.addWidget(parent)
    layout = QVBoxLayout(parent)
    view = QListView(parent)
    layout.addWidget(view)
    model = QStandardItemModel(view)
    for number in range(100):
        model.appendRow(QStandardItem(f"item {number}"))
    view.setModel(model)
    parent.show()
    qtbot.waitExposed(parent)
    view.scrollTo(model.index(50, 0), QAbstractItemView.ScrollHint.PositionAtTop)
    qtbot.wait(50)
    viewport = view.viewport()

    def viewport_area_after_parent_repaint():
        # 親ごと描き直したときに、ビューポートの領域に何が描かれるか。
        area = viewport.geometry().translated(view.pos())
        return parent.grab().toImage().copy(area)

    before = viewport_area_after_parent_repaint()
    guard = LayoutUpdateGuard(view, lambda index, hint: None)

    guard.begin()
    # 表示中の行を消し、スクロール位置も動かしてから親ごと描き直させる。
    model.removeRows(0, 60)
    view.verticalScrollBar().setValue(0)
    during = viewport_area_after_parent_repaint()

    assert during == before
    guard.finish(apply_scroll=False)
    assert viewport_area_after_parent_repaint() != before


def test_navigation_discards_pending_scroll_and_stops_freezing(qtbot):
    view, model, guard, scrolls = _guard(qtbot)
    guard.begin()
    guard.defer_scroll(model.index(50, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
    guard.finish(apply_scroll=False)
    assert not guard.active
    assert guard._frozen == {}
    assert scrolls == []
    assert not guard._timer.isActive()
    assert not guard._deadline.isActive()


def test_guard_leaves_updates_disabled_by_another_owner_alone(qtbot):
    view, _, guard, _ = _guard(qtbot)
    view.setUpdatesEnabled(False)
    guard.begin()
    assert not guard.active
    guard.finish()
    assert not view.updatesEnabled()


def test_guard_ignores_top_and_hidden_views(qtbot):
    view, model, guard, _ = _guard(qtbot)
    view.verticalScrollBar().setValue(0)
    guard.begin()
    assert not guard.active
    assert not guard._timer.isActive()
    view.scrollTo(model.index(50, 0))
    view.hide()
    guard.begin()
    assert not guard.active
    assert not guard._timer.isActive()


def test_guard_recovers_on_timeout_with_a_deleted_scroll_target(qtbot):
    view, model, guard, scrolls = _guard(qtbot)
    guard.begin()
    guard.defer_scroll(model.index(50, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
    model.removeRow(50)
    guard._expire()
    assert not guard.active
    assert guard._frozen == {}
    assert scrolls == []
    guard.finish()
    assert not guard.active


def _wheel(view: QListView, delta_y: int) -> QWheelEvent:
    center = QPointF(view.viewport().rect().center())
    return QWheelEvent(
        center,
        QPointF(view.viewport().mapToGlobal(center.toPoint())),
        QPoint(0, 0),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_resize_while_frozen_stops_freezing_but_keeps_the_scroll_pending(qtbot):
    view, model, guard, scrolls = _guard(qtbot)
    hint = QAbstractItemView.ScrollHint.PositionAtCenter
    guard.begin()
    guard.defer_scroll(model.index(50, 0), hint)
    assert guard.freezing

    old_size = view.viewport().size()
    view.resize(view.width() + 40, view.height() + 40)
    qtbot.waitUntil(lambda: view.viewport().size() != old_size)
    view.viewport().grab()  # 描画イベントを確実に発生させる

    assert not guard.freezing
    assert guard.active
    guard.finish()
    assert scrolls == [(50, hint)]


def test_wheel_during_freeze_is_replayed_after_the_pending_scroll(qtbot):
    """保留中のホイールは、選択復元のスクロールより後に反映される。"""
    view, model, guard, _ = _guard(qtbot)
    bar = view.verticalScrollBar()
    restored: list[int] = []

    def restore(index, hint) -> None:
        view.scrollTo(index, hint)
        restored.append(bar.value())

    guard._scroll = restore
    guard.begin()
    guard.defer_scroll(model.index(50, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
    frozen_value = bar.value()

    QApplication.sendEvent(view.viewport(), _wheel(view, -120))

    # 凍結中は、丸められているかもしれない値を基準に動かさない。
    assert bar.value() == frozen_value
    guard.finish()
    # 復元した位置から、ホイールのぶんだけ下へ進んでいる。
    assert bar.value() > restored[0]


def test_navigation_discards_input_queued_during_freeze(qtbot):
    view, _, guard, _ = _guard(qtbot)
    bar = view.verticalScrollBar()
    guard.begin()
    before = bar.value()
    QApplication.sendEvent(view.viewport(), _wheel(view, -120))
    guard.finish(apply_scroll=False)
    assert bar.value() == before


def test_key_during_freeze_is_replayed_after_finish(qtbot):
    view, model, guard, _ = _guard(qtbot)
    view.setCurrentIndex(model.index(50, 0))
    guard.begin()

    QApplication.sendEvent(
        view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    )

    assert view.currentIndex().row() == 50
    guard.finish()
    assert view.currentIndex().row() == 51


def test_click_on_the_frozen_image_is_ignored(qtbot):
    view, model, guard, _ = _guard(qtbot)
    view.setCurrentIndex(model.index(50, 0))
    guard.begin()
    point = view.visualRect(model.index(52, 0)).center()

    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    guard.finish()

    assert view.currentIndex().row() == 50
    # 保留が終われば、クリックは通常どおり効く。
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert view.currentIndex().row() == 52


def test_destroying_the_view_while_frozen_is_safe(qtbot):
    view, _, guard, _ = _guard(qtbot)
    guard.begin()
    assert guard.freezing
    view.deleteLater()
    qtbot.wait(200)
