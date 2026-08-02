"""GUIスレッドの停止を検出し、そのときのスタックを記録するウォッチドッグ。

フリーズは「落ちない」ぶんクラッシュより手掛かりが残らない。イベントループが
一定時間応答しなくなったら、全スレッドのスタックをクラッシュログへ出しておく。
これがあれば、再発したときに原因箇所を直接特定できる。

判定は監視スレッド側だけで行い、GUIスレッドには「生きている印」を更新する
軽いタイマーしか置かない。ウォッチドッグ自身がフリーズの原因にならないようにする。
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import threading
import time
import traceback
from contextlib import suppress
from typing import TextIO

from PyQt6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)

DEFAULT_HEARTBEAT_MS = 1000
DEFAULT_STALL_SECONDS = 5.0


class EventLoopWatchdog(QObject):
    """GUIスレッドの応答が途切れたらスタックダンプを出す。"""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        heartbeat_ms: int = DEFAULT_HEARTBEAT_MS,
        stall_seconds: float = DEFAULT_STALL_SECONDS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._stream = stream
        self._stall_seconds = max(1.0, stall_seconds)
        self._last_beat = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reported = False

        self._timer = QTimer(self)
        self._timer.setInterval(max(100, heartbeat_ms))
        self._timer.timeout.connect(self._beat)
        self._thread: threading.Thread | None = None

    # 監視スレッドの終了を待つ上限。ちょうどスタックダンプを書いている最中に
    # 終了処理が進むと、クラッシュログのクローズやPython終了と競合するため待つ。
    JOIN_TIMEOUT_SECONDS = 1.0

    def start(self) -> None:
        if self._thread is not None:
            # 二重に呼ばれても監視スレッドを増やさない。
            return
        self._timer.start()
        self._thread = threading.Thread(
            target=self._watch,
            name="omnidesk-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._timer.stop()

        thread = self._thread
        self._thread = None
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout=self.JOIN_TIMEOUT_SECONDS)
        if thread.is_alive():
            logger.warning("ウォッチドッグの監視スレッドが時間内に終了しませんでした")

    # ------------------------------------------------------------------
    def _beat(self) -> None:
        """GUIスレッドから呼ばれる「生きている印」の更新。"""
        with self._lock:
            self._last_beat = time.monotonic()
            self._reported = False

    def stalled_seconds(self) -> float:
        with self._lock:
            return time.monotonic() - self._last_beat

    def _watch(self) -> None:
        # 判定間隔は停止判定のしきい値より短くする。
        interval = min(1.0, self._stall_seconds / 2)
        while not self._stop.wait(interval):
            self._check_once()

    def _check_once(self) -> None:
        with self._lock:
            stalled = time.monotonic() - self._last_beat
            already_reported = self._reported
            if stalled >= self._stall_seconds and not already_reported:
                self._reported = True
            else:
                return
        self.report_stall(stalled)

    def report_stall(self, stalled_seconds: float) -> None:
        """停止を検出したときの記録。復帰までに1回だけ出す。"""
        logger.error(
            "GUIスレッドが応答していません: stalled_seconds=%.1f",
            stalled_seconds,
        )
        if self._stream is None:
            return
        try:
            self._stream.write(
                f"\n--- OmniDesk event loop stalled for {stalled_seconds:.1f}s ---\n"
            )
            self._stream.write(_python_thread_stacks())
            # ネイティブ側で止まっている場合はPythonのスタックに出ないため、
            # 実ファイルへ書ける場合だけ faulthandler も併用する。
            with suppress(OSError, ValueError, AttributeError):
                self._stream.flush()
                faulthandler.dump_traceback(file=self._stream, all_threads=True)
            self._stream.flush()
        except (OSError, ValueError):
            logger.exception("ウォッチドッグのスタックダンプを書き出せません")


def _python_thread_stacks() -> str:
    """全スレッドのPythonスタックを文字列にまとめる。

    ``faulthandler`` は実ファイルディスクリプタを必要とするため、ここは
    出力先を選ばない純Pythonの経路として用意している。今回対象としている
    フリーズはPythonコード側で起きるので、実用上はこちらが主な手掛かりになる。
    """
    names = {thread.ident: thread.name for thread in threading.enumerate()}
    lines: list[str] = []
    for thread_id, frame in sys._current_frames().items():
        lines.append(f"\nThread {names.get(thread_id, '?')} ({thread_id}):\n")
        lines.extend(traceback.format_stack(frame))
    return "".join(lines)
