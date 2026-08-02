from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QThreadPool

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# バックグラウンドジョブの終了を待つ上限。ここで待ち切れないほど長い処理を
# 走らせるテストは、テスト側で明示的に止めるべき。
POOL_DRAIN_TIMEOUT_MS = 10_000


@pytest.fixture(autouse=True)
def drain_global_thread_pool():
    """テスト境界をまたいでバックグラウンドジョブを持ち越さない。

    実ジョブを走らせたままテストを終えると、完了通知が次のテストの最中に届く。
    そのころには pytest-qt の例外捕捉が外れているため、スロット内で例外が起きると
    PyQt6 が ``qFatal()`` を呼び、トレースバックもテストサマリも出ないまま
    プロセスごと落ちる（CIで実際に発生した）。

    各テストの後にグローバルスレッドプールを空にして、この持ち越しを断つ。
    """
    yield
    pool = QThreadPool.globalInstance()
    if pool is None:
        return
    pool.clear()  # 未開始のジョブは捨てる
    if not pool.waitForDone(POOL_DRAIN_TIMEOUT_MS):
        pytest.fail(
            f"テスト終了時にバックグラウンドジョブが残りました (active={pool.activeThreadCount()})"
        )
