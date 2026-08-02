"""破棄済みQtオブジェクトのガードと、アプリ所有シグナルの増加率を固定する。"""

from __future__ import annotations

from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

from omnidesk.ui.file_browser.status_controller import _DirectoryCountSignals
from omnidesk.ui.file_operation_jobs import FileOperationSignals
from omnidesk.ui.media_icon_provider import WorkerSignals
from omnidesk.ui.qt_lifetime import call_if_alive, is_alive, own_by_application
from omnidesk.ui.tab_container import TabContainer
from omnidesk.ui.thumbnail_jobs import ThumbnailJobSignals

# タブ1つが QApplication へ預けるシグナル用オブジェクトの数。
SIGNALS_PER_TAB = 4

_SIGNAL_TYPES = (
    FileOperationSignals,
    ThumbnailJobSignals,
    WorkerSignals,
    _DirectoryCountSignals,
)


def test_is_alive_reports_deleted_objects(qtbot) -> None:
    obj = QObject()
    assert is_alive(obj)

    sip.delete(obj)

    assert not is_alive(obj)


def test_is_alive_treats_none_as_present(qtbot) -> None:
    # 任意の依存を持つ呼び出し側が、その都度 None 判定を書かなくて済むように。
    assert is_alive(None)
    assert is_alive(QObject(), None)


def test_is_alive_ignores_non_qt_objects(qtbot) -> None:
    assert is_alive(object(), "text", 1)


def test_call_if_alive_skips_deleted_objects(qtbot) -> None:
    obj = QObject()
    calls: list[str] = []

    call_if_alive(lambda: calls.append("alive"), obj)
    sip.delete(obj)
    call_if_alive(lambda: calls.append("deleted"), obj)

    assert calls == ["alive"]


def test_own_by_application_reparents_to_the_application(qtbot) -> None:
    obj = own_by_application(QObject())

    assert obj.parent() is QApplication.instance()


def _signal_object_count() -> int:
    app = QApplication.instance()
    assert app is not None
    return sum(len(app.findChildren(signal_type)) for signal_type in _SIGNAL_TYPES)


def test_application_owned_signals_grow_only_per_tab(qtbot, tmp_path: Path) -> None:
    """アプリ所有シグナルの増加率が、タブあたりの既知の数に収まること。

    これらはタブを閉じても解放されない（詳細は own_by_application のdocstring）。
    増加率が上がると、長時間セッションでの単調増加が無視できなくなるため固定する。
    """
    container = TabContainer()
    qtbot.addWidget(container)
    container.open_in_new_tab(tmp_path)
    baseline = _signal_object_count()

    cycles = 5
    for _ in range(cycles):
        container.open_in_new_tab(tmp_path)
        container._close_tab(container._tabs.count() - 1)
    qtbot.wait(200)

    assert _signal_object_count() - baseline == cycles * SIGNALS_PER_TAB
