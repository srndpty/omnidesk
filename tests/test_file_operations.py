from __future__ import annotations

from pathlib import Path

from omnidesk.ui.file_operations import create_file, create_folder, rename_path


def test_rename_path_reports_conflict(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    target = tmp_path / "target.txt"
    original.write_text("original", encoding="utf-8")
    target.write_text("target", encoding="utf-8")

    renamed, error = rename_path(original, target.name)

    assert renamed is None
    assert error == f"{target} already exists."
    assert original.exists()


def test_rename_path_success(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    original.write_text("original", encoding="utf-8")

    renamed, error = rename_path(original, "renamed.txt")

    assert error is None
    assert renamed == tmp_path / "renamed.txt"
    assert renamed.read_text(encoding="utf-8") == "original"


def test_create_file_and_folder_use_copy_names_for_conflicts(tmp_path: Path) -> None:
    (tmp_path / "New File.txt").write_text("existing", encoding="utf-8")
    (tmp_path / "New Folder").mkdir()

    file_path, file_error = create_file(tmp_path, "New File.txt")
    folder_path, folder_error = create_folder(tmp_path, "New Folder")

    assert file_error is None
    assert file_path == tmp_path / "New File - Copy 1.txt"
    assert file_path.exists()
    assert folder_error is None
    assert folder_path == tmp_path / "New Folder - Copy 1"
    assert folder_path.is_dir()
