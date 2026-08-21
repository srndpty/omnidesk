from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRect

from omnidesk.ui.file_browser_helpers import deletion_replacement_path
from omnidesk.ui.file_browser_selection import (
    rubber_band_intersecting_rows,
    rubber_band_target_rows,
)


def _paths(tmp_path: Path, names: list[str]) -> list[Path]:
    paths: list[Path] = []
    for name in names:
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        paths.append(path)
    return paths


def test_deletion_replacement_prefers_previous_item(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ["a.txt", "b.txt", "c.txt", "d.txt"])

    assert (
        deletion_replacement_path(
            lambda row: paths[row],
            len(paths),
            selected_rows={2},
            deleted_paths=[paths[2]],
        )
        == paths[1]
    )


def test_deletion_replacement_falls_back_to_next_item(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ["a.txt", "b.txt", "c.txt"])

    assert (
        deletion_replacement_path(
            lambda row: paths[row],
            len(paths),
            selected_rows={0, 1},
            deleted_paths=[paths[0], paths[1]],
        )
        == paths[2]
    )


def test_deletion_replacement_returns_none_when_no_item_remains(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ["a.txt", "b.txt"])

    assert (
        deletion_replacement_path(
            lambda row: paths[row],
            len(paths),
            selected_rows={0, 1},
            deleted_paths=[paths[0], paths[1]],
        )
        is None
    )


def test_rubber_band_intersecting_rows_ignores_offscreen_and_missed_rows() -> None:
    viewport = QRect(0, 0, 100, 100)
    selection = QRect(10, 10, 25, 25)
    row_rects = [
        (0, QRect(0, 0, 100, 8)),
        (1, QRect(0, 10, 100, 10)),
        (2, QRect(0, 80, 100, 10)),
        (3, QRect(0, 140, 100, 10)),
    ]

    assert rubber_band_intersecting_rows(selection, viewport, row_rects) == {1}


def test_rubber_band_target_rows_replaces_without_control() -> None:
    current = {(1, 0), (1, 1), (2, 0), (2, 1)}
    previous = {(2, 0), (2, 1), (3, 0), (3, 1)}

    assert rubber_band_target_rows(current, previous, control_pressed=False) == {1, 2}


def test_rubber_band_target_rows_toggles_with_control() -> None:
    current = {(1, 0), (1, 1), (2, 0), (2, 1)}
    previous = {(2, 0), (2, 1), (3, 0), (3, 1)}

    assert rubber_band_target_rows(current, previous, control_pressed=True) == {1, 3}


def test_rubber_band_target_rows_preserves_partial_previous_selection_behavior() -> None:
    current = {(2, 0), (2, 1)}
    previous = {(2, 0)}

    assert rubber_band_target_rows(current, previous, control_pressed=True) == {2}


def test_deletion_replacement_only_reads_rows_it_needs(tmp_path: Path) -> None:
    """全行を事前に列挙しないこと。

    削除確認の前に7,500件ぶんのモデル往復が走っていたのが、削除操作が数秒
    かかっていた原因のひとつ。探索は選択行の隣から始まるので、実際に引くのは
    ごく少数の行で足りる。
    """
    paths = _paths(tmp_path, [f"{index:03d}.txt" for index in range(100)])
    requested: list[int] = []

    def path_at(row: int) -> Path:
        requested.append(row)
        return paths[row]

    assert (
        deletion_replacement_path(
            path_at,
            len(paths),
            selected_rows={50},
            deleted_paths=[paths[50]],
        )
        == paths[49]
    )
    assert requested == [49]
