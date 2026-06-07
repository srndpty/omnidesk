from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QIcon, QImage

from omnidesk.ui import media_icon_provider
from omnidesk.ui.media_icon_provider import MediaThumbnailProvider, _ImageJob, _VideoJob
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
    _install_video_fakes(monkeypatch)
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
    _install_video_fakes(monkeypatch)
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
    assert _FakePlayer.instances[0].stopped
    assert "next-key" in provider._video_jobs
    assert "next-key" in provider._video_tokens
    assert provider._queued_video_keys == set()


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


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback) -> None:
        if callback not in self.callbacks:
            raise TypeError("callback is not connected")
        self.callbacks.remove(callback)


class _FakeAudioOutput:
    def __init__(self, _parent=None) -> None:
        self.volume: float | None = None
        self.deleted = False

    def setVolume(self, volume: float) -> None:
        self.volume = volume

    def deleteLater(self) -> None:
        self.deleted = True


class _FakePlayer:
    instances: list[_FakePlayer] = []

    def __init__(self, _parent=None) -> None:
        self.audio_output = None
        self.video_sink = None
        self.source = None
        self.position: int | None = None
        self.played = False
        self.stopped = False
        self.deleted = False
        _FakePlayer.instances.append(self)

    def setAudioOutput(self, audio_output) -> None:
        self.audio_output = audio_output

    def setVideoSink(self, video_sink) -> None:
        self.video_sink = video_sink

    def setSource(self, source) -> None:
        self.source = source

    def setPosition(self, position: int) -> None:
        self.position = position

    def play(self) -> None:
        self.played = True

    def stop(self) -> None:
        self.stopped = True

    def deleteLater(self) -> None:
        self.deleted = True


class _FakeVideoSink:
    instances: list[_FakeVideoSink] = []

    def __init__(self, _parent=None) -> None:
        self.videoFrameChanged = _FakeSignal()
        _FakeVideoSink.instances.append(self)


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


class _FakeFrame:
    def __init__(self, image: QImage, *, valid: bool = True) -> None:
        self._image = image
        self._valid = valid

    def isValid(self) -> bool:
        return self._valid

    def toImage(self) -> QImage:
        return self._image


def _install_video_fakes(monkeypatch) -> None:
    _FakePlayer.instances = []
    _FakeVideoSink.instances = []
    _FakeTimer.instances = []
    monkeypatch.setattr(media_icon_provider, "QAudioOutput", _FakeAudioOutput)
    monkeypatch.setattr(media_icon_provider, "QMediaPlayer", _FakePlayer)
    monkeypatch.setattr(media_icon_provider, "QVideoSink", _FakeVideoSink)
    monkeypatch.setattr(media_icon_provider, "QTimer", _FakeTimer)


def test_video_job_start_configures_player(monkeypatch, tmp_path: Path) -> None:
    _install_video_fakes(monkeypatch)
    video_path = tmp_path / "movie.mp4"
    token = CancellationToken(21)

    job = _VideoJob("video-key", video_path, 80, token)
    job.start()

    player = _FakePlayer.instances[-1]
    timer = _FakeTimer.instances[-1]
    assert player.source is not None
    source = cast(QUrl, player.source)
    assert Path(source.toLocalFile()) == video_path
    assert player.position == 0
    assert player.played
    assert timer.single_shot is True
    assert timer.started_with == 5000


def test_video_job_cancelled_start_finishes_without_icon(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    _install_video_fakes(monkeypatch)
    token = CancellationToken(22)
    token.cancel()
    job = _VideoJob("video-key", tmp_path / "movie.mp4", 80, token)

    with qtbot.waitSignal(job.finished, timeout=1000) as blocker:
        job.start()

    player = _FakePlayer.instances[-1]
    assert blocker.args == ["video-key", None, 22]
    assert player.stopped
    assert player.deleted
    assert _FakeTimer.instances[-1].stopped


def test_video_job_unavailable_start_emits_none(monkeypatch, qtbot, tmp_path: Path) -> None:
    _install_video_fakes(monkeypatch)
    job = _VideoJob("video-key", tmp_path / "movie.mp4", 80, CancellationToken(23))
    monkeypatch.setattr(media_icon_provider, "QVideoSink", None)

    with qtbot.waitSignal(job.finished, timeout=1000) as blocker:
        job.start()

    assert blocker.args == ["video-key", None, 23]


def test_video_job_handle_frame_emits_scaled_icon(monkeypatch, qtbot, tmp_path: Path) -> None:
    _install_video_fakes(monkeypatch)
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(0x00FF00)
    job = _VideoJob("video-key", tmp_path / "movie.mp4", 64, CancellationToken(24))

    with qtbot.waitSignal(job.finished, timeout=1000) as blocker:
        job._handle_frame(_FakeFrame(image))

    key, icon, generation = blocker.args
    assert key == "video-key"
    assert isinstance(icon, QIcon)
    assert generation == 24
    pixmap = icon.pixmap(64, 64)
    assert pixmap.width() <= 64
    assert pixmap.height() <= 64
    assert _FakePlayer.instances[-1].stopped


def test_video_job_ignores_invalid_null_cancelled_and_complete_frames(
    monkeypatch,
    qtbot,
    tmp_path: Path,
) -> None:
    _install_video_fakes(monkeypatch)
    token = CancellationToken(25)
    job = _VideoJob("video-key", tmp_path / "movie.mp4", 64, token)
    null_image = QImage()
    valid_image = QImage(20, 20, QImage.Format.Format_RGB32)
    valid_image.fill(0x0000FF)

    with qtbot.assertNotEmitted(job.finished, wait=100):
        job._handle_frame(_FakeFrame(valid_image, valid=False))
        job._handle_frame(_FakeFrame(null_image))
        token.cancel()
        job._handle_frame(_FakeFrame(valid_image))

    token = CancellationToken(26)
    job = _VideoJob("video-key-2", tmp_path / "movie.mp4", 64, token)
    job._complete = True

    with qtbot.assertNotEmitted(job.finished, wait=100):
        job._handle_frame(_FakeFrame(valid_image))


def test_video_job_timeout_finishes_once(monkeypatch, qtbot, tmp_path: Path) -> None:
    _install_video_fakes(monkeypatch)
    job = _VideoJob("video-key", tmp_path / "movie.mp4", 64, CancellationToken(27))
    emitted: list[list[object]] = []
    job.finished.connect(lambda *args: emitted.append(list(args)))

    with qtbot.waitSignal(job.finished, timeout=1000) as blocker:
        job._handle_timeout()

    assert blocker.args == ["video-key", None, 27]
    assert emitted == [["video-key", None, 27]]

    job._handle_timeout()

    assert emitted == [["video-key", None, 27]]
