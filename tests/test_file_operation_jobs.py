from __future__ import annotations

from omnidesk.ui.file_operation_jobs import FileOperationJob, FileOperationSignals
from omnidesk.ui.file_operations import FileOperationRequest


def test_file_operation_job_records_cancelled_state() -> None:
    job = FileOperationJob(FileOperationRequest([], None, "delete"), FileOperationSignals(), 1)

    assert not job.cancelled

    job.cancel()

    assert job.cancelled
