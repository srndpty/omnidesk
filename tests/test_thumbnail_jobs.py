from __future__ import annotations

from pathlib import Path

from omnidesk.ui.thumbnail_jobs import CancellationToken, FolderScanJob


def test_cancellation_token_records_cancelled_state() -> None:
    token = CancellationToken(7)

    assert not token.cancelled

    token.cancel()

    assert token.cancelled
    assert token.generation == 7


def test_folder_scan_job_finds_first_supported_media(qtbot, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("ignored", encoding="utf-8")
    first_media = tmp_path / "b.jpg"
    first_media.write_bytes(b"not a real image, only suffix matters")
    (tmp_path / "c.png").write_bytes(b"later")

    token = CancellationToken(3)
    job = FolderScanJob("folder-key", tmp_path, {".jpg", ".png"}, token)

    with qtbot.waitSignal(job.signals.found, timeout=1000) as blocker:
        job.run()

    assert blocker.args == ["folder-key", 3, first_media]


def test_cancelled_folder_scan_job_does_not_emit(qtbot, tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"media")
    token = CancellationToken(3)
    token.cancel()
    job = FolderScanJob("folder-key", tmp_path, {".jpg"}, token)

    with qtbot.assertNotEmitted(job.signals.found, wait=100):
        job.run()
