from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QImage

from omnidesk.ui.media_icon_provider import MediaThumbnailProvider
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
