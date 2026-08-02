"""バックグラウンド処理の実行中にUIが破棄される競合を固定するテスト。

数日に1回のクラッシュは、ジョブ完了通知が「既に破棄されたウィジェット」へ
届くことで起きる。ここではその順序を意図的に作り、例外にならないことを確認する。
"""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import QThreadPool

from omnidesk.ui.file_browser_tab import FileBrowserTab
from omnidesk.ui.file_operations import FileOperationResult


def _wait_for_background_work(qtbot) -> None:
    """走らせたジョブの完了と、その通知の配送までを見届ける。"""
    pool = QThreadPool.globalInstance()
    assert pool is not None
    assert pool.waitForDone(10000)
    qtbot.wait(100)  # 積み残したキュー配送を捌く


def _slow_operation(delay: float = 0.2):
    """完了までに時間がかかるファイル操作の代役を返す。"""

    def run(request, *, is_cancelled=None):
        _ = (request, is_cancelled)
        time.sleep(delay)
        return FileOperationResult([], [])

    return run


def test_file_operation_completion_after_tab_deletion_does_not_raise(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    """転送中にタブを閉じても、完了通知で RuntimeError にならない。

    完了スロットはタブ自身を捕捉するため、生存確認が無いと
    ``RuntimeError: wrapped C/C++ object ... has been deleted`` になる。
    """
    monkeypatch.setattr(
        "omnidesk.ui.file_operation_jobs.execute_file_operation",
        _slow_operation(),
    )
    source = tmp_path / "source.txt"
    source.write_text("payload", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()

    # 実アプリのタブクローズと同じ手順を踏むため qtbot には登録しない。
    tab = FileBrowserTab()
    tab.navigate_to(tmp_path)
    tab._start_copy_or_move([source], dest, move=False)

    # ジョブが走っている最中に、TabContainer._close_tab と同じ順序で破棄する。
    tab.cancel_all_work_for_shutdown()
    tab.deleteLater()
    qtbot.waitUntil(lambda: sip.isdeleted(tab), timeout=5000)

    # 完了通知が届くまで待つ。ガードが無いとここで例外が上がる。
    # ここで待ち切らないと、通知が次のテストの最中に届いてしまう
    # （その頃には pytest-qt の例外捕捉が外れており、プロセスごと落ちる）。
    _wait_for_background_work(qtbot)


def test_tab_disposal_during_thumbnail_work_is_quiet(qtbot, tmp_path: Path) -> None:
    """サムネイル生成の最中にタブを破棄しても静かに終わる。"""
    for index in range(20):
        (tmp_path / f"image{index:02d}.png").write_bytes(b"not really a png")

    tab = FileBrowserTab()
    tab.navigate_to(tmp_path)
    tab.activate()
    tab._request_visible_thumbnails(scrolling=False)

    tab.cancel_all_work_for_shutdown()
    tab.deleteLater()
    qtbot.waitUntil(lambda: sip.isdeleted(tab), timeout=5000)
    _wait_for_background_work(qtbot)
