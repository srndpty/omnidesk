"""Asynchronous thumbnail loader for image and video files."""

from __future__ import annotations

import logging
from collections import deque
from contextlib import suppress
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QImageReader, QPixmap

from .thumbnail_jobs import CancellationToken

logger = logging.getLogger(__name__)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
except ImportError:  # pragma: no cover - optional dependency
    QAudioOutput = None  # type: ignore[assignment]
    QMediaPlayer = None  # type: ignore[assignment]
    QVideoSink = None  # type: ignore[assignment]


class WorkerSignals(QObject):
    """Signals emitted by background thumbnail jobs."""

    finished = pyqtSignal(str, object, int, int)  # key, QImage | None, edge, generation


class MediaThumbnailProvider(QObject):
    """Coordinates thumbnail extraction and emits results asynchronously."""

    thumbnailReady = pyqtSignal(str, object, int)  # path, QIcon | None, generation

    IMAGE_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".svg",
    }
    VIDEO_EXTENSIONS = {
        ".mp4",
        ".m4v",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".wmv",
        ".mpg",
        ".mpeg",
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        thread_pool = QThreadPool.globalInstance()
        assert thread_pool is not None
        self._thread_pool: QThreadPool = thread_pool
        # これにより、メインスレッドへのシグナルの殺到を防ぎ、UIの応答性を保つ
        self._thread_pool.setMaxThreadCount(4)
        self._image_jobs: dict[str, _ImageJob] = {}
        self._image_tokens: dict[str, CancellationToken] = {}

        # Video job management
        self._video_jobs: dict[str, _VideoJob] = {}
        self._video_queue: deque[tuple[str, Path, int, CancellationToken]] = deque()
        self._active_video_jobs = 0
        self.MAX_CONCURRENT_VIDEO_JOBS = 1

        self._video_support = QMediaPlayer is not None and QVideoSink is not None
        self._video_timeout_ms = 5000

    def set_video_timeout_ms(self, ms: int) -> None:
        if ms >= 1000:
            self._video_timeout_ms = ms

    # ------------------------------------------------------------------
    @property
    def media_extensions(self) -> set[str]:
        return self.IMAGE_EXTENSIONS | self.VIDEO_EXTENSIONS

    @property
    def video_supported(self) -> bool:
        return self._video_support

    def request_thumbnail(
        self,
        path: Path,
        edge: int,
        *,
        result_key: str | None = None,
        token: CancellationToken | None = None,
    ) -> bool:
        suffix = path.suffix.lower()

        # 通知用のキーが指定されていなければ、元のパスをキーとする
        final_key = result_key or str(path)
        token = token or CancellationToken(0)
        if suffix in self.IMAGE_EXTENSIONS:
            if final_key in self._image_jobs:
                return False
            job = _ImageJob(final_key, path, edge, token)
            job.signals.finished.connect(self._handle_image_from_worker)
            self._image_jobs[final_key] = job
            self._image_tokens[final_key] = token
            self._thread_pool.start(job)
            return True
        if suffix in self.VIDEO_EXTENSIONS:
            if not self._video_support:
                return False
            if final_key in self._video_jobs:
                return False

            # Check if we can start immediately
            if self._active_video_jobs < self.MAX_CONCURRENT_VIDEO_JOBS:
                self._start_video_job(final_key, path, edge, token)
            else:
                self._video_queue.append((final_key, path, edge, token))

            return True
        return False

    def cancel_thumbnail(self, key: str) -> None:
        token = self._image_tokens.get(key)
        if token is not None:
            token.cancel()
        self._video_queue = deque(item for item in self._video_queue if item[0] != key)

    def _start_video_job(self, key: str, path: Path, edge: int, token: CancellationToken) -> None:
        job = _VideoJob(key, path, edge, token, timeout_ms=self._video_timeout_ms)
        job.finished.connect(self._on_video_finished)
        self._video_jobs[key] = job
        self._active_video_jobs += 1
        job.start()

    # ------------------------------------------------------------------
    def _handle_image_from_worker(
        self, key: str, image: object, edge: int, generation: int
    ) -> None:
        qimage = image if isinstance(image, QImage) else None
        self._on_image_finished(key, qimage, edge, generation)

    def _on_image_finished(
        self, key: str, image: QImage | None, edge: int, generation: int
    ) -> None:
        self._image_jobs.pop(key, None)
        token = self._image_tokens.pop(key, None)
        if token is not None and token.cancelled:
            return
        icon: QIcon | None = None
        if image is not None and not image.isNull():
            pixmap = QPixmap.fromImage(image)
            icon = QIcon(pixmap)
        else:
            logger.warning("Image thumbnail job finished with no image: %s", key)
        self.thumbnailReady.emit(key, icon, generation)

    def _on_video_finished(self, key: str, icon: QIcon | None, generation: int) -> None:
        job = self._video_jobs.pop(key, None)
        if job is not None:
            job.deleteLater()
        self.thumbnailReady.emit(key, icon, generation)

        self._active_video_jobs -= 1
        self._process_video_queue()

    def _process_video_queue(self) -> None:
        while self._active_video_jobs < self.MAX_CONCURRENT_VIDEO_JOBS and self._video_queue:
            key, path, edge, token = self._video_queue.popleft()
            if token.cancelled:
                continue
            if key in self._video_jobs:  # Should not happen usually
                continue
            self._start_video_job(key, path, edge, token)


class _ImageJob(QRunnable):
    """Runs thumbnail generation for still images in a background thread."""

    def __init__(self, result_key: str, path: Path, edge: int, token: CancellationToken) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._result_key = result_key  # 内部変数名も変更
        self._path = path
        self._edge = edge
        self._token = token
        self.signals = WorkerSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        if self._token.cancelled:
            return
        image = self._load_image(self._path)

        # Scale here in background thread
        if image is not None and not image.isNull() and not self._token.cancelled:
            image = image.scaled(
                self._edge,
                self._edge,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        if not self._token.cancelled:
            self.signals.finished.emit(
                self._result_key,
                image,
                self._edge,
                self._token.generation,
            )

    @staticmethod
    def _load_image(path: Path) -> QImage | None:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return None
        return image


class _VideoJob(QObject):
    """Captures the first available frame of a video file asynchronously."""

    finished = pyqtSignal(str, object, int)

    def __init__(
        self,
        key: str,
        path: Path,
        edge: int,
        token: CancellationToken,
        *,
        timeout_ms: int = 5000,
    ) -> None:
        super().__init__()
        media_player_cls = QMediaPlayer
        video_sink_cls = QVideoSink
        if media_player_cls is None or video_sink_cls is None:
            raise RuntimeError("Qt Multimedia video support is not available")
        self._key = key
        self._path = path
        self._edge = edge
        self._token = token
        self._timeout_ms = timeout_ms
        self._player = media_player_cls(self)
        self._audio = QAudioOutput(self) if QAudioOutput is not None else None
        if self._audio is not None:
            self._audio.setVolume(0.0)
            self._player.setAudioOutput(self._audio)
        self._sink = video_sink_cls(self)
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._handle_frame)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._handle_timeout)
        self._complete = False

    def start(self) -> None:
        if QMediaPlayer is None or QVideoSink is None:
            self.finished.emit(self._key, None, self._token.generation)
            return
        if self._token.cancelled:
            self._finish(None)
            return
        logger.debug("Video thumbnail job started: %s", self._path)
        self._player.setSource(QUrl.fromLocalFile(str(self._path)))
        self._player.setPosition(0)
        self._player.play()
        self._timeout.start(self._timeout_ms)

    def _handle_frame(self, frame) -> None:  # type: ignore[override]
        if self._complete or self._token.cancelled or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            logger.debug("Video thumbnail frame was null: %s", self._path)
            return
        logger.debug("Video thumbnail frame captured: %s", self._path)
        pixmap = QPixmap.fromImage(image).scaled(
            self._edge,
            self._edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._finish(QIcon(pixmap))

    def _handle_timeout(self) -> None:
        if not self._complete:
            logger.warning("Video thumbnail job timed out: %s", self._path)
            self._finish(None)

    def _finish(self, icon: QIcon | None) -> None:
        self._complete = True
        with suppress(TypeError, RuntimeError):
            self._sink.videoFrameChanged.disconnect(self._handle_frame)
        self._timeout.stop()
        self._player.stop()
        if self._audio is not None:
            self._audio.deleteLater()
        self._player.deleteLater()
        logger.debug("Video thumbnail job finished: %s icon=%s", self._path, bool(icon))
        self.finished.emit(
            self._key, None if self._token.cancelled else icon, self._token.generation
        )
        self.deleteLater()
