from __future__ import annotations

from pathlib import Path

from omnidesk.ui.file_operation_jobs import FileOperationJob
from omnidesk.ui.file_operations import FileOperationRequest, FileOperationResult


def test_file_operation_job_emits_result(qtbot, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    dest = tmp_path / "dest"
    source.write_text("source", encoding="utf-8")
    job = FileOperationJob(FileOperationRequest([source], dest, "copy"))

    with qtbot.waitSignal(job.signals.finished, timeout=1000) as blocker:
        job.run()

    result = blocker.args[0]
    assert isinstance(result, FileOperationResult)
    assert result.errors == []
    assert (dest / source.name).read_text(encoding="utf-8") == "source"


def test_file_operation_job_can_be_cancelled_before_run(qtbot, tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    dest = tmp_path / "dest"
    source.write_text("source", encoding="utf-8")
    job = FileOperationJob(FileOperationRequest([source], dest, "copy"))
    job.cancel()

    with qtbot.waitSignal(job.signals.finished, timeout=1000) as blocker:
        job.run()

    result = blocker.args[0]
    assert isinstance(result, FileOperationResult)
    assert result.cancelled
    assert not dest.exists()
