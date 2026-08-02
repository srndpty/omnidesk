"""大量ファイルのフォルダでGUIスレッドが固まらないことを確認するテスト。

利用者から報告されたフリーズは「大量ファイルのフォルダ」で起きていた。
ここでは実際に多数のファイルを作り、GUIスレッドで走る処理の所要時間に
上限を設ける。閾値は環境差を吸収できるよう緩め（体感でひっかかる水準）に
してあり、O(N log N) 回の QFileInfo 生成のような退行だけを捕まえる。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PyQt6.QtCore import QPoint, QRect, QSize, Qt

from omnidesk.ui.file_browser_tab import FileBrowserTab

ENTRY_COUNT = 3000
SORT_BUDGET_MS = 3000
RUBBER_BAND_BUDGET_MS = 1500

pytestmark = pytest.mark.stress


@pytest.fixture
def large_directory(tmp_path: Path) -> Path:
    for index in range(ENTRY_COUNT):
        (tmp_path / f"file{index:05d}.txt").write_bytes(b"x")
    return tmp_path


def _open_tab(qtbot, directory: Path) -> FileBrowserTab:
    tab = FileBrowserTab()
    qtbot.addWidget(tab)
    tab.navigate_to(directory)
    qtbot.waitUntil(
        lambda: tab._model.rowCount(tab._model.index(str(directory))) >= ENTRY_COUNT,
        timeout=60000,
    )
    return tab


@pytest.mark.timeout(180)
def test_sorting_a_large_directory_stays_responsive(qtbot, large_directory: Path) -> None:
    tab = _open_tab(qtbot, large_directory)

    started = time.monotonic()
    tab._model.invalidate()
    elapsed_ms = round((time.monotonic() - started) * 1000)

    assert elapsed_ms < SORT_BUDGET_MS, f"並べ替えに {elapsed_ms}ms かかりました"


@pytest.mark.timeout(180)
def test_repeated_sorting_reuses_cached_metadata(qtbot, large_directory: Path) -> None:
    """2回目以降の並べ替えが、初回より遅くならないこと。

    キャッシュが毎回捨てられていると、ここが初回と同じコストのままになる。
    """
    tab = _open_tab(qtbot, large_directory)
    tab._model.invalidate()

    started = time.monotonic()
    for _ in range(3):
        tab._model.invalidate()
    elapsed_ms = round((time.monotonic() - started) * 1000)

    assert elapsed_ms < SORT_BUDGET_MS, f"再並べ替え3回に {elapsed_ms}ms かかりました"


@pytest.mark.timeout(180)
def test_rubber_band_selection_does_not_scan_every_row(qtbot, large_directory: Path) -> None:
    """ラバーバンド選択が、行数に比例して重くならないこと。"""
    tab = _open_tab(qtbot, large_directory)
    view = tab._tree_view
    view.resize(QSize(800, 600))
    viewport = view.viewport()
    assert viewport is not None

    view._rubber_band_origin = QPoint(0, 0)
    view._rubber_band.setGeometry(QRect(0, 0, 400, 300))

    started = time.monotonic()
    for _ in range(20):
        view._update_rubber_band_selection(Qt.KeyboardModifier.NoModifier)
    elapsed_ms = round((time.monotonic() - started) * 1000)

    assert elapsed_ms < RUBBER_BAND_BUDGET_MS, f"ラバーバンド選択20回に {elapsed_ms}ms かかりました"


@pytest.mark.timeout(180)
def test_visible_thumbnail_requests_do_not_stat_every_row(qtbot, large_directory: Path) -> None:
    """可視サムネイル要求が、可視範囲ぶんのコストで収まること。"""
    tab = _open_tab(qtbot, large_directory)
    tab.activate()

    started = time.monotonic()
    for _ in range(10):
        tab._request_visible_thumbnails(scrolling=False)
    elapsed_ms = round((time.monotonic() - started) * 1000)

    assert elapsed_ms < RUBBER_BAND_BUDGET_MS, f"可視要求10回に {elapsed_ms}ms かかりました"
