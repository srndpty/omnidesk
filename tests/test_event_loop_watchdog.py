"""イベントループ・ウォッチドッグの判定を固定するテスト。

判定ロジックは時刻依存なので、実際に固まらせるのではなく「生きている印」を
直接動かして確認する。
"""

from __future__ import annotations

import io

from omnidesk.utils.event_loop_watchdog import EventLoopWatchdog


def _watchdog(stream=None, *, stall_seconds: float = 5.0) -> EventLoopWatchdog:
    return EventLoopWatchdog(stream, stall_seconds=stall_seconds)


def test_beat_resets_the_stall_measurement(qtbot) -> None:
    watchdog = _watchdog()
    watchdog._last_beat -= 10.0
    assert watchdog.stalled_seconds() >= 10.0

    watchdog._beat()

    assert watchdog.stalled_seconds() < 1.0


def test_stall_dumps_stacks_to_the_crash_stream(qtbot) -> None:
    stream = io.StringIO()
    watchdog = _watchdog(stream, stall_seconds=2.0)
    watchdog._last_beat -= 10.0

    watchdog._check_once()

    written = stream.getvalue()
    assert "event loop stalled" in written
    # 全スレッドのスタックが出ていること（原因特定の手掛かりになる）。
    assert "Thread" in written or "File " in written


def test_stall_is_reported_once_until_the_loop_recovers(qtbot) -> None:
    stream = io.StringIO()
    watchdog = _watchdog(stream, stall_seconds=2.0)
    watchdog._last_beat -= 10.0

    watchdog._check_once()
    first_length = len(stream.getvalue())
    watchdog._check_once()

    assert len(stream.getvalue()) == first_length

    # 復帰したあと再び固まれば、あらためて記録する。
    watchdog._beat()
    watchdog._last_beat -= 10.0
    watchdog._check_once()

    assert len(stream.getvalue()) > first_length


def test_healthy_loop_writes_nothing(qtbot) -> None:
    stream = io.StringIO()
    watchdog = _watchdog(stream, stall_seconds=5.0)

    watchdog._check_once()

    assert stream.getvalue() == ""


def test_stall_dump_reaches_a_real_crash_log_file(qtbot, tmp_path) -> None:
    """実ファイル（本番のクラッシュログ相当）へも書き出せることを確認する。"""
    crash_log = tmp_path / "omnidesk-crash.log"
    with crash_log.open("a", encoding="utf-8", buffering=1) as stream:
        watchdog = _watchdog(stream, stall_seconds=2.0)
        watchdog._last_beat -= 10.0
        watchdog._check_once()

    written = crash_log.read_text(encoding="utf-8", errors="replace")
    assert "event loop stalled" in written
    assert "Thread" in written


def test_missing_stream_does_not_raise(qtbot, caplog) -> None:
    """クラッシュログを開けなかった環境でも、ウォッチドッグは落ちない。"""
    watchdog = _watchdog(None, stall_seconds=2.0)
    watchdog._last_beat -= 10.0

    watchdog._check_once()  # 例外にならないこと
