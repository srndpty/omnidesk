from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize

from omnidesk.ui.file_browser_media_mode import (
    calculate_grid_size,
    is_media_heavy_directory,
    media_mode_button_text,
)
from omnidesk.ui.file_browser_navigation import (
    navigation_target,
    path_to_focus_after_go_up,
    resolve_address_path,
    should_record_history,
)
from omnidesk.ui.file_browser_selection import (
    has_selection_path_in_directory,
    pending_selection_action,
)


def test_navigation_target_uses_directory_or_parent(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    file_path = folder / "file.txt"
    folder.mkdir()
    file_path.write_text("file", encoding="utf-8")

    assert navigation_target(folder) == folder
    assert navigation_target(file_path) == folder


def test_should_record_history_respects_history_flag_and_same_path(tmp_path: Path) -> None:
    current = tmp_path / "current"
    other = tmp_path / "other"
    current.mkdir()
    other.mkdir()

    assert not should_record_history(current, other, from_history=True)
    assert not should_record_history(current, current, from_history=False)
    assert should_record_history(current, other, from_history=False)


def test_path_to_focus_after_go_up_returns_parent_and_current(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()

    assert path_to_focus_after_go_up(child) == (tmp_path, child)


def test_resolve_address_path_expands_env_and_relative_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OMNIDESK_TEST_FOLDER", "expanded")

    assert resolve_address_path("relative.txt", tmp_path) == tmp_path / "relative.txt"
    assert resolve_address_path("%OMNIDESK_TEST_FOLDER%\\file.txt", tmp_path) == tmp_path / "expanded" / "file.txt"


def test_pending_selection_action_covers_all_branches(tmp_path: Path) -> None:
    pending = tmp_path / "pending"

    assert pending_selection_action(
        pending,
        pending_exists=True,
        selected_in_current_directory=False,
        pending_select_succeeded=True,
    ) == "selected_pending"
    assert pending_selection_action(
        pending,
        pending_exists=True,
        selected_in_current_directory=False,
        pending_select_succeeded=False,
    ) == "wait_for_pending"
    assert pending_selection_action(
        pending,
        pending_exists=False,
        selected_in_current_directory=True,
        pending_select_succeeded=False,
    ) == "select_first"
    assert pending_selection_action(
        None,
        pending_exists=False,
        selected_in_current_directory=True,
        pending_select_succeeded=False,
    ) == "keep_current"
    assert pending_selection_action(
        None,
        pending_exists=False,
        selected_in_current_directory=False,
        pending_select_succeeded=False,
    ) == "select_first"


def test_has_selection_path_in_directory(tmp_path: Path) -> None:
    selected = tmp_path / "selected.txt"
    selected.write_text("selected", encoding="utf-8")

    assert has_selection_path_in_directory(selected, tmp_path)
    assert not has_selection_path_in_directory(tmp_path / "missing.txt", tmp_path)


def test_calculate_grid_size_and_button_text() -> None:
    assert calculate_grid_size(160, 12) == QSize(184, 208)
    assert media_mode_button_text(True) == ("List View", "Switch to list view (details)")
    assert media_mode_button_text(False) == ("Tile View", "Switch to tile view (thumbnails)")


def test_is_media_heavy_directory_thresholds(tmp_path: Path) -> None:
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "a.jpg").write_text("media", encoding="utf-8")
    (media_dir / "b.txt").write_text("text", encoding="utf-8")

    assert is_media_heavy_directory(
        media_dir,
        {".jpg"},
        ratio_threshold=0.6,
        min_count=4,
        scan_limit=60,
    )

    sparse_dir = tmp_path / "sparse"
    sparse_dir.mkdir()
    for index in range(5):
        (sparse_dir / f"{index}.txt").write_text("text", encoding="utf-8")
    (sparse_dir / "only.jpg").write_text("media", encoding="utf-8")

    assert not is_media_heavy_directory(
        sparse_dir,
        {".jpg"},
        ratio_threshold=0.6,
        min_count=4,
        scan_limit=60,
    )


def test_is_media_heavy_directory_handles_os_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(OSError()))

    assert not is_media_heavy_directory(
        tmp_path,
        {".jpg"},
        ratio_threshold=0.6,
        min_count=4,
        scan_limit=60,
    )
