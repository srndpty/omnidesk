from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QImage

from omnidesk.ui.thumbnail_jobs import (
    CacheLoadJob,
    CacheSaveJob,
    CancellationToken,
    FolderScanJob,
    ThumbnailJobSignals,
    scaled_image,
)


@pytest.fixture
def signals(qtbot) -> ThumbnailJobSignals:
    """ジョブが共有する、GUIスレッド常駐のシグナル置き場。

    ワーカースレッドで QObject を破棄しないよう、シグナルはジョブではなく
    所有者（モデル）が持つ設計になっている。
    """
    return ThumbnailJobSignals()


def test_cancellation_token_records_cancelled_state() -> None:
    token = CancellationToken(7)

    assert not token.cancelled

    token.cancel()

    assert token.cancelled
    assert token.generation == 7


def test_folder_scan_job_finds_first_supported_media(qtbot, signals, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("ignored", encoding="utf-8")
    first_media = tmp_path / "b.jpg"
    first_media.write_bytes(b"not a real image, only suffix matters")
    (tmp_path / "c.png").write_bytes(b"later")

    token = CancellationToken(3)
    job = FolderScanJob("folder-key", tmp_path, {".jpg", ".png"}, token, signals)

    with qtbot.waitSignal(signals.folder_scanned, timeout=1000) as blocker:
        job.run()

    assert blocker.args == ["folder-key", 3, first_media]


def test_cancelled_folder_scan_job_does_not_emit(qtbot, signals, tmp_path: Path) -> None:
    (tmp_path / "a.jpg").write_bytes(b"media")
    token = CancellationToken(3)
    token.cancel()
    job = FolderScanJob("folder-key", tmp_path, {".jpg"}, token, signals)

    with qtbot.assertNotEmitted(signals.folder_scanned, wait=100):
        job.run()


def test_folder_scan_job_emits_none_for_unreadable_or_empty_folder(
    qtbot, signals, tmp_path: Path
) -> None:
    token = CancellationToken(4)
    job = FolderScanJob("folder-key", tmp_path / "missing", {".jpg"}, token, signals)

    with qtbot.waitSignal(signals.folder_scanned, timeout=1000) as blocker:
        job.run()

    assert blocker.args == ["folder-key", 4, None]


def test_folder_scan_job_stops_at_scan_limit(qtbot, signals, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("ignored", encoding="utf-8")
    media_after_limit = tmp_path / "b.jpg"
    media_after_limit.write_bytes(b"media")
    token = CancellationToken(5)
    job = FolderScanJob("folder-key", tmp_path, {".jpg"}, token, signals, scan_limit=1)

    with qtbot.waitSignal(signals.folder_scanned, timeout=1000) as blocker:
        job.run()

    assert blocker.args == ["folder-key", 5, None]


def test_cache_load_job_loads_png_and_emits_image(qtbot, signals, tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.png"
    image = QImage(20, 10, QImage.Format.Format_RGB32)
    image.fill(0x00FF00)
    assert image.save(str(cache_path), "PNG")
    token = CancellationToken(5)
    job = CacheLoadJob("cache-key", cache_path, token, signals)

    with qtbot.waitSignal(signals.cache_loaded, timeout=1000) as blocker:
        job.run()

    key, generation, loaded, is_dir = blocker.args
    assert key == "cache-key"
    assert generation == 5
    assert isinstance(loaded, QImage)
    assert loaded.width() == 20
    assert is_dir is False


def test_cache_load_job_emits_none_for_missing_or_invalid_cache(
    qtbot, signals, tmp_path: Path
) -> None:
    token = CancellationToken(6)
    job = CacheLoadJob("cache-key", tmp_path / "missing.png", token, signals)

    with qtbot.waitSignal(signals.cache_loaded, timeout=1000) as blocker:
        job.run()

    assert blocker.args == ["cache-key", 6, None, False]


def test_cancelled_cache_load_job_does_not_emit(qtbot, signals, tmp_path: Path) -> None:
    token = CancellationToken(7)
    token.cancel()
    job = CacheLoadJob("cache-key", tmp_path / "missing.png", token, signals)

    with qtbot.assertNotEmitted(signals.cache_loaded, wait=100):
        job.run()


def test_cache_load_job_reports_directory_flag(qtbot, signals, tmp_path: Path) -> None:
    """フォルダ用のキャッシュ読み込みかどうかを、シグナルで運ぶ。

    共有シグナルへ移したことで、ジョブごとのラムダで ``is_dir`` を
    束ねられなくなったため、ペイロードに含めている。
    """
    cache_path = tmp_path / "folder.png"
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(0x123456)
    assert image.save(str(cache_path), "PNG")
    job = CacheLoadJob("folder-key", cache_path, CancellationToken(9), signals, is_dir=True)

    with qtbot.waitSignal(signals.cache_loaded, timeout=1000) as blocker:
        job.run()

    assert blocker.args[0] == "folder-key"
    assert blocker.args[3] is True


def test_cache_save_job_writes_png_and_runs_cleanup(tmp_path: Path) -> None:
    cache_path = tmp_path / "nested" / "cache.png"
    image = QImage(12, 12, QImage.Format.Format_RGB32)
    image.fill(0x0000FF)
    cleaned: list[bool] = []
    job = CacheSaveJob(cache_path, image, lambda: cleaned.append(True))

    job.run()

    assert cache_path.exists()
    assert cleaned == [True]


def test_cache_save_job_skips_cleanup_when_commit_rejects(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.png"
    image = QImage(12, 12, QImage.Format.Format_RGB32)
    image.fill(0x0000FF)
    cleaned: list[bool] = []
    commits: list[tuple[Path, Path]] = []
    job = CacheSaveJob(
        cache_path,
        image,
        lambda: cleaned.append(True),
        lambda temp_path, final_path: commits.append((temp_path, final_path)) or False,
    )

    job.run()

    assert not cache_path.exists()
    assert cleaned == []
    assert len(commits) == 1
    assert not commits[0][0].exists()


def test_scaled_image_preserves_null_and_scales_non_null() -> None:
    null_image = QImage()
    image = QImage(100, 50, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFF)

    assert scaled_image(null_image, 16).isNull()

    scaled = scaled_image(image, 20)
    assert scaled.width() <= 20
    assert scaled.height() <= 20
