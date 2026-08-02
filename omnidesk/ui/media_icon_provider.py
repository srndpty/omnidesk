"""Asynchronous thumbnail loader for image and video files."""

from __future__ import annotations

import logging
import sys
import tempfile
import time
import uuid
from collections import deque
from pathlib import Path

from PyQt6.QtCore import (
    QObject,
    QProcess,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QIcon, QImage, QImageReader, QPixmap

from .qt_lifetime import own_by_application
from .thumbnail_jobs import CancellationToken

logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    """Signals emitted by background thumbnail jobs.

    ``QRunnable`` ごとに生成せず、プロバイダ（GUIスレッド常駐）が1つだけ所有する。
    ジョブは ``setAutoDelete(True)`` によりワーカースレッドで破棄されるため、
    シグナル用 ``QObject`` を持たせると、生成したスレッド以外で ``QObject`` を
    破棄することになり、Qt のスレッド規約に反してクラッシュし得る。
    """

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
        # 画像ジョブが共有する長寿命のシグナル置き場（詳細は WorkerSignals）。
        self._image_signals = own_by_application(WorkerSignals())
        self._image_signals.finished.connect(self._handle_image_from_worker)
        self._image_jobs: dict[str, _ImageJob] = {}
        self._image_tokens: dict[str, CancellationToken] = {}
        self._default_generations: dict[str, int] = {}

        # Video job management
        self._video_jobs: dict[str, _VideoJob] = {}
        self._video_tokens: dict[str, CancellationToken] = {}
        self._video_queue: deque[tuple[str, Path, int, CancellationToken]] = deque()
        self._queued_video_keys: set[str] = set()
        self._active_video_jobs = 0
        self._shutting_down = False
        self.MAX_CONCURRENT_VIDEO_JOBS = 1

        self._video_support = True
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
            job = _ImageJob(final_key, path, edge, token, self._image_signals)
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
        job = _VideoJob(
            key,
            path,
            edge,
            token,
            timeout_ms=self._video_timeout_ms,
            parent=self,
        )
        job.finished.connect(self._on_video_finished)
        self._video_jobs[key] = job
        self._video_tokens[key] = token
        self._active_video_jobs += 1
        job.start()

    def shutdown_video_jobs(self, timeout_ms: int | None = None) -> None:
        """動画ワーカーを停止し、子プロセスの終了を確定させる。

        以前はここで ``_video_jobs`` を先に空にしていたため、``cancel()`` 後に
        届く完了通知が自分のジョブを見つけられず ``deleteLater()`` されなかった。
        その結果、実行中の ``QProcess`` を持ったままプロバイダごと破棄され、
        Qt の "Destroyed while process is still running" とクラッシュを招いていた。
        """
        _ = timeout_ms  # 互換のため受け取るが、待ち時間は _VideoJob 側が持つ
        if self._shutting_down:
            return
        self._shutting_down = True

        for _, _, _, token in self._video_queue:
            token.cancel()
        self._video_queue.clear()
        self._queued_video_keys.clear()

        # cancel() は子プロセスの終了を待ってから finished を発火するため、
        # この時点で _on_video_finished が各ジョブを取り除き deleteLater する。
        for job in list(self._video_jobs.values()):
            job.cancel()

        remaining = list(self._video_jobs.values())
        if remaining:
            logger.error(
                "Video thumbnail jobs did not finish during shutdown: count=%d", len(remaining)
            )
            for job in remaining:
                # 子プロセスがまだ動いているジョブは破棄しない（破棄すると
                # 実行中の QProcess をデストラクタで壊すことになる）。
                if not job.detach_until_finished():
                    job.deleteLater()
        self._video_jobs.clear()
        self._video_tokens.clear()
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

    def _on_video_finished(self, key: str, image: QImage | None, generation: int) -> None:
        job = self._video_jobs.pop(key, None)
        token = self._video_tokens.pop(key, None)
        try:
            if self._shutting_down:
                return
            self._active_video_jobs = max(0, self._active_video_jobs - 1)
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
            if job is not None:
                job.deleteLater()
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

    def __init__(
        self,
        result_key: str,
        path: Path,
        edge: int,
        token: CancellationToken,
        signals: WorkerSignals,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._result_key = result_key  # 内部変数名も変更
        self._path = path
        self._edge = edge
        self._token = token
        self.signals = signals

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
    """別プロセスで動画の先頭フレームを抽出する。"""

    finished = pyqtSignal(str, object, int)

    def __init__(
        self,
        key: str,
        path: Path,
        edge: int,
        token: CancellationToken,
        *,
        timeout_ms: int = 5000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self._path = path
        self._edge = edge
        self._token = token
        self._timeout_ms = timeout_ms
        self._process: QProcess | None = None
        self._timeout: QTimer | None = None
        self._complete = False
        self._timed_out = False
        self._started_at: float | None = None
        output_dir = Path(tempfile.gettempdir()) / "OmniDesk" / "video-thumbnails"
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_path = output_dir / f"{uuid.uuid4().hex}.png"

    def start(self) -> None:
        if self._complete:
            return
        if self._token.cancelled:
            self._finish(None)
            return
        process = QProcess(self)
        process.finished.connect(self._handle_process_finished)
        process.errorOccurred.connect(self._handle_process_error)
        self._process = process
        timeout = QTimer(self)
        timeout.setSingleShot(True)
        timeout.timeout.connect(self._handle_timeout)
        self._timeout = timeout
        self._started_at = time.monotonic()
        logger.info("Video thumbnail job started: %s", self._path)
        timeout.start(self._timeout_ms)
        program, arguments = _video_worker_command(self._path, self._edge, self._output_path)
        process.start(program, arguments)

    def _handle_process_finished(self, exit_code: int, _exit_status: object) -> None:
        if self._complete:
            return
        image: QImage | None = None
        if (
            exit_code == 0
            and not self._token.cancelled
            and not self._timed_out
            and self._output_path.exists()
        ):
            candidate = QImage(str(self._output_path))
            if not candidate.isNull():
                image = candidate
        if image is None:
            stderr = self._read_stderr()
            if stderr:
                logger.warning("Video thumbnail worker failed for %s: %s", self._path, stderr)
        self._finish(image)

    def _handle_process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and not self._complete:
            logger.error("Video thumbnail worker could not start: %s", self._path)
            self._finish(None)

    def _read_stderr(self) -> str:
        if self._process is None:
            return ""
        return self._process.readAllStandardError().data().decode("utf-8", errors="replace").strip()

    # kill() したプロセスが NotRunning になるまで待つ上限。ここで待ち切らないと
    # 実行中の QProcess をデストラクタで破棄することになり、Qt が
    # "Destroyed while process is still running" を出してクラッシュし得る。
    KILL_WAIT_MS = 2000

    def _stop_process(self) -> bool:
        """子プロセスを強制終了し、終了が確定するまで待つ。

        ``kill()`` は非同期なので、直後の ``state()`` はまだ ``Running`` を返す。
        待たずに親を破棄すると実行中の ``QProcess`` が破棄され、ネイティブ側の
        クラッシュや終了時のブロッキングにつながる。

        終了を確認できたら ``True``。``kill()`` に応じないプロセスが残っている
        場合は ``False`` を返し、呼び出し側が破棄を避けられるようにする。
        """
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return True
        process.kill()
        if process.waitForFinished(self.KILL_WAIT_MS):
            return True
        logger.error(
            "Video thumbnail worker did not exit after kill: %s pid=%s",
            self._path,
            process.processId(),
        )
        return False

    def process_is_running(self) -> bool:
        process = self._process
        return process is not None and process.state() != QProcess.ProcessState.NotRunning

    def detach_until_finished(self) -> bool:
        """終了しなかったワーカーを、破棄せずアプリ寿命へ退避する。

        ``kill()`` に応じないプロセスを抱えたまま ``deleteLater()`` すると、
        実行中の ``QProcess`` がデストラクタで破棄され、Qt の
        "Destroyed while process is still running" とクラッシュにつながる。
        この経路を塞ぐため、終了していないジョブは破棄せず ``QApplication`` の
        子として残し、実際に終了したときに解放する。

        退避した場合は ``True``（呼び出し側は破棄してはならない）。
        """
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return False
        logger.error(
            "終了しない動画サムネイルワーカーを破棄せず保持します: %s pid=%s",
            self._path,
            process.processId(),
        )
        own_by_application(self)
        # 実際に終了したら、GUIスレッド上で安全に解放する。
        process.finished.connect(self._release_detached)
        return True

    def _release_detached(self, *_args: object) -> None:
        """退避したジョブを、子プロセスの終了後に解放する。"""
        logger.info("終了しなかった動画サムネイルワーカーを解放します: %s", self._path)
        self.deleteLater()

    def _remove_output(self) -> None:
        try:
            self._output_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove temporary video thumbnail: %s", self._output_path)

    def _handle_timeout(self) -> None:
        if self._complete:
            return
        self._timed_out = True
        logger.warning("Video thumbnail job timed out: %s", self._path)
        self._stop_process()
        if self._process is None or self._process.state() == QProcess.ProcessState.NotRunning:
            self._finish(None)

    def cancel(self) -> None:
        if self._complete:
            return
        self._token.cancel()
        self._stop_process()
        if self._process is None or self._process.state() == QProcess.ProcessState.NotRunning:
            self._finish(None)

    def _finish(self, image: QImage | None) -> None:
        if self._complete:
            return
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            logger.error("Refusing to finish a running video thumbnail worker: %s", self._path)
            return
        self._complete = True
        if self._timeout is not None:
            self._timeout.stop()
        self._remove_output()
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


def _video_worker_command(path: Path, edge: int, output_path: Path) -> tuple[str, list[str]]:
    """実行環境に応じた動画サムネイルワーカーのコマンドを返す。"""
    worker_args = [str(path), str(edge), str(output_path)]
    if getattr(sys, "frozen", False):
        return sys.executable, ["--thumbnail-worker", *worker_args]
    return sys.executable, ["-m", "omnidesk.video_thumbnail_worker", *worker_args]
