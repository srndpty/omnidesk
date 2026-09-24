from __future__ import annotations

import gc
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


@pytest.fixture(scope="session", autouse=True)
def ensure_qapplication(qapp):
    """どのテストより先に ``QApplication`` を用意する。

    ``QPixmap`` や ``QIcon`` は ``QApplication`` が無いとプロセスごと落ちる。
    ``qapp`` / ``qtbot`` を要求しないテストが、先行テストの作った ``QApplication`` に
    暗黙に頼っていると、pytest-xdist でワーカーの先頭に割り振られたときだけ落ちる。
    """
    return qapp


@pytest.fixture(scope="session", autouse=True)
def isolated_thumbnail_cache(tmp_path_factory: pytest.TempPathFactory):
    """サムネイルのディスクキャッシュを、テストセッション専用の一時フォルダへ向ける。

    既定の保存先は利用者の実キャッシュ（``%LOCALAPPDATA%`` 配下）で、テストが
    利用者のキャッシュを汚すうえ、pytest-xdist の各ワーカーが同じフォルダを
    同時に読み書き・削除し合う。モジュール読み込み時に作られる共有キャッシュの
    保存先だけを差し替え、終了時に戻す。
    """
    from omnidesk.utils import thumbnail_cache

    caches = (thumbnail_cache.folder_preview_cache, thumbnail_cache.file_thumbnail_cache)
    originals = [cache._root for cache in caches]
    root = tmp_path_factory.mktemp("thumbnail-cache")
    for cache, original in zip(caches, originals, strict=True):
        cache._root = root / original.name
        cache._root.mkdir(parents=True, exist_ok=True)
    yield root
    for cache, original in zip(caches, originals, strict=True):
        cache._root = original


@pytest.fixture(autouse=True)
def drain_global_thread_pool():
    """テスト境界をまたいでバックグラウンドジョブを持ち越さない。

    実ジョブを走らせたままテストを終えると、完了通知が次のテストの最中に届く。
    そのころには pytest-qt の例外捕捉が外れているため、スロット内で例外が起きると
    PyQt6 が ``qFatal()`` を呼び、トレースバックもテストサマリも出ないまま
    プロセスごと落ちる（CIで実際に発生した）。

    各テストの後にグローバルスレッドプールを空にして、この持ち越しを断つ。

    待機の前に GUI スレッドで循環参照を回収しておく。テストが親なしで作った
    ウィジェットやモデル（Python 所有）は、pytest-qt が ``deleteLater()`` しても
    循環参照に残ったままになる。これをワーカースレッドで走る Python コード
    （走査ジョブなど）の割り当てが引き起こした GC が回収すると、``QFileSystemModel``
    などを GUI スレッド以外で破棄することになり、トレースバックもダンプも残さずに
    プロセスごと落ちた（大量ファイルのストレステストの teardown で断続的に発生）。
    """
    yield
    gc.collect()
    pool = QThreadPool.globalInstance()
    if pool is None:
        return
    pool.clear()  # 未開始のジョブは捨てる
    if not pool.waitForDone(POOL_DRAIN_TIMEOUT_MS):
        pytest.fail(
            f"テスト終了時にバックグラウンドジョブが残りました (active={pool.activeThreadCount()})"
        )
