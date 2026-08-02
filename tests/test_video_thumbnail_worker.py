"""動画サムネイルワーカー（別プロセス側）の終了条件を固定するテスト。"""

from __future__ import annotations

from pathlib import Path

from omnidesk import video_thumbnail_worker
from omnidesk.video_thumbnail_worker import EXIT_TIMED_OUT, VideoThumbnailWorker, run


def test_run_rejects_invalid_arguments(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp4"
    assert run([]) == 2
    assert run([str(missing), "80", str(tmp_path / "out.png")]) == 2

    source = tmp_path / "movie.mp4"
    source.write_bytes(b"fake")
    assert run([str(source), "not-a-number", str(tmp_path / "out.png")]) == 2
    assert run([str(source), "0", str(tmp_path / "out.png")]) == 2


def test_worker_schedules_a_self_timeout(monkeypatch, qtbot, tmp_path: Path) -> None:
    """フレームが来ない動画でも必ず終了するタイマーを張る。

    これが無いと、破損動画や未対応コーデックでワーカーが無期限に居座り、
    親が異常終了した場合は孤児プロセスとして残る。
    """
    scheduled: list[int] = []
    monkeypatch.setattr(
        video_thumbnail_worker.QTimer,
        "singleShot",
        staticmethod(lambda msec, _callback: scheduled.append(msec)),
    )
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"fake")
    worker = VideoThumbnailWorker(source, 80, tmp_path / "out.png")

    worker.start()

    assert scheduled == [video_thumbnail_worker.SELF_TIMEOUT_MS]


def test_worker_timeout_exits_with_timeout_code(monkeypatch, qtbot, tmp_path: Path) -> None:
    exits: list[int] = []
    monkeypatch.setattr(
        video_thumbnail_worker.QApplication,
        "exit",
        staticmethod(lambda code: exits.append(code)),
    )
    source = tmp_path / "movie.mp4"
    source.write_bytes(b"fake")
    worker = VideoThumbnailWorker(source, 80, tmp_path / "out.png")

    worker._handle_timeout()

    assert exits == [EXIT_TIMED_OUT]
    assert worker._complete

    # 二重発火しても、既に完了しているので何も起きない。
    worker._handle_timeout()
    assert exits == [EXIT_TIMED_OUT]
