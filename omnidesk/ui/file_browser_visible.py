"""Helpers for discovering visible indexes in file browser views."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect


def tile_probe_step(icon_width: int) -> int:
    """Return a small viewport-local probe stride for tile view scanning."""
    return max(16, min(32, icon_width // 3 or 24))


def tile_probe_points(rect: QRect, step: int) -> list[QPoint]:
    """Return scan points plus strategic viewport points for a tile view."""
    points: list[QPoint] = []
    y = rect.top()
    while y <= rect.bottom():
        x = rect.left()
        while x <= rect.right():
            points.append(QPoint(x, y))
            x += step
        y += step

    points.extend(
        (
            rect.topLeft(),
            rect.topRight(),
            rect.bottomLeft(),
            rect.bottomRight(),
            rect.center(),
        )
    )
    return points


def index_identity(row: int, column: int, internal_id: int) -> tuple[int, int, int]:
    """Return the stable identity used to de-duplicate probed indexes."""
    return (row, column, internal_id)
