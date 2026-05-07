from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QImage

from omnidesk.ui.media_icon_provider import MediaThumbnailProvider, _ImageJob
from omnidesk.ui.thumbnail_jobs import CancellationToken


def _save_image(path: Path, *, size: int = 256) -> None:
    image = QImage(size, size, QImage.Format.Format_RGB32)
    image.fill(0xFF0000)
    assert image.save(str(path))


def test_image_thumbnail_emits_scaled_icon(qtbot, tmp_path: Path) -> None:
    image_path = tmp_path / "large.png"
    _save_image(image_path, size=1200)

    provider = MediaThumbnailProvider()
    edge = 100

    with qtbot.waitSignal(provider.thumbnailReady, timeout=5000) as blocker:
        assert provider.request_thumbnail(image_path, edge, result_key="image-key")

    key, icon, generation = blocker.args
    assert key == "image-key"
    assert generation == 0
    assert icon is not None

    pixmap = icon.pixmap(edge, edge)
    assert not pixmap.isNull()
    assert pixmap.width() <= edge
    assert pixmap.height() <= edge


def test_cancelled_image_thumbnail_does_not_emit(qtbot, tmp_path: Path) -> None:
    image_path = tmp_path / "cancelled.png"
    _save_image(image_path, size=1600)

    provider = MediaThumbnailProvider()
    token = CancellationToken(42)

    with qtbot.assertNotEmitted(provider.thumbnailReady, wait=1000):
        assert provider.request_thumbnail(
            image_path,
            100,
            result_key="cancelled-key",
            token=token,
        )
        token.cancel()


def test_duplicate_result_key_is_rejected(tmp_path: Path) -> None:
    image_path = tmp_path / "duplicate.png"
    _save_image(image_path)

    provider = MediaThumbnailProvider()
    provider._image_jobs["same-key"] = object()

    assert not provider.request_thumbnail(image_path, 100, result_key="same-key")


def test_unsupported_thumbnail_extension_is_rejected(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("not media", encoding="utf-8")

    provider = MediaThumbnailProvider()

    assert not provider.request_thumbnail(file_path, 100, result_key="text-key")


def test_cancel_thumbnail_cancels_image_token_and_removes_queued_video() -> None:
    provider = MediaThumbnailProvider()
    token = CancellationToken(5)
    provider._image_tokens["image-key"] = token
    provider._video_queue = [
        ("drop-me", Path("queued.mp4"), 100, CancellationToken(1)),
        ("keep-me", Path("other.mp4"), 100, CancellationToken(2)),
    ]

    provider.cancel_thumbnail("drop-me")
    provider.cancel_thumbnail("image-key")

    assert token.cancelled
    assert [item[0] for item in provider._video_queue] == ["keep-me"]


def test_on_image_finished_suppresses_cancelled_token(qtbot) -> None:
    provider = MediaThumbnailProvider()
    token = CancellationToken(6)
    token.cancel()
    provider._image_tokens["cancelled"] = token

    with qtbot.assertNotEmitted(provider.thumbnailReady, wait=100):
        provider._on_image_finished("cancelled", QImage(), 100, token.generation)

    assert "cancelled" not in provider._image_tokens


def test_process_video_queue_skips_cancelled_and_duplicate_jobs(monkeypatch) -> None:
    provider = MediaThumbnailProvider()
    started: list[str] = []
    duplicate = CancellationToken(1)
    cancelled = CancellationToken(2)
    cancelled.cancel()
    ready = CancellationToken(3)
    provider._video_jobs["duplicate"] = object()
    provider._video_queue = [
        ("cancelled", Path("cancelled.mp4"), 100, cancelled),
        ("duplicate", Path("duplicate.mp4"), 100, duplicate),
        ("ready", Path("ready.mp4"), 100, ready),
    ]
    monkeypatch.setattr(
        provider,
        "_start_video_job",
        lambda key, path, edge, token: started.append(key),
    )

    provider._process_video_queue()

    assert started == ["ready"]
    assert provider._video_queue == []


def test_on_video_finished_starts_next_queued_job(monkeypatch, qtbot) -> None:
    provider = MediaThumbnailProvider()
    started: list[str] = []
    provider._active_video_jobs = 1
    provider._video_jobs["done"] = None
    provider._video_queue = [("next", Path("next.mp4"), 100, CancellationToken(4))]
    monkeypatch.setattr(
        provider,
        "_start_video_job",
        lambda key, path, edge, token: started.append(key),
    )

    with qtbot.waitSignal(provider.thumbnailReady, timeout=1000) as blocker:
        provider._on_video_finished("done", None, 9)

    assert blocker.args == ["done", None, 9]
    assert provider._active_video_jobs == 0
    assert started == ["next"]


def test_image_job_load_image_success_and_failure(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    bad_path = tmp_path / "bad.png"
    _save_image(image_path, size=24)
    bad_path.write_text("not an image", encoding="utf-8")

    loaded = _ImageJob._load_image(image_path)

    assert loaded is not None
    assert not loaded.isNull()
    assert _ImageJob._load_image(bad_path) is None


def test_image_job_run_emits_scaled_image(qtbot, tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    _save_image(image_path, size=200)
    token = CancellationToken(11)
    job = _ImageJob("job-key", image_path, 40, token)

    with qtbot.waitSignal(job.signals.finished, timeout=1000) as blocker:
        job.run()

    key, image, edge, generation = blocker.args
    assert key == "job-key"
    assert edge == 40
    assert generation == 11
    assert image.width() <= 40
    assert image.height() <= 40


def test_image_job_run_does_not_emit_when_cancelled(qtbot, tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    _save_image(image_path, size=200)
    token = CancellationToken(12)
    token.cancel()
    job = _ImageJob("job-key", image_path, 40, token)

    with qtbot.assertNotEmitted(job.signals.finished, wait=100):
        job.run()
