"""Cancellable QRunnable wrappers for file operations."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from .file_operations import FileOperationRequest, execute_file_operation


class FileOperationSignals(QObject):
    finished = pyqtSignal(object)  # FileOperationResult


class FileOperationJob(QRunnable):
    """Run a file operation off the GUI thread with cooperative cancellation."""

    def __init__(self, request: FileOperationRequest) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._request = request
        self._cancelled = False
        self.signals = FileOperationSignals()

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        result = execute_file_operation(self._request, is_cancelled=lambda: self._cancelled)
        self.signals.finished.emit(result)
