from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from pytest_mock import MockerFixture

from omnidesk.ui.file_operations import (
    FileOperationRequest,
    create_file,
    create_folder,
    delete_paths,
    execute_file_operation,
    is_dangerous_operation_path,
    is_plain_child_name,
    perform_copy_or_move,
    perform_copy_or_move_with_result,
    rename_path,
    validate_copy_or_move,
)


def test_file_operations_work_on_pyfakefs(fs) -> None:
    workspace = Path("C:/workspace")
    source = workspace / "source"
    destination = workspace / "destination"
    source.mkdir(parents=True)
    (source / "file.txt").write_text("copy", encoding="utf-8")

    errors = perform_copy_or_move([source / "file.txt"], destination, move=False)

    assert errors == []
    assert (destination / "file.txt").read_text(encoding="utf-8") == "copy"


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
    assert renamed is not None
    assert renamed == tmp_path / "renamed.txt"
    assert renamed.read_text(encoding="utf-8") == "original"


def test_create_file_and_folder_use_copy_names_for_conflicts(tmp_path: Path) -> None:
    (tmp_path / "New File.txt").write_text("existing", encoding="utf-8")
    (tmp_path / "New Folder").mkdir()

    file_path, file_error = create_file(tmp_path, "New File.txt")
    folder_path, folder_error = create_folder(tmp_path, "New Folder")

    assert file_error is None
    assert file_path is not None
    assert file_path == tmp_path / "New File - Copy 1.txt"
    assert file_path.exists()
    assert folder_error is None
    assert folder_path is not None
    assert folder_path == tmp_path / "New Folder - Copy 1"
    assert folder_path.is_dir()


def test_plain_child_name_rejects_paths_and_empty_values() -> None:
    assert is_plain_child_name("file.txt")
    assert not is_plain_child_name("")
    assert not is_plain_child_name("   ")
    assert not is_plain_child_name("nested/file.txt")
    assert not is_plain_child_name(r"nested\file.txt")


def test_create_and_rename_reject_path_separator_names(tmp_path: Path) -> None:
    original = tmp_path / "original.txt"
    original.write_text("original", encoding="utf-8")

    renamed, rename_error = rename_path(original, "nested/renamed.txt")
    created_file, file_error = create_file(tmp_path, "nested/file.txt")
    created_folder, folder_error = create_folder(tmp_path, "nested/folder")

    assert renamed is None
    assert rename_error == "Name must not contain path separators."
    assert created_file is None
    assert file_error == "Name must not contain path separators."
    assert created_folder is None
    assert folder_error == "Name must not contain path separators."
    assert not (tmp_path / "nested").exists()


def test_dangerous_operation_path_detects_roots() -> None:
    assert is_dangerous_operation_path(Path(Path.cwd().anchor))


def test_delete_paths_refuses_dangerous_path(mocker: MockerFixture, tmp_path: Path) -> None:
    mocker.patch(
        "omnidesk.ui.file_operations.is_dangerous_operation_path",
        lambda path: path == tmp_path,
    )

    errors = delete_paths([tmp_path])

    assert len(errors) == 1
    assert "Refusing to delete dangerous path" in errors[0]
    assert tmp_path.exists()


def test_copy_or_move_refuses_dangerous_source(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    src = tmp_path / "source.txt"
    dest = tmp_path / "dest"
    src.write_text("source", encoding="utf-8")
    mocker.patch(
        "omnidesk.ui.file_operations.is_dangerous_operation_path",
        lambda path: path == src,
    )

    errors = perform_copy_or_move([src], dest, move=False)

    assert len(errors) == 1
    assert "Refusing to operate on dangerous path" in errors[0]
    assert not (dest / src.name).exists()


def test_copy_or_move_result_reports_changed_dirs_for_partial_success(tmp_path: Path) -> None:
    copied = tmp_path / "copied.txt"
    missing = tmp_path / "missing.txt"
    dest = tmp_path / "dest"
    copied.write_text("copied", encoding="utf-8")

    result = perform_copy_or_move_with_result([copied, missing], dest, move=False)

    assert len(result.errors) == 1
    assert "Missing:" in result.errors[0]
    assert result.changed_dirs == [dest]
    assert (dest / copied.name).read_text(encoding="utf-8") == "copied"


def test_copy_directory_into_own_descendant_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("source", encoding="utf-8")
    descendant = source / "child"
    descendant.mkdir()

    result = perform_copy_or_move_with_result([source], descendant, move=False)

    assert len(result.errors) == 1
    assert "folder into itself" in result.errors[0]
    assert not (descendant / "source").exists()


def test_move_directory_into_own_descendant_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    descendant = source / "child"
    descendant.mkdir()

    result = perform_copy_or_move_with_result([source], descendant, move=True)

    assert len(result.errors) == 1
    assert "folder into itself" in result.errors[0]
    assert source.exists()


def test_execute_file_operation_can_cancel_before_start(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    dest = tmp_path / "dest"
    request = FileOperationRequest([source], dest, "copy")

    result = execute_file_operation(request, is_cancelled=lambda: True)

    assert result.cancelled
    assert result.errors == []
    assert not dest.exists()


def test_execute_file_operation_rejects_unknown_mode(tmp_path: Path) -> None:
    request = FileOperationRequest([], tmp_path, cast(Any, "archive"))

    result = execute_file_operation(request)

    assert result.errors == ["Unsupported file operation mode: archive"]


def test_validate_copy_or_move_detects_same_target_with_case_difference(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Source.txt"
    source.write_text("source", encoding="utf-8")

    error = validate_copy_or_move(source.with_name("SOURCE.txt"), tmp_path, move=False)

    assert error is not None
    assert "Source and destination are the same" in error
