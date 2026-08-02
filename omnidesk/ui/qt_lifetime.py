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
from PyQt6.QtCore import QTimer

T = TypeVar("T")


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
