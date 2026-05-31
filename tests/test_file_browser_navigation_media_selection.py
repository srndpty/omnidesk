from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QSize

from omnidesk.ui.file_browser_media_mode import (
    calculate_grid_size,
    is_media_heavy_directory,
    media_mode_button_text,
)
from omnidesk.ui.file_browser_navigation import (
    directory_fingerprint,
    directory_fingerprint_changed,
    navigation_history_step,
    navigation_target,
    path_to_focus_after_go_up,
    resolve_address_path,
    same_navigation_path,
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


def test_navigation_path_comparison_uses_resolved_paths(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()

    assert same_navigation_path(current, current / ".." / "current")
    assert same_navigation_path(tmp_path, current / "..")


def test_directory_fingerprint_detects_directory_metadata_change(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    before = directory_fingerprint(folder)

    assert before is not None
    assert not directory_fingerprint_changed(folder, before)

    current_stat = folder.stat()
    atime = getattr(current_stat, "st_atime_ns", int(current_stat.st_atime * 1e9))
    os.utime(folder, ns=(atime, before[0] + 1_000_000))

    assert directory_fingerprint_changed(folder, before)


def test_navigation_history_step_moves_between_back_and_forward_stacks(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    third = tmp_path / "third"

    back_step = navigation_history_step([first, second], [], third, direction="back")

    assert back_step is not None
    assert back_step.target == second
    assert back_step.back_history == [first]
    assert back_step.forward_history == [third]

    forward_step = navigation_history_step(
        back_step.back_history,
        back_step.forward_history,
        back_step.target,
        direction="forward",
    )

    assert forward_step is not None
    assert forward_step.target == third
    assert forward_step.back_history == [first, second]
    assert forward_step.forward_history == []


def test_navigation_history_step_returns_none_without_target(tmp_path: Path) -> None:
    current = tmp_path / "current"

    assert navigation_history_step([], [], current, direction="back") is None
    assert navigation_history_step([], [], current, direction="forward") is None


def test_path_to_focus_after_go_up_returns_parent_and_current(tmp_path: Path) -> None:
    child = tmp_path / "child"
    child.mkdir()

    assert path_to_focus_after_go_up(child) == (tmp_path, child)


def test_resolve_address_path_expands_env_and_relative_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OMNIDESK_TEST_FOLDER", "expanded")

    assert resolve_address_path("relative.txt", tmp_path) == tmp_path / "relative.txt"
    assert (
        resolve_address_path("%OMNIDESK_TEST_FOLDER%\\file.txt", tmp_path)
        == tmp_path / "expanded" / "file.txt"
    )


def test_pending_selection_action_covers_all_branches(tmp_path: Path) -> None:
    pending = tmp_path / "pending"

    assert (
        pending_selection_action(
            pending,
            pending_exists=True,
            selected_in_current_directory=False,
            pending_select_succeeded=True,
        )
        == "selected_pending"
    )
    assert (
        pending_selection_action(
            pending,
            pending_exists=True,
            selected_in_current_directory=False,
            pending_select_succeeded=False,
        )
        == "wait_for_pending"
    )
    assert (
        pending_selection_action(
            pending,
            pending_exists=False,
            selected_in_current_directory=True,
            pending_select_succeeded=False,
        )
        == "select_first"
    )
    assert (
        pending_selection_action(
            None,
            pending_exists=False,
            selected_in_current_directory=True,
            pending_select_succeeded=False,
        )
        == "keep_current"
    )
    assert (
        pending_selection_action(
            None,
            pending_exists=False,
            selected_in_current_directory=False,
            pending_select_succeeded=False,
        )
        == "select_first"
    )


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
