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


def visible_row_range(
    first_row: int,
    last_row: int,
    row_count: int,
    *,
    margin: int = 1,
) -> range:
    """可視範囲として走査すべき行番号の範囲を返す。

    ``first_row`` / ``last_row`` はビューの ``indexAt()`` から得た行番号で、
    ビューポート端にアイテムが無い場合は ``-1`` になり得る。その場合は
    それぞれ先頭・末尾へ丸める。``margin`` は端の行が部分的にしか
    見えていない場合に取りこぼさないための余白。

    これは大量ファイルのフォルダで全行を走査しないための絞り込みであり、
    ここで返す範囲の外にある行は、そもそもビューポートと交差しない。
    """
    if row_count <= 0:
        return range(0)
    start = 0 if first_row < 0 else first_row
    end = row_count - 1 if last_row < 0 else last_row
    if end < start:
        start, end = end, start
    start = max(0, start - margin)
    end = min(row_count - 1, end + margin)
    return range(start, end + 1)
