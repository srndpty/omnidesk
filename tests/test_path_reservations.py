"""実行中のファイル操作が使うパスの予約（排他）を検証する。

モック内で別の操作を同期的に呼ぶだけでは、判定と登録の間の競合（TOCTOU）は
検出できない。ここでは実際にスレッドを走らせ、1つの操作が書き込み中のまま
止まっている間に、別スレッドの操作がどう扱われるかを確かめる。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from omnidesk.ui import file_operations
from omnidesk.ui.file_operations import FileOperationResult
from omnidesk.ui.path_reservations import (
    BUSY_PATH_MESSAGE,
    RESERVATIONS,
    BusyPathError,
    PathReservations,
)

WAIT_SECONDS = 5


@pytest.fixture(autouse=True)
def _no_reservations_left_behind():
    """どのテストも、終わった時点で予約を残していないこと。"""
    yield
    assert RESERVATIONS._entries == []


class _BlockingCall:
    """呼ばれたら合図を出し、解放されるまで戻らない差し替え用の関数。"""

    def __init__(self, action: Callable[..., object] | None = None) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[tuple[object, ...]] = []
        self._action = action

    def __call__(self, *args: object) -> None:
        self.calls.append(args)
        if self._action is not None:
            self._action(*args)
        self.entered.set()
        assert self.release.wait(WAIT_SECONDS)


def _run_in_thread(func: Callable[[], FileOperationResult]) -> tuple[threading.Thread, list]:
    results: list[FileOperationResult] = []
    thread = threading.Thread(target=lambda: results.append(func()), daemon=True)
    thread.start()
    return thread, results


def _album(tmp_path: Path, name: str = "album") -> Path:
    src = tmp_path / name
    src.mkdir()
    (src / "1.gif").write_text("x", encoding="utf-8")
    return src


# ----------------------------------------------------------------------
# 台帳そのもの
# ----------------------------------------------------------------------
def test_only_one_of_many_threads_reserves_the_same_path(tmp_path: Path) -> None:
    """判定と登録がひとつながりなので、同時に予約しても取れるのは1つだけ。"""
    reservations = PathReservations()
    target = tmp_path / "album - Copy 1"
    threads_count = 8
    start = threading.Barrier(threads_count)
    holding = threading.Event()
    done = threading.Event()
    outcomes: list[str] = []
    lock = threading.Lock()

    def contend() -> None:
        start.wait(WAIT_SECONDS)
        try:
            with reservations.reserve(writes=[target]):
                with lock:
                    outcomes.append("reserved")
                holding.set()
                assert done.wait(WAIT_SECONDS)
        except BusyPathError:
            with lock:
                outcomes.append("busy")

    threads = [threading.Thread(target=contend, daemon=True) for _ in range(threads_count)]
    for thread in threads:
        thread.start()
    assert holding.wait(WAIT_SECONDS)
    # 予約を握っているスレッド以外は、すべて断られて終わっているはず。
    for _ in range(100):
        if len(outcomes) == threads_count:
            break
        threading.Event().wait(0.01)
    done.set()
    for thread in threads:
        thread.join(WAIT_SECONDS)

    assert sorted(outcomes) == ["busy"] * (threads_count - 1) + ["reserved"]
    assert reservations._entries == []


def test_reads_share_but_writes_exclude(tmp_path: Path) -> None:
    reservations = PathReservations()
    folder = tmp_path / "album"
    with reservations.reserve(reads=[folder]):
        # 同じフォルダを別の場所へ同時にコピーするのは許す。
        with reservations.reserve(reads=[folder]):
            pass
        # 読み取り中のものは、書き換えさせない（親も子孫も）。
        for path in (folder, folder / "1.gif", tmp_path):
            with pytest.raises(BusyPathError), reservations.reserve(writes=[path]):
                pass
    with (
        reservations.reserve(writes=[folder]),
        pytest.raises(BusyPathError),
        reservations.reserve(reads=[folder]),
    ):
        pass


def test_overlap_does_not_match_a_sibling_with_a_common_prefix(tmp_path: Path) -> None:
    reservations = PathReservations()
    with reservations.reserve(writes=[tmp_path / "album"]):
        assert reservations.busy_path(tmp_path / "album - Copy 1") is None
        assert reservations.busy_path(tmp_path / "album" / "1.gif") is not None
        assert reservations.busy_path(tmp_path) is not None


def test_failed_reservation_registers_nothing(tmp_path: Path) -> None:
    """一部が重なったら、重ならなかった分も含めて何も登録しない。"""
    reservations = PathReservations()
    with reservations.reserve(writes=[tmp_path / "busy"]):
        with (
            pytest.raises(BusyPathError),
            reservations.reserve(writes=[tmp_path / "free", tmp_path / "busy"]),
        ):
            pass
        assert reservations.busy_path(tmp_path / "free") is None


def test_reservation_is_released_on_exception(tmp_path: Path) -> None:
    reservations = PathReservations()
    with pytest.raises(RuntimeError), reservations.reserve(writes=[tmp_path / "a"]):
        raise RuntimeError("boom")
    assert reservations._entries == []


def test_new_child_skips_names_reserved_by_another_operation(tmp_path: Path) -> None:
    reservations = PathReservations()
    candidates = [tmp_path / "a - Copy 1", tmp_path / "a - Copy 2"]
    with reservations.reserve_new_child(candidates, exists=Path.exists) as first:
        with reservations.reserve_new_child(candidates, exists=Path.exists) as second:
            assert (first, second) == tuple(candidates)
        with (
            pytest.raises(ValueError),
            reservations.reserve_new_child(candidates[:1], exists=Path.exists),
        ):
            pass
    # 親が書き込み中なら、名前を変えても避けられない。
    with (
        reservations.reserve(writes=[tmp_path]),
        pytest.raises(BusyPathError),
        reservations.reserve_new_child(candidates, exists=Path.exists),
    ):
        pass


# ----------------------------------------------------------------------
# 実際のファイル操作（別スレッドで書き込み中のまま止める）
# ----------------------------------------------------------------------
def test_concurrent_copies_of_the_same_folder_get_distinct_targets(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """同じフォルダを同時に2回貼り付けても、同じコピー先を取り合わない。"""
    src = _album(tmp_path)
    blocking = _BlockingCall(lambda _src, dst: Path(dst).mkdir())
    mocker.patch.object(file_operations.shutil, "copytree", side_effect=blocking)

    first, first_results = _run_in_thread(
        lambda: file_operations.perform_copy_or_move_with_result([src], tmp_path, move=False)
    )
    assert blocking.entered.wait(WAIT_SECONDS)
    blocking.entered.clear()
    second, second_results = _run_in_thread(
        lambda: file_operations.perform_copy_or_move_with_result([src], tmp_path, move=False)
    )
    assert blocking.entered.wait(WAIT_SECONDS)
    blocking.release.set()
    first.join(WAIT_SECONDS)
    second.join(WAIT_SECONDS)

    assert first_results[0].errors == []
    assert second_results[0].errors == []
    targets = sorted(Path(call[1]).name for call in blocking.calls)
    assert targets == ["album - Copy 1", "album - Copy 2"]


def test_concurrent_moves_to_the_same_target_only_one_proceeds(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first_src = _album(tmp_path / "a")
    second_src = _album(tmp_path / "b")
    dest = tmp_path / "dest"
    dest.mkdir()
    blocking = _BlockingCall()
    mocker.patch.object(file_operations.shutil, "move", side_effect=blocking)

    first, first_results = _run_in_thread(
        lambda: file_operations.perform_copy_or_move_with_result([first_src], dest, move=True)
    )
    assert blocking.entered.wait(WAIT_SECONDS)
    second = file_operations.perform_copy_or_move_with_result([second_src], dest, move=True)
    blocking.release.set()
    first.join(WAIT_SECONDS)

    assert first_results[0].errors == []
    assert second.errors == [BUSY_PATH_MESSAGE.format(path=dest / "album")]
    assert len(blocking.calls) == 1


def test_nothing_may_touch_a_folder_while_it_is_being_copied(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """コピー中のコピー先には、削除・名前変更・コピー・配下への書き込みを受け付けない。

    コピーの途中でコピー先を削除すると、残りのファイルの書き込みがすべて
    ``FileNotFoundError`` になっていた。
    """
    src = _album(tmp_path)
    other = _album(tmp_path, "other")
    copying = tmp_path / "album - Copy 1"
    trash = mocker.patch.object(file_operations, "send2trash")

    def start_copying(_src: object, dst: object) -> None:
        Path(str(dst)).mkdir()
        (Path(str(dst)) / "1.gif").write_text("x", encoding="utf-8")

    blocking = _BlockingCall(start_copying)
    mocker.patch.object(file_operations.shutil, "copytree", side_effect=blocking)
    real_move = mocker.patch.object(file_operations.shutil, "move")

    thread, results = _run_in_thread(
        lambda: file_operations.perform_copy_or_move_with_result([src], tmp_path, move=False)
    )
    assert blocking.entered.wait(WAIT_SECONDS)
    try:
        delete = file_operations.delete_paths_with_result([copying])
        delete_parent_child = file_operations.delete_paths_with_result([copying / "1.gif"])
        rename = file_operations.rename_path(copying, "renamed")
        copy_it = file_operations.perform_copy_or_move_with_result(
            [copying], tmp_path / "elsewhere", move=False
        )
        move_into = file_operations.perform_copy_or_move_with_result([other], copying, move=True)
        move_source = file_operations.perform_copy_or_move_with_result(
            [src], tmp_path / "elsewhere", move=True
        )
    finally:
        blocking.release.set()
        thread.join(WAIT_SECONDS)

    assert results[0].errors == []
    for result in (delete, delete_parent_child, copy_it, move_into, move_source):
        assert len(result.errors) == 1
        assert "still using" in result.errors[0]
    assert rename[0] is None and rename[1] is not None and "still using" in rename[1]
    trash.assert_not_called()
    real_move.assert_not_called()
    assert copying.exists()
    # 終わったら解除され、通常どおり操作できる。
    assert file_operations.busy_operation_path(copying) is None
    assert file_operations.delete_paths_with_result([copying]).errors == []


def test_reading_the_same_source_twice_is_allowed(mocker: MockerFixture, tmp_path: Path) -> None:
    """コピー元は読むだけなので、コピー中でも別の場所へのコピーは止めない。"""
    src = _album(tmp_path)
    blocking = _BlockingCall(lambda _src, dst: Path(dst).mkdir(parents=True))
    mocker.patch.object(file_operations.shutil, "copytree", side_effect=blocking)

    thread, results = _run_in_thread(
        lambda: file_operations.perform_copy_or_move_with_result(
            [src], tmp_path / "one", move=False
        )
    )
    assert blocking.entered.wait(WAIT_SECONDS)
    blocking.release.set()
    second = file_operations.perform_copy_or_move_with_result([src], tmp_path / "two", move=False)
    thread.join(WAIT_SECONDS)

    assert results[0].errors == []
    assert second.errors == []


def test_only_busy_sources_are_skipped_in_a_multi_source_operation(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    """複数の対象のうち使用中のものだけを断り、残りは通常どおり処理する。"""
    busy = _album(tmp_path, "busy")
    free = _album(tmp_path, "free")
    dest = tmp_path / "dest"
    trash = mocker.patch.object(file_operations, "send2trash")

    with RESERVATIONS.reserve(writes=[busy]):
        copied = file_operations.perform_copy_or_move_with_result([busy, free], dest, move=False)
        deleted = file_operations.delete_paths_with_result([busy, free])

    assert copied.errors == [BUSY_PATH_MESSAGE.format(path=busy)]
    assert (dest / "free" / "1.gif").exists()
    assert not (dest / "busy").exists()
    assert deleted.errors == [BUSY_PATH_MESSAGE.format(path=busy)]
    trash.assert_called_once_with([str(free)])


def test_reservation_is_released_when_the_operation_raises(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    src = _album(tmp_path)
    mocker.patch.object(file_operations.shutil, "copytree", side_effect=OSError("disk full"))
    failed = file_operations.perform_copy_or_move_with_result([src], tmp_path, move=False)
    assert len(failed.errors) == 1

    mocker.patch.object(file_operations.shutil, "copytree", side_effect=RuntimeError("bug"))
    with pytest.raises(RuntimeError):
        file_operations.perform_copy_or_move_with_result([src], tmp_path, move=False)

    assert file_operations.busy_operation_path(tmp_path / "album - Copy 1") is None


def test_cancellation_between_sources_releases_reservations(
    mocker: MockerFixture, tmp_path: Path
) -> None:
    first = _album(tmp_path, "first")
    second = _album(tmp_path, "second")
    copied: list[str] = []
    mocker.patch.object(
        file_operations.shutil,
        "copytree",
        side_effect=lambda src, _dst: copied.append(Path(src).name),
    )

    result = file_operations.perform_copy_or_move_with_result(
        [first, second], tmp_path / "dest", move=False, is_cancelled=lambda: bool(copied)
    )

    assert result.cancelled is True
    assert copied == ["first"]
