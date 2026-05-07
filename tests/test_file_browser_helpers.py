from __future__ import annotations

from pathlib import Path

from omnidesk.ui.file_browser_helpers import (
    delete_paths,
    is_within,
    perform_copy_or_move,
    resolve_destination,
    resolve_windows_program,
)


def test_resolve_destination_returns_original_when_available(tmp_path: Path) -> None:
    assert resolve_destination(tmp_path, "new.txt", move=False) == tmp_path / "new.txt"


def test_resolve_destination_generates_copy_name(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("one", encoding="utf-8")
    (tmp_path / "file - Copy 1.txt").write_text("two", encoding="utf-8")

    assert resolve_destination(tmp_path, "file.txt", move=False) == tmp_path / "file - Copy 2.txt"


def test_resolve_destination_rejects_move_conflict(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("one", encoding="utf-8")

    try:
        resolve_destination(tmp_path, "file.txt", move=True)
    except ValueError as exc:
        assert str(exc) == "Destination already has file.txt"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("move conflict should raise")


def test_is_within_detects_child_path(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = parent / "child"

    assert is_within(child, parent)
    assert not is_within(parent, child)


def test_delete_paths_removes_files_and_directories(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    dir_path = tmp_path / "folder"
    file_path.write_text("delete", encoding="utf-8")
    dir_path.mkdir()
    (dir_path / "nested.txt").write_text("delete", encoding="utf-8")

    assert delete_paths([file_path, dir_path]) == []
    assert not file_path.exists()
    assert not dir_path.exists()


def test_delete_paths_reports_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"

    errors = delete_paths([missing])

    assert len(errors) == 1
    assert str(missing) in errors[0]


def test_perform_copy_or_move_copies_file_and_directory(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    folder = src_dir / "folder"
    src_dir.mkdir()
    folder.mkdir()
    source_file = src_dir / "file.txt"
    source_file.write_text("copy", encoding="utf-8")
    (folder / "nested.txt").write_text("nested", encoding="utf-8")

    errors = perform_copy_or_move([source_file, folder], dest_dir, move=False)

    assert errors == []
    assert (dest_dir / "file.txt").read_text(encoding="utf-8") == "copy"
    assert (dest_dir / "folder" / "nested.txt").read_text(encoding="utf-8") == "nested"
    assert source_file.exists()


def test_perform_copy_or_move_moves_file_and_reports_missing(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    source_file = src_dir / "file.txt"
    missing = src_dir / "missing.txt"
    source_file.write_text("move", encoding="utf-8")

    errors = perform_copy_or_move([source_file, missing], dest_dir, move=True)

    assert errors == [f"Missing: {missing}"]
    assert not source_file.exists()
    assert (dest_dir / "file.txt").read_text(encoding="utf-8") == "move"


def test_perform_copy_or_move_skips_same_directory_move(tmp_path: Path) -> None:
    source_file = tmp_path / "file.txt"
    source_file.write_text("stay", encoding="utf-8")

    assert perform_copy_or_move([source_file], tmp_path, move=True) == []
    assert source_file.read_text(encoding="utf-8") == "stay"


def test_resolve_windows_program_finds_relative_batch(tmp_path: Path) -> None:
    script = tmp_path / "tools" / "run.cmd"
    script.parent.mkdir()
    script.write_text("@echo off", encoding="utf-8")

    resolved, is_batch = resolve_windows_program(
        "tools\\run.cmd",
        tmp_path,
        environ={"PATHEXT": ".EXE;.CMD"},
        which=lambda _program: None,
    )

    assert resolved == str(script)
    assert is_batch


def test_resolve_windows_program_uses_pathext_in_current_directory(tmp_path: Path) -> None:
    executable = tmp_path / "tool.exe"
    executable.write_text("exe", encoding="utf-8")

    resolved, is_batch = resolve_windows_program(
        "tool",
        tmp_path,
        environ={"PATHEXT": ".EXE;.CMD"},
        which=lambda _program: None,
    )

    assert resolved == str(executable)
    assert not is_batch


def test_resolve_windows_program_falls_back_to_path_lookup(tmp_path: Path) -> None:
    resolved, is_batch = resolve_windows_program(
        "external",
        tmp_path,
        environ={"PATHEXT": ".EXE;.CMD"},
        which=lambda program: f"C:/bin/{program}.bat",
    )

    assert resolved == "C:/bin/external.bat"
    assert is_batch


def test_resolve_windows_program_returns_none_when_missing(tmp_path: Path) -> None:
    assert resolve_windows_program(
        "missing",
        tmp_path,
        environ={"PATHEXT": ".EXE"},
        which=lambda _program: None,
    ) == (None, False)
