"""Cancellable QRunnable wrappers for file operations."""

from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from .file_operations import FileOperationRequest, execute_file_operation

logger = logging.getLogger(__name__)


class FileOperationSignals(QObject):
    """ファイル操作ジョブが共有する、GUIスレッド常駐のシグナル置き場。

    ``QRunnable`` ごとに ``QObject`` を持たせると、``setAutoDelete(True)`` に
    より生成スレッド以外（ワーカースレッド）で ``QObject`` が破棄される。
    Qt が禁じている操作で、まれにネイティブクラッシュを起こす。

    完了通知の宛先は ``job_id`` で振り分ける。
    """

    finished = pyqtSignal(int, object)  # job_id, FileOperationResult


class FileOperationJob(QRunnable):
    """Run a file operation off the GUI thread with cooperative cancellation."""

    def __init__(
        self,
        request: FileOperationRequest,
        signals: FileOperationSignals,
        job_id: int,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._request = request
        self._cancelled = False
        self.signals = signals
        self.job_id = job_id
        # 「OKを押してから反映されるまで」の内訳を切り分けるための計測。
        # 待ち行列で待たされているのか、実処理そのものが遅いのかを分ける。
        self._queued_at = time.monotonic()

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        # Cancellation is cooperative and checked between top-level sources.
        # A single shutil copy/move call may not be interruptible.
        started_at = time.monotonic()
        result = execute_file_operation(self._request, is_cancelled=lambda: self._cancelled)
        finished_at = time.monotonic()
        # ファイル操作はユーザー操作ごとに1回しか起きないのでINFOで残す。
        # queue_wait_ms が大きい: 共有スレッドプールで順番待ちしている。
        # work_ms が大きい: 実処理（削除ならWindowsシェルのゴミ箱移動）が遅い。
        logger.info(
            "ファイル操作ジョブ: job_id=%d mode=%s sources=%d queue_wait_ms=%d work_ms=%d",
            self.job_id,
            self._request.mode,
            len(self._request.sources),
            round((started_at - self._queued_at) * 1000),
            round((finished_at - started_at) * 1000),
        )
        self.signals.finished.emit(self.job_id, result)
