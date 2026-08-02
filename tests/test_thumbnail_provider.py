from __future__ import annotations

import sys
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QProcess
from PyQt6.QtGui import QImage

from omnidesk.ui import media_icon_provider
from omnidesk.ui.media_icon_provider import (
    MediaThumbnailProvider,
    WorkerSignals,
    _ImageJob,
    _video_worker_command,
    _VideoJob,
)
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
    provider._image_jobs["same-key"] = cast(_ImageJob, object())

    assert not provider.request_thumbnail(image_path, 100, result_key="same-key")


def test_cancelled_duplicate_image_key_can_be_requested_again(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "restart.png"
    _save_image(image_path)

    provider = MediaThumbnailProvider()
    started: list[_ImageJob] = []
    monkeypatch.setattr(provider._thread_pool, "start", started.append)
    old_token = CancellationToken(1)
    old_token.cancel()
    provider._image_jobs["same-key"] = cast(_ImageJob, object())
    provider._image_tokens["same-key"] = old_token

    new_token = CancellationToken(2)

    assert provider.request_thumbnail(
        image_path,
        100,
        result_key="same-key",
        token=new_token,
    )
    assert len(started) == 1
    assert provider._image_tokens["same-key"] is new_token

    with qtbot.assertNotEmitted(provider.thumbnailReady, wait=100):
        provider._on_image_finished("same-key", QImage(), 100, old_token.generation)

    assert provider._image_tokens["same-key"] is new_token
    assert "same-key" in provider._image_jobs


def test_cancelled_default_token_image_key_uses_new_generation(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "default-token.png"
    _save_image(image_path)

    provider = MediaThumbnailProvider()
    started: list[_ImageJob] = []
    monkeypatch.setattr(provider._thread_pool, "start", started.append)

    assert provider.request_thumbnail(image_path, 100, result_key="same-key")
    old_token = provider._image_tokens["same-key"]
    assert old_token.generation == 0

    provider.cancel_thumbnail("same-key")
    assert provider.request_thumbnail(image_path, 100, result_key="same-key")
    new_token = provider._image_tokens["same-key"]
    assert new_token.generation == 1
    assert len(started) == 2

    stale_image = QImage(20, 20, QImage.Format.Format_RGB32)
    stale_image.fill(0x00FF00)
    with qtbot.assertNotEmitted(provider.thumbnailReady, wait=100):
        provider._on_image_finished("same-key", stale_image, 100, old_token.generation)

    assert provider._image_tokens["same-key"] is new_token
    assert "same-key" in provider._image_jobs


def test_unsupported_thumbnail_extension_is_rejected(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("not media", encoding="utf-8")

    provider = MediaThumbnailProvider()

    assert not provider.request_thumbnail(file_path, 100, result_key="text-key")


def test_cancel_thumbnail_cancels_image_token_and_removes_queued_video() -> None:
    provider = MediaThumbnailProvider()
    token = CancellationToken(5)
    active_video_token = CancellationToken(6)
    queued_video_token = CancellationToken(7)
    provider._image_tokens["image-key"] = token
    provider._video_tokens["active-video"] = active_video_token
    provider._video_queue = deque(
        [
            ("drop-me", Path("queued.mp4"), 100, queued_video_token),
            ("keep-me", Path("other.mp4"), 100, CancellationToken(2)),
        ]
    )
    provider._queued_video_keys = {"drop-me", "keep-me"}

    provider.cancel_thumbnail("drop-me")
    provider.cancel_thumbnail("image-key")
    provider.cancel_thumbnail("active-video")

    assert token.cancelled
    assert active_video_token.cancelled
    assert queued_video_token.cancelled
    assert [item[0] for item in provider._video_queue] == ["keep-me"]
    assert provider._queued_video_keys == {"keep-me"}


def test_cancel_thumbnail_drops_image_job_and_token_entries() -> None:
    provider = MediaThumbnailProvider()
    token = CancellationToken(3)
    provider._image_jobs["leaky-key"] = cast(_ImageJob, object())
    provider._image_tokens["leaky-key"] = token

    provider.cancel_thumbnail("leaky-key")

    assert token.cancelled
    assert "leaky-key" not in provider._image_jobs
    assert "leaky-key" not in provider._image_tokens


def test_on_image_finished_ignores_result_after_token_dropped(qtbot) -> None:
    provider = MediaThumbnailProvider()

    with qtbot.assertNotEmitted(provider.thumbnailReady, wait=100):
        provider._on_image_finished("dropped", QImage(), 100, 0)


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
    provider._video_jobs["duplicate"] = cast(_VideoJob, object())
    provider._video_queue = deque(
        [
            ("cancelled", Path("cancelled.mp4"), 100, cancelled),
            ("duplicate", Path("duplicate.mp4"), 100, duplicate),
            ("ready", Path("ready.mp4"), 100, ready),
        ]
    )
    monkeypatch.setattr(
        provider,
        "_start_video_job",
        lambda key, path, edge, token: started.append(key),
    )

    provider._process_video_queue()

    assert started == ["ready"]
    assert len(provider._video_queue) == 0


def test_duplicate_queued_video_key_is_rejected(monkeypatch, tmp_path: Path) -> None:
    provider = MediaThumbnailProvider()
    provider._active_video_jobs = provider.MAX_CONCURRENT_VIDEO_JOBS
    video_path = tmp_path / "queued.mp4"
    video_path.write_bytes(b"fake")

    assert provider.request_thumbnail(video_path, 100, result_key="queued-key")
    assert not provider.request_thumbnail(video_path, 100, result_key="queued-key")

    assert [item[0] for item in provider._video_queue] == ["queued-key"]
    assert provider._queued_video_keys == {"queued-key"}


def test_cancelled_active_video_thumbnail_does_not_emit(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_VideoJob, "start", lambda self: None)
    provider = MediaThumbnailProvider()
    provider.MAX_CONCURRENT_VIDEO_JOBS = 1
    video_path = tmp_path / "active.mp4"
    next_video_path = tmp_path / "next.mp4"
    video_path.write_bytes(b"fake")
    next_video_path.write_bytes(b"fake")

    assert provider.request_thumbnail(video_path, 100, result_key="active-key")
    assert provider.request_thumbnail(next_video_path, 100, result_key="next-key")
    job = provider._video_jobs["active-key"]

    with qtbot.assertNotEmitted(provider.thumbnailReady, wait=100):
        provider.cancel_thumbnail("active-key")

    assert provider._active_video_jobs == 1
    assert "active-key" not in provider._video_jobs
    assert "active-key" not in provider._video_tokens
    assert job._complete
    assert "next-key" in provider._video_jobs
    assert "next-key" in provider._video_tokens
    assert provider._queued_video_keys == set()

    provider.cancel_thumbnail("next-key")


def test_video_job_cancel_kills_worker_process(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    _install_process_fakes(monkeypatch)
    video_path = tmp_path / "race.mp4"
    video_path.write_bytes(b"fake")
    job = _VideoJob("race-key", video_path, 100, CancellationToken(8))
    job.start()
    process = _FakeProcess.instances[-1]

    with qtbot.waitSignal(job.finished, timeout=1000):
        job.cancel()
        process.finish(1)

    assert process.killed
    assert job._complete


def test_shutdown_video_jobs_cancels_workers_without_waiting(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_VideoJob, "start", lambda self: None)
    provider = MediaThumbnailProvider()
    video_path = tmp_path / "shutdown.mp4"
    video_path.write_bytes(b"fake")

    assert provider.request_thumbnail(video_path, 100, result_key="shutdown-key")
    provider.shutdown_video_jobs()

    assert provider._video_jobs == {}
    assert provider._video_tokens == {}
    assert not provider.request_thumbnail(video_path, 100, result_key="after-shutdown")
    qtbot.wait(50)
    assert provider._active_video_jobs == 0


def test_cancel_waits_for_the_child_process_to_exit(monkeypatch, tmp_path: Path) -> None:
    """kill() したあと、終了が確定するまで待ってから完了扱いにする。

    待たずに親を破棄すると、実行中の QProcess がデストラクタで破棄され
    "Destroyed while process is still running" とクラッシュにつながる。
    """
    _install_process_fakes(monkeypatch)
    video_path = tmp_path / "movie.mp4"
    job = _VideoJob("video-key", video_path, 80, CancellationToken(31))
    job.start()
    process = _FakeProcess.instances[-1]

    job.cancel()

    assert process.killed
    assert process.wait_calls  # 終了待ちを行っている
    assert process.state() == QProcess.ProcessState.NotRunning
    assert job._complete


def test_shutdown_deletes_jobs_even_when_process_ignores_kill(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    """kill() に応じないプロセスが居ても、ジョブを取り残さない。"""
    _install_process_fakes(monkeypatch)
    provider = MediaThumbnailProvider()
    video_path = tmp_path / "stubborn.mp4"
    video_path.write_bytes(b"fake")

    assert provider.request_thumbnail(video_path, 100, result_key="stubborn-key")
    _FakeProcess.instances[-1].ignores_kill = True

    provider.shutdown_video_jobs()

    assert provider._video_jobs == {}
    assert provider._video_tokens == {}
    qtbot.wait(50)
    assert provider._active_video_jobs == 0


def test_shutdown_releases_jobs_that_finish_during_cancel(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    """終了停止の途中で完了したジョブも、辞書から取り除かれる。"""
    _install_process_fakes(monkeypatch)
    provider = MediaThumbnailProvider()
    video_path = tmp_path / "normal.mp4"
    video_path.write_bytes(b"fake")

    assert provider.request_thumbnail(video_path, 100, result_key="normal-key")
    job = provider._video_jobs["normal-key"]

    provider.shutdown_video_jobs()

    assert job._complete
    assert provider._video_jobs == {}
    qtbot.wait(50)
    assert provider._active_video_jobs == 0


def test_on_video_finished_starts_next_queued_job(monkeypatch, qtbot) -> None:
    provider = MediaThumbnailProvider()
    started: list[str] = []
    provider._active_video_jobs = 1
    provider._video_jobs["done"] = cast(_VideoJob, None)
    provider._video_queue = deque([("next", Path("next.mp4"), 100, CancellationToken(4))])
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
    signals = WorkerSignals()
    job = _ImageJob("job-key", image_path, 40, token, signals)

    with qtbot.waitSignal(signals.finished, timeout=1000) as blocker:
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
    signals = WorkerSignals()
    job = _ImageJob("job-key", image_path, 40, token, signals)

    with qtbot.assertNotEmitted(signals.finished, wait=100):
        job.run()


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[Callable[..., object]] = []

    def connect(self, callback: Callable[..., object]) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback: Callable[..., object]) -> None:
        if callback not in self.callbacks:
            raise TypeError("callback is not connected")
        self.callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        for callback in list(self.callbacks):
            callback(*args)


class _FakeTimer:
    instances: list[_FakeTimer] = []

    def __init__(self, _parent=None) -> None:
        self.timeout = _FakeSignal()
        self.single_shot: bool | None = None
        self.started_with: int | None = None
        self.stopped = False
        _FakeTimer.instances.append(self)

    def setSingleShot(self, single_shot: bool) -> None:
        self.single_shot = single_shot

    def start(self, milliseconds: int) -> None:
        self.started_with = milliseconds

    def stop(self) -> None:
        self.stopped = True


class _FakeByteArray:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def data(self) -> bytes:
        return self._data


class _FakeProcess:
    instances: list[_FakeProcess] = []
    ProcessError = QProcess.ProcessError
    ProcessState = QProcess.ProcessState

    def __init__(self, _parent=None) -> None:
        self.finished = _FakeSignal()
        self.errorOccurred = _FakeSignal()
        self.program: str | None = None
        self.arguments: list[str] = []
        self.killed = False
        self.wait_calls: list[int] = []
        # True にすると kill() 後も終了しない「死なないプロセス」を再現できる。
        self.ignores_kill = False
        self._state = QProcess.ProcessState.NotRunning
        self.stderr = b""
        _FakeProcess.instances.append(self)

    def start(self, program: str, arguments: list[str]) -> None:
        self.program = program
        self.arguments = arguments
        self._state = QProcess.ProcessState.Running

    def state(self) -> QProcess.ProcessState:
        return self._state

    def kill(self) -> None:
        # 本物の kill() は非同期で、直後の state() はまだ Running を返す。
        self.killed = True

    def waitForFinished(self, msecs: int = 30000) -> bool:  # noqa: N802 - Qt-style API
        self.wait_calls.append(msecs)
        if self.ignores_kill:
            return False
        if self._state == QProcess.ProcessState.NotRunning:
            return True
        self.finish(1)
        return True

    def processId(self) -> int:  # noqa: N802 - Qt-style API
        return 4242

    def finish(self, exit_code: int = 0) -> None:
        self._state = QProcess.ProcessState.NotRunning
        self.finished.emit(exit_code, QProcess.ExitStatus.NormalExit)

    def fail_to_start(self) -> None:
        self._state = QProcess.ProcessState.NotRunning
        self.errorOccurred.emit(QProcess.ProcessError.FailedToStart)

    def readAllStandardError(self) -> _FakeByteArray:
        return _FakeByteArray(self.stderr)


def _install_process_fakes(monkeypatch) -> None:
    _FakeProcess.instances = []
    _FakeTimer.instances = []
    monkeypatch.setattr(media_icon_provider, "QProcess", _FakeProcess)
    monkeypatch.setattr(media_icon_provider, "QTimer", _FakeTimer)


def test_video_job_start_configures_worker_process(monkeypatch, tmp_path: Path) -> None:
    _install_process_fakes(monkeypatch)
    video_path = tmp_path / "movie.mp4"
    token = CancellationToken(21)

    job = _VideoJob("video-key", video_path, 80, token)
    job.start()

    process = _FakeProcess.instances[-1]
    timer = _FakeTimer.instances[-1]
    assert process.program is not None
    assert str(video_path) in process.arguments
    assert "80" in process.arguments
    assert str(job._output_path) in process.arguments
    assert timer.single_shot is True
    assert timer.started_with == 5000


def test_video_job_cancelled_start_finishes_without_icon(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    _install_process_fakes(monkeypatch)
    token = CancellationToken(22)
    token.cancel()
    job = _VideoJob("video-key", tmp_path / "movie.mp4", 80, token)

    with qtbot.waitSignal(job.finished, timeout=1000) as blocker:
        job.start()

    assert blocker.args == ["video-key", None, 22]
    assert not _FakeProcess.instances
    assert not _FakeTimer.instances


def test_video_job_failed_start_emits_none(monkeypatch, qtbot, tmp_path: Path) -> None:
    _install_process_fakes(monkeypatch)
    job = _VideoJob("video-key", tmp_path / "movie.mp4", 80, CancellationToken(23))
    job.start()

    with qtbot.waitSignal(job.finished, timeout=1000) as blocker:
        _FakeProcess.instances[-1].fail_to_start()

    assert blocker.args == ["video-key", None, 23]


def test_video_job_success_loads_worker_output(monkeypatch, qtbot, tmp_path: Path) -> None:
    _install_process_fakes(monkeypatch)
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(0x00FF00)
    job = _VideoJob("video-key", tmp_path / "movie.mp4", 64, CancellationToken(24))
    job.start()
    assert image.save(str(job._output_path), "PNG")

    with qtbot.waitSignal(job.finished, timeout=1000) as blocker:
        _FakeProcess.instances[-1].finish()

    key, image, generation = blocker.args
    assert key == "video-key"
    assert isinstance(image, QImage)
    assert generation == 24
    assert image.width() == 200
    assert image.height() == 100
    assert not job._output_path.exists()


def test_video_job_timeout_finishes_once(monkeypatch, qtbot, tmp_path: Path) -> None:
    _install_process_fakes(monkeypatch)
    job = _VideoJob("video-key", tmp_path / "movie.mp4", 64, CancellationToken(27))
    emitted: list[list[object]] = []
    job.finished.connect(lambda *args: emitted.append(list(args)))
    job.start()
    process = _FakeProcess.instances[-1]

    with qtbot.waitSignal(job.finished, timeout=1000) as blocker:
        job._handle_timeout()
        process.finish(1)

    assert blocker.args == ["video-key", None, 27]
    assert emitted == [["video-key", None, 27]]
    assert process.killed

    job._handle_timeout()

    assert emitted == [["video-key", None, 27]]


def test_video_worker_timeout_waits_for_process_exit(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    video_path = tmp_path / "hung.mp4"
    video_path.write_bytes(b"fake")
    monkeypatch.setattr(
        media_icon_provider,
        "_video_worker_command",
        lambda *_args: (sys.executable, ["-c", "import time; time.sleep(60)"]),
    )
    provider = MediaThumbnailProvider()
    provider.set_video_timeout_ms(1000)

    with qtbot.waitSignal(provider.thumbnailReady, timeout=5000) as blocker:
        assert provider.request_thumbnail(video_path, 96, result_key="hung-key")

    assert blocker.args == ["hung-key", None, 0]
    assert provider._video_jobs == {}
    assert provider._video_tokens == {}
    assert provider._active_video_jobs == 0


def test_video_worker_command_uses_module_in_development(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delattr(media_icon_provider.sys, "frozen", raising=False)

    program, arguments = _video_worker_command(tmp_path / "movie.mp4", 96, tmp_path / "output.png")

    assert program == media_icon_provider.sys.executable
    assert arguments[:2] == ["-m", "omnidesk.video_thumbnail_worker"]


def test_video_worker_command_reuses_frozen_executable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(media_icon_provider.sys, "frozen", True, raising=False)

    program, arguments = _video_worker_command(tmp_path / "movie.mp4", 96, tmp_path / "output.png")

    assert program == media_icon_provider.sys.executable
    assert arguments[0] == "--thumbnail-worker"
