"""Cancellable QRunnable wrappers for file operations."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from .file_operations import FileOperationRequest, execute_file_operation


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

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        # Cancellation is cooperative and checked between top-level sources.
        # A single shutil copy/move call may not be interruptible.
        result = execute_file_operation(self._request, is_cancelled=lambda: self._cancelled)
        self.signals.finished.emit(self.job_id, result)
