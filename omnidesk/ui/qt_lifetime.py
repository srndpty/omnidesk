"""破棄済みQtオブジェクトへのコールバックを防ぐ小さなヘルパー。

非同期ジョブの完了通知や ``QTimer.singleShot`` は、対象のウィジェットが
既に ``deleteLater()`` されたあとに届くことがある。その状態でPython側の
ラッパーへ触ると ``RuntimeError: wrapped C/C++ object ... has been deleted``
になり、Qtのスロット内で送出されるため復帰しづらい。

``ColumnBrowser`` が持っていた ``sip.isdeleted()`` ガードをここへ切り出し、
ファイルブラウザ側からも同じ判定を使えるようにする。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication

T = TypeVar("T")
_SignalsT = TypeVar("_SignalsT", bound=QObject)


def own_by_application(obj: _SignalsT) -> _SignalsT:
    """``QObject`` の寿命を ``QApplication`` に預ける。

    ワーカースレッドで動くジョブが参照するシグナル用 ``QObject`` は、
    ジョブより先に壊れてはならない。タブやモデルの子にすると、タブを閉じた
    瞬間にシグナルが破棄され、実行中のジョブが emit した時点で
    ``RuntimeError: wrapped C/C++ object ... has been deleted`` になる。

    ``QApplication`` を親にすると、GUIスレッド上でアプリ終了時にまとめて
    破棄されるため、この競合が起きない。

    既知の制限: ここへ預けたオブジェクトはアプリ終了まで残る。タブを閉じても
    解放されないため、開閉を繰り返すと単調増加する（実測でタブ1回の開閉あたり
    4個・約1.2KB。100回で400個・約58KB）。受け手が破棄された接続はQtが自動で
    外すので動作上の害はないが、有限ではない。解消するにはアプリ単位の
    シグナルハブへ集約する必要があり、その場合は ``job_id`` をアプリ全体で
    一意にしたうえで、タブ間のクロストークを各ハンドラで弾く設計が要る。
    増加率は ``tests/test_qt_lifetime.py`` で固定している。
    """
    app = QApplication.instance()
    if app is not None:
        obj.setParent(app)
    return obj


def is_alive(*objects: Any) -> bool:
    """渡したQtオブジェクトがすべて生きていれば ``True``。

    ``None`` は「対象なし」として生存扱いにする（任意の依存を持つ
    呼び出し側が、その都度 ``None`` 判定を書かなくて済むようにするため）。
    """
    for obj in objects:
        if obj is None:
            continue
        try:
            if sip.isdeleted(obj):
                return False
        except TypeError:
            # sipが管理していないPythonオブジェクトは寿命管理の対象外。
            continue
    return True


def call_if_alive(callback: Callable[[], None], *objects: Any) -> None:
    """対象がすべて生きているときだけ ``callback`` を呼ぶ。"""
    if is_alive(*objects):
        callback()


def single_shot_if_alive(msec: int, callback: Callable[[], None], *objects: Any) -> None:
    """``QTimer.singleShot`` を、発火時の生存チェック付きで予約する。"""

    def run_if_alive() -> None:
        call_if_alive(callback, *objects)

    QTimer.singleShot(msec, run_if_alive)
