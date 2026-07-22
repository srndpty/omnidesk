"""Asynchronous thumbnail loader for image and video files."""

from __future__ import annotations

import logging
import time
from collections import deque
from contextlib import suppress
from pathlib import Path

from PyQt6.QtCore import (
    QObject,
    QRunnable,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
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
    VIDEO_SHUTDOWN_TIMEOUT_MS = 1000

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
        self._default_generations: dict[str, int] = {}

        # Video job management
        self._video_jobs: dict[str, _VideoJob] = {}
        self._video_threads: dict[str, QThread] = {}
        self._video_tokens: dict[str, CancellationToken] = {}
        self._video_queue: deque[tuple[str, Path, int, CancellationToken]] = deque()
        self._queued_video_keys: set[str] = set()
        self._active_video_jobs = 0
        self._shutting_down = False
        self.MAX_CONCURRENT_VIDEO_JOBS = 1

        self._video_support = QMediaPlayer is not None and QVideoSink is not None
        self._video_timeout_ms = 5000

    def set_video_timeout_ms(self, ms: int) -> None:
        if 1000 <= ms <= 30000:
            self._video_timeout_ms = ms

    # ------------------------------------------------------------------
    @property
    def media_extensions(self) -> set[str]:
        return self.IMAGE_EXTENSIONS | self.VIDEO_EXTENSIONS

    @property
    def video_supported(self) -> bool:
        return self._video_support

    def _new_default_token(self, key: str) -> CancellationToken:
        generation = self._default_generations.get(key, -1) + 1
        self._default_generations[key] = generation
        return CancellationToken(generation)

    def request_thumbnail(
        self,
        path: Path,
        edge: int,
        *,
        result_key: str | None = None,
        token: CancellationToken | None = None,
    ) -> bool:
        if self._shutting_down:
            return False
        suffix = path.suffix.lower()

        # 通知用のキーが指定されていなければ、元のパスをキーとする
        final_key = result_key or str(path)
        if suffix in self.IMAGE_EXTENSIONS:
            if final_key in self._image_jobs:
                existing_token = self._image_tokens.get(final_key)
                if existing_token is None or not existing_token.cancelled:
                    return False
            token = token or self._new_default_token(final_key)
            job = _ImageJob(final_key, path, edge, token)
            job.signals.finished.connect(self._handle_image_from_worker)
            self._image_jobs[final_key] = job
            self._image_tokens[final_key] = token
            self._thread_pool.start(job)
            return True
        if suffix in self.VIDEO_EXTENSIONS:
            if not self._video_support:
                return False
            if final_key in self._video_jobs or final_key in self._queued_video_keys:
                return False

            # Check if we can start immediately
            token = token or self._new_default_token(final_key)
            if self._active_video_jobs < self.MAX_CONCURRENT_VIDEO_JOBS:
                self._start_video_job(final_key, path, edge, token)
            else:
                self._video_queue.append((final_key, path, edge, token))
                self._queued_video_keys.add(final_key)

            return True
        return False

    def cancel_thumbnail(self, key: str) -> None:
        image_token = self._image_tokens.pop(key, None)
        if image_token is not None:
            image_token.cancel()
            # キャンセルされた画像ジョブは emit せずに run() を抜けるため、
            # _on_image_finished が呼ばれずこれらのエントリが掃除されない。
            # 再要求されないままキャンセルされたキーが溜まらないよう、ここで取り除く。
            self._image_jobs.pop(key, None)
        video_token = self._video_tokens.get(key)
        if video_token is not None:
            video_token.cancel()
            job = self._video_jobs.get(key)
            if job is not None:
                job.cancel()
        queued_items = []
        for item in self._video_queue:
            queued_key, _, _, queued_token = item
            if queued_key == key:
                queued_token.cancel()
                continue
            queued_items.append(item)
        self._video_queue = deque(queued_items)
        self._queued_video_keys.discard(key)

    def _start_video_job(self, key: str, path: Path, edge: int, token: CancellationToken) -> None:
        job = _VideoJob(key, path, edge, token, timeout_ms=self._video_timeout_ms)
        thread = QThread(self)
        job.moveToThread(thread)
        thread.started.connect(job.start)
        job.finished.connect(self._on_video_finished)
        thread.finished.connect(job.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda key=key, thread=thread: self._forget_video_thread(key, thread)
        )
        self._video_jobs[key] = job
        self._video_threads[key] = thread
        self._video_tokens[key] = token
        self._active_video_jobs += 1
        thread.start()

    def _forget_video_thread(self, key: str, thread: QThread) -> None:
        if self._video_threads.get(key) is thread:
            self._video_threads.pop(key, None)

    def shutdown_video_jobs(self, timeout_ms: int | None = None) -> None:
        """動画ジョブを停止し、所有する専用スレッドの終了を待つ。"""
        if self._shutting_down:
            return
        self._shutting_down = True

        for _, _, _, token in self._video_queue:
            token.cancel()
        self._video_queue.clear()
        self._queued_video_keys.clear()

        jobs = list(self._video_jobs.items())
        threads = list(self._video_threads.items())
        for key, job in jobs:
            thread = self._video_threads.get(key)
            if thread is None or not thread.isRunning():
                continue
            if not job._complete:
                job.cancel()
            # キャンセルトークンは同期的に設定済み。イベントループへ終了を要求し、
            # 下の wait() で親QObjectを破棄する前に停止を確認する。
            thread.quit()

        deadline = time.monotonic() + (
            (timeout_ms if timeout_ms is not None else self.VIDEO_SHUTDOWN_TIMEOUT_MS) / 1000
        )
        for key, thread in threads:
            if not thread.isRunning():
                continue
            remaining_ms = max(0, round((deadline - time.monotonic()) * 1000))
            if remaining_ms > 0 and thread.wait(remaining_ms):
                continue
            logger.error("Video thumbnail thread did not stop in time: %s", key)
            thread.terminate()
            # terminate() 後に実行中QThreadを親ごと破棄しないよう、終了を確実に待つ。
            thread.wait(2**32 - 1)

        self._video_jobs.clear()
        self._video_tokens.clear()
        self._video_threads.clear()
        self._active_video_jobs = 0

    # ------------------------------------------------------------------
    def _handle_image_from_worker(
        self, key: str, image: object, edge: int, generation: int
    ) -> None:
        qimage = image if isinstance(image, QImage) else None
        self._on_image_finished(key, qimage, edge, generation)

    def _on_image_finished(
        self, key: str, image: QImage | None, edge: int, generation: int
    ) -> None:
        token = self._image_tokens.get(key)
        if token is None:
            # token は cancel_thumbnail で破棄済み（または重複配信）。古いアイコンを
            # emit せず、遅れて届いた結果は無視する。
            return
        if token.generation != generation:
            logger.debug(
                "Ignoring stale image thumbnail job: %s generation=%s current=%s",
                key,
                generation,
                token.generation,
            )
            return
        self._image_jobs.pop(key, None)
        self._image_tokens.pop(key, None)
        if token.cancelled:
            return
        icon: QIcon | None = None
        if image is not None and not image.isNull():
            pixmap = QPixmap.fromImage(image)
            icon = QIcon(pixmap)
        else:
            logger.warning("Image thumbnail job finished with no image: %s", key)
        self.thumbnailReady.emit(key, icon, generation)

    @pyqtSlot(str, object, int)
    def _on_video_finished(self, key: str, image: QImage | None, generation: int) -> None:
        self._video_jobs.pop(key, None)
        token = self._video_tokens.pop(key, None)
        thread = self._video_threads.get(key)
        try:
            if self._shutting_down:
                return
            self._active_video_jobs -= 1
            if token is not None and token.generation != generation:
                logger.debug(
                    "Ignoring stale video thumbnail job: %s generation=%s current=%s",
                    key,
                    generation,
                    token.generation,
                )
                return
            if token is not None and token.cancelled:
                return
            icon = (
                QIcon(QPixmap.fromImage(image))
                if image is not None and not image.isNull()
                else None
            )
            self.thumbnailReady.emit(key, icon, generation)
        finally:
            if thread is not None:
                thread.quit()
            if not self._shutting_down:
                self._process_video_queue()

    def _process_video_queue(self) -> None:
        while self._active_video_jobs < self.MAX_CONCURRENT_VIDEO_JOBS and self._video_queue:
            key, path, edge, token = self._video_queue.popleft()
            self._queued_video_keys.discard(key)
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
    cancelRequested = pyqtSignal()

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
        self._media_player_cls = media_player_cls
        self._video_sink_cls = video_sink_cls
        self._player = None
        self._audio = None
        self._sink = None
        self._timeout = None
        self._complete = False
        self._started_at: float | None = None
        self.cancelRequested.connect(self._cancel_in_worker)

    @pyqtSlot()
    def start(self) -> None:
        if self._complete:
            return
        if QMediaPlayer is None or QVideoSink is None:
            self.finished.emit(self._key, None, self._token.generation)
            return
        if self._token.cancelled:
            self._finish(None)
            return
        self._player = self._media_player_cls(self)
        self._audio = QAudioOutput(self) if QAudioOutput is not None else None
        if self._audio is not None:
            self._audio.setVolume(0.0)
            self._player.setAudioOutput(self._audio)
        self._sink = self._video_sink_cls(self)
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._handle_frame)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._handle_timeout)
        self._started_at = time.monotonic()
        logger.info("Video thumbnail job started: %s", self._path)
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
        image = image.scaled(
            self._edge,
            self._edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._finish(image)

    def _handle_timeout(self) -> None:
        if not self._complete:
            logger.warning("Video thumbnail job timed out: %s", self._path)
            self._finish(None)

    def cancel(self) -> None:
        self._token.cancel()
        self.cancelRequested.emit()

    @pyqtSlot()
    def _cancel_in_worker(self) -> None:
        self._finish(None)

    def _finish(self, image: QImage | None) -> None:
        if self._complete:
            return
        self._complete = True
        if self._sink is not None:
            with suppress(TypeError, RuntimeError):
                self._sink.videoFrameChanged.disconnect(self._handle_frame)
        if self._timeout is not None:
            self._timeout.stop()
        if self._player is not None:
            self._player.stop()
        if self._audio is not None:
            self._audio.deleteLater()
        if self._player is not None:
            self._player.deleteLater()
        elapsed_ms = (
            round((time.monotonic() - self._started_at) * 1000)
            if self._started_at is not None
            else 0
        )
        logger.info(
            "Video thumbnail job finished: %s image=%s elapsed_ms=%d",
            self._path,
            image is not None and not image.isNull(),
            elapsed_ms,
        )
        self.finished.emit(
            self._key, None if self._token.cancelled else image, self._token.generation
        )
