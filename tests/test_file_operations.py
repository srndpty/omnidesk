from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest
from pytest_mock import MockerFixture

from omnidesk.ui import file_operations
from omnidesk.ui.file_operations import (
    MAX_NAME_COMPONENT_UNITS,
    MAX_PATH_UNITS,
    FileOperationRequest,
    clip_child_name,
    create_file,
    create_folder,
    delete_paths,
    delete_paths_with_result,
    execute_file_operation,
    is_dangerous_operation_path,
    is_plain_child_name,
    name_exceeds_limits,
    perform_copy_or_move,
    perform_copy_or_move_with_result,
    rename_path,
    validate_copy_or_move,
)


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_file_operations_work_on_tmp_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
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


def test_clip_child_name_keeps_short_names_unchanged() -> None:
    parent = Path("C:/Users/lambe/Pictures")
    assert clip_child_name(parent, "photo.png") == "photo.png"


def test_clip_child_name_trims_overlong_name_and_keeps_extension() -> None:
    parent = Path("C:/Users/lambe/OneDrive/Pictures/Screenshots")
    name = "スクリーンショット 2025-02-27 204923 " * 30 + ".png"

    clipped = clip_child_name(parent, name)

    assert clipped.endswith(".png")
    assert len(clipped) < len(name)
    assert _utf16_units(clipped) <= MAX_NAME_COMPONENT_UNITS
    # The whole path must stay within the classic MAX_PATH budget.
    assert _utf16_units(str(parent)) + 1 + _utf16_units(clipped) <= MAX_PATH_UNITS
    # No invalid trailing space/dot before the extension.
    assert not clipped[: -len(".png")].endswith((" ", "."))


def test_clip_child_name_folder_does_not_treat_dot_as_extension() -> None:
    parent = Path("C:/data")
    name = "あ" * 400 + ".tar.gz"

    clipped = clip_child_name(parent, name, keep_extension=False)

    assert "." not in clipped  # whole name treated as a stem and trimmed
    assert _utf16_units(clipped) <= MAX_NAME_COMPONENT_UNITS


def test_clip_child_name_when_extension_exceeds_budget() -> None:
    parent = Path("C:/data")
    name = "a." + "x" * 500  # the "extension" alone blows the budget

    clipped = clip_child_name(parent, name)

    assert _utf16_units(clipped) <= MAX_NAME_COMPONENT_UNITS
    assert _utf16_units(str(parent)) + 1 + _utf16_units(clipped) <= MAX_PATH_UNITS


def test_clip_child_name_uses_placeholder_when_stem_is_only_dots_and_spaces() -> None:
    parent = Path("C:/data")
    name = (" . " * 200) + ".png"

    clipped = clip_child_name(parent, name)

    stem = clipped[: -len(".png")]
    assert stem  # not empty
    assert not stem.endswith((" ", "."))
    assert _utf16_units(clipped) <= MAX_NAME_COMPONENT_UNITS


def test_name_exceeds_limits() -> None:
    parent = Path("C:/data")
    assert not name_exceeds_limits(parent, "photo.png")
    assert name_exceeds_limits(parent, "あ" * 500 + ".png")


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


def test_copy_file_into_same_directory_uses_conflict_safe_copy_name(tmp_path: Path) -> None:
    source = tmp_path / "copied.txt"
    source.write_text("copied", encoding="utf-8")

    result = perform_copy_or_move_with_result([source], tmp_path, move=False)

    assert result.errors == []
    assert result.changed_dirs == [tmp_path]
    assert source.read_text(encoding="utf-8") == "copied"
    assert (tmp_path / "copied - Copy 1.txt").read_text(encoding="utf-8") == "copied"


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


def test_copy_directory_into_new_own_descendant_does_not_create_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    descendant = source / "newchild"

    result = perform_copy_or_move_with_result([source], descendant, move=False)

    assert len(result.errors) == 1
    assert "folder into itself" in result.errors[0]
    assert result.changed_dirs == []
    assert not descendant.exists()


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


