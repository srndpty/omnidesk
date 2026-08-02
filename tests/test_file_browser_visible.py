from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect

from omnidesk.ui.file_browser_visible import (
    index_identity,
    tile_probe_points,
    tile_probe_step,
    visible_row_range,
)


def test_tile_probe_step_uses_small_bounded_stride() -> None:
    assert tile_probe_step(0) == 24
    assert tile_probe_step(30) == 16
    assert tile_probe_step(96) == 32
    assert tile_probe_step(300) == 32


def test_tile_probe_points_scan_grid_and_include_strategic_points() -> None:
    rect = QRect(0, 0, 40, 40)
    points = tile_probe_points(rect, 20)

    assert points[:4] == [
        QPoint(0, 0),
        QPoint(20, 0),
        QPoint(0, 20),
        QPoint(20, 20),
    ]
    assert rect.topRight() in points
    assert rect.bottomLeft() in points
    assert rect.bottomRight() in points
    assert rect.center() in points


def test_index_identity_matches_row_column_and_internal_id() -> None:
    assert index_identity(1, 2, 3) == (1, 2, 3)


def test_visible_row_range_adds_margin_around_visible_rows() -> None:
    assert list(visible_row_range(10, 20, 1000)) == list(range(9, 22))


def test_visible_row_range_clamps_to_model_bounds() -> None:
    assert list(visible_row_range(0, 4, 5)) == [0, 1, 2, 3, 4]


def test_visible_row_range_treats_missing_indexes_as_edges() -> None:
    # ビューポート端にアイテムが無いと indexAt() は -1 を返す。
    assert list(visible_row_range(-1, 3, 100)) == [0, 1, 2, 3, 4]
    assert list(visible_row_range(97, -1, 100)) == [96, 97, 98, 99]
    assert list(visible_row_range(-1, -1, 3)) == [0, 1, 2]


def test_visible_row_range_is_empty_for_empty_model() -> None:
    assert list(visible_row_range(-1, -1, 0)) == []


def test_visible_row_range_orders_reversed_bounds() -> None:
    assert list(visible_row_range(20, 10, 1000)) == list(range(9, 22))
