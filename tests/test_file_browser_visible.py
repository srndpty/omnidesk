from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect

from omnidesk.ui.file_browser_visible import index_identity, tile_probe_points, tile_probe_step


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
