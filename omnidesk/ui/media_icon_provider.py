"""Asynchronous thumbnail loader for image and video files."""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from PyQt6.QtCore import QObject, Qt, QUrl, QRunnable, QThreadPool, pyqtSignal, QTimer
from PyQt6.QtGui import QImage, QImageReader, QIcon, QPixmap

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
except ImportError:  # pragma: no cover - optional dependency
    QAudioOutput = None  # type: ignore[assignment]
    QMediaPlayer = None  # type: ignore[assignment]
    QVideoSink = None  # type: ignore[assignment]



class WorkerSignals(QObject):
    """Signals emitted by background thumbnail jobs."""

    finished = pyqtSignal(str, object, int)  # key, QImage | None, edge


class MediaThumbnailProvider(QObject):
    """Coordinates thumbnail extraction and emits results asynchronously."""

    thumbnailReady = pyqtSignal(str, object)  # path, QIcon | None

    IMAGE_EXTENSIONS = {
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg",
    }
    VIDEO_EXTENSIONS = {
        ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".mpg", ".mpeg",
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread_pool = QThreadPool.globalInstance()
        self._image_jobs: Dict[str, _ImageJob] = {}
        self._video_jobs: Dict[str, _VideoJob] = {}
        self._video_support = QMediaPlayer is not None and QVideoSink is not None

    # ------------------------------------------------------------------
    @property
    def media_extensions(self) -> set[str]:
        return self.IMAGE_EXTENSIONS | self.VIDEO_EXTENSIONS

    @property
    def video_supported(self) -> bool:
        return self._video_support

    def request_thumbnail(self, path: Path, edge: int) -> bool:
        suffix = path.suffix.lower()
        key = str(path)
        if suffix in self.IMAGE_EXTENSIONS:
            if key in self._image_jobs:
                # print(f"[MediaThumbnailProvider] image job already queued: {key}", flush=True)
                return False
            # print(f"[MediaThumbnailProvider] queue image job: {key} edge={edge}", flush=True)
            job = _ImageJob(key, path, edge)
            job.signals.finished.connect(self._handle_image_from_worker)
            self._image_jobs[key] = job
            self._thread_pool.start(job)
            return True
        if suffix in self.VIDEO_EXTENSIONS:
            if not self._video_support:
                # print(f"[MediaThumbnailProvider] video support unavailable for: {key}", flush=True)
                return False
            if key in self._video_jobs:
                # print(f"[MediaThumbnailProvider] video job already queued: {key}", flush=True)
                return False
            # print(f"[MediaThumbnailProvider] start video job: {key} edge={edge}", flush=True)
            job = _VideoJob(path, edge)
            job.finished.connect(self._on_video_finished)
            self._video_jobs[key] = job
            job.start()
            return True
        return False

    # ------------------------------------------------------------------
    def _handle_image_from_worker(self, key: str, image: object, edge: int) -> None:
        qimage = image if isinstance(image, QImage) else None
        self._on_image_finished(key, qimage, edge)

    def _on_image_finished(self, key: str, image: QImage | None, edge: int) -> None:
        icon: QIcon | None = None
        if image is not None and not image.isNull():
            pixmap = QPixmap.fromImage(image).scaled(
                edge,
                edge,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            icon = QIcon(pixmap)
            # print(f"[MediaThumbnailProvider] created pixmap for {key} size={pixmap.width()}x{pixmap.height()}", flush=True)
        else:
            print(f"[MediaThumbnailProvider] image job finished with no image: {key}", flush=True)
        # print(f"[MediaThumbnailProvider] image job finished: {key} icon={'Y' if icon else 'N'}", flush=True)
        self._image_jobs.pop(key, None)
        self.thumbnailReady.emit(key, icon)

    def _on_video_finished(self, key: str, icon: QIcon | None) -> None:
        job = self._video_jobs.pop(key, None)
        if job is not None:
            job.deleteLater()
        # print(f"[MediaThumbnailProvider] video job finished: {key} icon={'Y' if icon else 'N'}", flush=True)
        self.thumbnailReady.emit(key, icon)


class _ImageJob(QRunnable):
    """Runs thumbnail generation for still images in a background thread."""

    def __init__(self, key: str, path: Path, edge: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._key = key
        self._path = path
        self._edge = edge
        self.signals = WorkerSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        # print(f"[_ImageJob] processing image: {self._path}", flush=True)
        image = self._load_image(self._path)
        has_image = isinstance(image, QImage) and not image.isNull() if image is not None else False
        # print(f"[_ImageJob] emitting result for {self._path} image={'Y' if has_image else 'N'}", flush=True)
        self.signals.finished.emit(self._key, image, self._edge)

    @staticmethod
    def _load_image(path: Path) -> QImage | None:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            # print(f"[_ImageJob] failed to read image: {path}", flush=True)
            return None
        # print(f"[_ImageJob] image read success: {path} size={image.width()}x{image.height()}", flush=True)
        return image


class _VideoJob(QObject):
    """Captures the first available frame of a video file asynchronously."""

    finished = pyqtSignal(str, object)

    def __init__(self, path: Path, edge: int) -> None:
        super().__init__()
        self._path = path
        self._edge = edge
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self) if QAudioOutput is not None else None
        if self._audio is not None:
            self._audio.setVolume(0.0)
            self._player.setAudioOutput(self._audio)
        self._sink = QVideoSink(self)
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._handle_frame)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._handle_timeout)
        self._complete = False

    def start(self) -> None:
        if QMediaPlayer is None or QVideoSink is None:
            self.finished.emit(str(self._path), None)
            return
        print(f"[_VideoJob] start: {self._path}", flush=True)
        self._player.setSource(QUrl.fromLocalFile(str(self._path)))
        self._player.setPosition(0)
        self._player.play()
        self._timeout.start(2000)

    def _handle_frame(self, frame) -> None:  # type: ignore[override]
        if self._complete or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            print(f"[_VideoJob] frame invalid for {self._path}", flush=True)
            return
        print(f"[_VideoJob] captured frame for {self._path}", flush=True)
        pixmap = QPixmap.fromImage(image).scaled(
            self._edge,
            self._edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._finish(QIcon(pixmap))

    def _handle_timeout(self) -> None:
        if not self._complete:
            print(f"[_VideoJob] timeout: {self._path}", flush=True)
            self._finish(None)

    def _finish(self, icon: QIcon | None) -> None:
        self._complete = True
        try:
            self._sink.videoFrameChanged.disconnect(self._handle_frame)
        except (TypeError, RuntimeError):
            pass
        self._timeout.stop()
        self._player.stop()
        if self._audio is not None:
            self._audio.deleteLater()
        self._player.deleteLater()
        print(f"[_VideoJob] finish: {self._path} icon={'Y' if icon else 'N'}", flush=True)
        self.finished.emit(str(self._path), icon)
        self.deleteLater()
