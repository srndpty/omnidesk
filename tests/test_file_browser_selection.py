from __future__ import annotations

from pathlib import Path

from omnidesk.ui.file_browser_helpers import deletion_replacement_path


def _paths(tmp_path: Path, names: list[str]) -> list[Path]:
    paths: list[Path] = []
    for name in names:
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        paths.append(path)
    return paths


def test_deletion_replacement_prefers_previous_item(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ["a.txt", "b.txt", "c.txt", "d.txt"])

    assert deletion_replacement_path(
        paths,
        selected_rows={2},
        deleted_paths={paths[2].resolve()},
    ) == paths[1]


def test_deletion_replacement_falls_back_to_next_item(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ["a.txt", "b.txt", "c.txt"])

    assert deletion_replacement_path(
        paths,
        selected_rows={0, 1},
        deleted_paths={paths[0].resolve(), paths[1].resolve()},
    ) == paths[2]


def test_deletion_replacement_returns_none_when_no_item_remains(tmp_path: Path) -> None:
    paths = _paths(tmp_path, ["a.txt", "b.txt"])

    assert deletion_replacement_path(
        paths,
        selected_rows={0, 1},
        deleted_paths={paths[0].resolve(), paths[1].resolve()},
    ) is None
