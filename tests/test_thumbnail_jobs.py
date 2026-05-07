from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QImage

from omnidesk.ui.thumbnail_jobs import (
    CacheLoadJob,
    CacheSaveJob,
    CancellationToken,
    FolderScanJob,
    scaled_image,
)


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


def test_folder_scan_job_emits_none_for_unreadable_or_empty_folder(qtbot, tmp_path: Path) -> None:
    token = CancellationToken(4)
    job = FolderScanJob("folder-key", tmp_path / "missing", {".jpg"}, token)

    with qtbot.waitSignal(job.signals.found, timeout=1000) as blocker:
        job.run()

    assert blocker.args == ["folder-key", 4, None]


def test_cache_load_job_loads_png_and_emits_image(qtbot, tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.png"
    image = QImage(20, 10, QImage.Format.Format_RGB32)
    image.fill(0x00FF00)
    assert image.save(str(cache_path), "PNG")
    token = CancellationToken(5)
    job = CacheLoadJob("cache-key", cache_path, token)

    with qtbot.waitSignal(job.signals.loaded, timeout=1000) as blocker:
        job.run()

    key, generation, loaded = blocker.args
    assert key == "cache-key"
    assert generation == 5
    assert isinstance(loaded, QImage)
    assert loaded.width() == 20


def test_cache_load_job_emits_none_for_missing_or_invalid_cache(qtbot, tmp_path: Path) -> None:
    token = CancellationToken(6)
    job = CacheLoadJob("cache-key", tmp_path / "missing.png", token)

    with qtbot.waitSignal(job.signals.loaded, timeout=1000) as blocker:
        job.run()

    assert blocker.args == ["cache-key", 6, None]


def test_cancelled_cache_load_job_does_not_emit(qtbot, tmp_path: Path) -> None:
    token = CancellationToken(7)
    token.cancel()
    job = CacheLoadJob("cache-key", tmp_path / "missing.png", token)

    with qtbot.assertNotEmitted(job.signals.loaded, wait=100):
        job.run()


def test_cache_save_job_writes_png_and_runs_cleanup(tmp_path: Path) -> None:
    cache_path = tmp_path / "nested" / "cache.png"
    image = QImage(12, 12, QImage.Format.Format_RGB32)
    image.fill(0x0000FF)
    cleaned: list[bool] = []
    job = CacheSaveJob(cache_path, image, lambda: cleaned.append(True))

    job.run()

    assert cache_path.exists()
    assert cleaned == [True]


def test_scaled_image_preserves_null_and_scales_non_null() -> None:
    null_image = QImage()
    image = QImage(100, 50, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFF)

    assert scaled_image(null_image, 16).isNull()

    scaled = scaled_image(image, 20)
    assert scaled.width() <= 20
    assert scaled.height() <= 20