@pytest.mark.skipif(os.name != "nt", reason="case-insensitive path behavior is Windows-specific")
def test_validate_copy_or_move_detects_same_move_target_with_case_difference(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Source.txt"
    source.write_text("source", encoding="utf-8")

    error = validate_copy_or_move(source.with_name("SOURCE.txt"), tmp_path, move=True)

    assert error is not None
    assert "Source and destination are the same" in error


def test_delete_paths_calls_send2trash_for_existing_path(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")
    mock_send2trash = mocker.patch("omnidesk.ui.file_operations.send2trash")

    result = delete_paths_with_result([file_path])

    mock_send2trash.assert_called_once_with([str(file_path)])
    assert result.errors == []
    assert result.changed_dirs == [file_path.parent]


def test_delete_paths_moves_everything_in_one_shell_call(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """複数削除を1回のシェル呼び出しにまとめること。

    1件ずつ呼ぶとWindowsのシェル操作の固定コストが件数ぶんかかる（14件で実測515ms）
    うえ、ディレクトリの変更通知も件数ぶん飛ぶ。通知のたびに QFileSystemModel が
    ディレクトリ全体を再走査するため、大量ファイルのフォルダでは再走査が数秒に
    わたって繰り返される。
    """
    paths = []
    for index in range(14):
        path = tmp_path / f"f{index:02d}.txt"
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    mock_send2trash = mocker.patch("omnidesk.ui.file_operations.send2trash")

    result = delete_paths_with_result(paths)

    mock_send2trash.assert_called_once_with([str(path) for path in paths])
    assert result.errors == []
    assert result.changed_dirs == [tmp_path] * len(paths)


def test_delete_paths_retries_individually_when_the_batch_fails(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """まとめての呼び出しが失敗したら、原因を特定するため1件ずつ再試行すること。"""
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    for path in (first, second):
        path.write_text("x", encoding="utf-8")

    def fake_send2trash(target):
        if isinstance(target, list):
            raise OSError("batch failed")
        if target == str(second):
            raise OSError("locked")
        Path(target).unlink()

    mocker.patch("omnidesk.ui.file_operations.send2trash", side_effect=fake_send2trash)

    result = delete_paths_with_result([first, second])

    assert not first.exists()
    assert result.changed_dirs == [tmp_path]
    assert len(result.errors) == 1
    assert str(second) in result.errors[0]


def test_delete_paths_individual_retry_treats_already_gone_as_done(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """まとめての呼び出しが途中まで成功していたら、その分をエラーにしないこと。"""
    gone = tmp_path / "gone.txt"
    remaining = tmp_path / "remaining.txt"
    for path in (gone, remaining):
        path.write_text("x", encoding="utf-8")

    def fake_send2trash(target):
        if isinstance(target, list):
            # 1件目だけ移動できた状態で失敗する。
            gone.unlink()
            raise OSError("batch failed halfway")
        Path(target).unlink()

    mocker.patch("omnidesk.ui.file_operations.send2trash", side_effect=fake_send2trash)

    result = delete_paths_with_result([gone, remaining])

    assert result.errors == []
    assert result.changed_dirs == [tmp_path, tmp_path]
    assert not remaining.exists()


def test_delete_paths_skips_send2trash_for_dangerous_path(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    mocker.patch(
        "omnidesk.ui.file_operations.is_dangerous_operation_path",
        lambda path: path == tmp_path,
    )
    mock_send2trash = mocker.patch("omnidesk.ui.file_operations.send2trash")

    result = delete_paths_with_result([tmp_path])

    mock_send2trash.assert_not_called()
    assert len(result.errors) == 1
    assert "Refusing to delete dangerous path" in result.errors[0]


def test_delete_paths_reports_missing_path_without_calling_send2trash(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    missing = tmp_path / "missing.txt"
    mock_send2trash = mocker.patch("omnidesk.ui.file_operations.send2trash")

    result = delete_paths_with_result([missing])

    mock_send2trash.assert_not_called()
    assert len(result.errors) == 1
    assert str(missing) in result.errors[0]
    assert result.changed_dirs == []


def test_delete_paths_passes_broken_symlink_to_send2trash(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    broken_link = tmp_path / "broken"
    try:
        broken_link.symlink_to(tmp_path / "nonexistent")
    except (OSError, NotImplementedError):
        pytest.skip("Cannot create symlinks in this environment")
    mock_send2trash = mocker.patch("omnidesk.ui.file_operations.send2trash")

    result = delete_paths_with_result([broken_link])

    mock_send2trash.assert_called_once_with(str(broken_link))
    assert result.errors == []


def test_delete_paths_chunks_large_selections_so_cancellation_still_works(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """巨大な選択でも、途中でキャンセルできる粒度を残すこと。

    1回のシェル呼び出し自体は中断できないので、区切りを入れてその境目で見る。
    """
    paths = []
    for index in range(file_operations.TRASH_BATCH_SIZE + 5):
        path = tmp_path / f"f{index:04d}.txt"
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    mock_send2trash = mocker.patch("omnidesk.ui.file_operations.send2trash")
    calls: list[int] = []
    mock_send2trash.side_effect = lambda batch: calls.append(len(batch))

    result = delete_paths_with_result(paths)

    assert calls == [file_operations.TRASH_BATCH_SIZE, 5]
    assert result.errors == []


def test_delete_paths_stops_between_batches_when_cancelled(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    paths = []
    for index in range(file_operations.TRASH_BATCH_SIZE * 2):
        path = tmp_path / f"f{index:04d}.txt"
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    calls: list[int] = []
    mocker.patch(
        "omnidesk.ui.file_operations.send2trash",
        side_effect=lambda batch: calls.append(len(batch)),
    )
    # 最初のバッチが終わったところでキャンセルされる状況を作る。
    seen = {"checks": 0}

    def is_cancelled() -> bool:
        seen["checks"] += 1
        return bool(calls)

    result = delete_paths_with_result(paths, is_cancelled=is_cancelled)

    assert calls == [file_operations.TRASH_BATCH_SIZE]
    assert result.cancelled is True


def test_delete_paths_batch_failure_does_not_stop_later_batches(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """あるバッチが失敗しても、残りのバッチは処理すること。"""
    paths = []
    for index in range(file_operations.TRASH_BATCH_SIZE + 2):
        path = tmp_path / f"f{index:04d}.txt"
        path.write_text("x", encoding="utf-8")
        paths.append(path)

    def fake_send2trash(target):
        if isinstance(target, list) and len(target) == file_operations.TRASH_BATCH_SIZE:
            raise OSError("first batch failed")
        for item in target if isinstance(target, list) else [target]:
            Path(item).unlink()

    mocker.patch("omnidesk.ui.file_operations.send2trash", side_effect=fake_send2trash)

    result = delete_paths_with_result(paths)

    # 1件ずつの再試行で最初のバッチも片付き、最後の2件も消える。
    assert result.errors == []
    assert not any(path.exists() for path in paths)
