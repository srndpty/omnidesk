"""走査ベースのフラットなディレクトリモデルのテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QModelIndex, Qt

from omnidesk.ui.directory_model import (
    COLUMN_COUNT,
    COLUMN_MODIFIED,
    COLUMN_NAME,
    COLUMN_SIZE,
    COLUMN_TYPE,
    DirectoryModel,
    contiguous_descending_ranges,
    normalise_entry_key,
)

pytestmark = pytest.mark.usefixtures("qapp")


def _make_tree(directory: Path) -> None:
    (directory / "sub").mkdir()
    (directory / "a.png").write_bytes(b"12345")
    (directory / "b.txt").write_text("hello", encoding="utf-8")


def _names(model: DirectoryModel) -> list[str]:
    return sorted(model.index(row, COLUMN_NAME).data() for row in range(model.rowCount()))


def _load(qtbot, model: DirectoryModel, directory: Path) -> None:
    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.setRootPath(str(directory))


def test_scan_lists_directory_entries(qtbot, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    model = DirectoryModel()

    _load(qtbot, model, tmp_path)

    assert _names(model) == ["a.png", "b.txt", "sub"]
    assert model.columnCount() == COLUMN_COUNT
    assert model.rootPath() == str(tmp_path)


def test_root_index_is_invalid_because_the_model_is_flat(qtbot, tmp_path: Path) -> None:
    """ツリーではないので、ビューへ渡すルートインデックスは常に不正値。"""
    _make_tree(tmp_path)
    model = DirectoryModel()

    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        root_index = model.setRootPath(str(tmp_path))

    assert not root_index.isValid()
    assert model.rowCount(QModelIndex()) == 3
    # 行の下に子は無い。
    assert model.rowCount(model.index(0, COLUMN_NAME)) == 0


def test_entry_carries_scan_time_metadata(qtbot, tmp_path: Path) -> None:
    """名前・種別・サイズ・更新日時が走査1回で揃うこと。"""
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)

    entry = model.entry(model.index_for_path(tmp_path / "a.png"))

    assert entry is not None
    assert entry.name == "a.png"
    assert entry.suffix == "png"
    assert entry.is_dir is False
    assert entry.size == 5
    assert entry.mtime_ms > 0

    folder = model.entry(model.index_for_path(tmp_path / "sub"))
    assert folder is not None and folder.is_dir


def test_display_columns(qtbot, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)

    row = model.index_for_path(tmp_path / "a.png").row()

    assert model.index(row, COLUMN_NAME).data() == "a.png"
    assert model.index(row, COLUMN_SIZE).data()
    assert model.index(row, COLUMN_TYPE).data() == "PNG File"
    assert model.index(row, COLUMN_MODIFIED).data()

    folder_row = model.index_for_path(tmp_path / "sub").row()
    assert model.index(folder_row, COLUMN_TYPE).data() == "File Folder"
    # フォルダはサイズを出さない。
    assert model.index(folder_row, COLUMN_SIZE).data() == ""


def test_header_labels_match_the_column_layout(qtbot, tmp_path: Path) -> None:
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)

    labels = [
        model.headerData(section, Qt.Orientation.Horizontal) for section in range(COLUMN_COUNT)
    ]

    assert labels == ["Name", "Size", "Type", "Date Modified"]


def test_sibling_columns_keep_the_row_identity(qtbot, tmp_path: Path) -> None:
    """``siblingAtColumn()`` で entry_id が落ちないこと。

    落ちると、上に乗るプロキシが行の同一性を見失い、並べ替えキャッシュが壊れる。
    """
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)

    name_index = model.index(0, COLUMN_NAME)
    for column in range(COLUMN_COUNT):
        assert name_index.siblingAtColumn(column).internalId() == name_index.internalId()


def test_qt_compatible_accessors(qtbot, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)

    index = model.index_for_path(tmp_path / "b.txt")

    assert Path(model.filePath(index)) == tmp_path / "b.txt"
    assert model.fileName(index) == "b.txt"
    assert model.isDir(index) is False
    assert model.fileInfo(index).fileName() == "b.txt"
    assert model.isDir(model.index_for_path(tmp_path / "sub")) is True

    missing = model.index_for_path(tmp_path / "nope.txt")
    assert not missing.isValid()
    assert model.filePath(missing) == ""


def test_refresh_reports_removals_and_additions_without_resetting(qtbot, tmp_path: Path) -> None:
    """更新は差分で通知すること（全入れ替えにしない）。

    ``modelReset`` にすると、ビューは選択もスクロール位置も作り直し、上に乗る
    プロキシも並べ替えキャッシュを全部捨てる。7,500件のフォルダではそれが
    体感できる停止になる。
    """
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)

    resets: list[object] = []
    removed: list[tuple[int, int]] = []
    inserted: list[tuple[int, int]] = []
    model.modelReset.connect(lambda: resets.append(True))
    model.rowsRemoved.connect(lambda parent, first, last: removed.append((first, last)))
    model.rowsInserted.connect(lambda parent, first, last: inserted.append((first, last)))

    (tmp_path / "b.txt").unlink()
    (tmp_path / "c.md").write_text("new", encoding="utf-8")
    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.refresh()

    assert resets == []
    assert len(removed) == 1
    assert len(inserted) == 1
    assert _names(model) == ["a.png", "c.md", "sub"]


def test_surviving_rows_keep_their_entry_id_across_refreshes(qtbot, tmp_path: Path) -> None:
    """生き残った行のIDが変わらないこと。

    変わると、上のプロキシは無関係な行の並べ替えキャッシュまで捨てることになる。
    """
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)
    before = model.entry(model.index_for_path(tmp_path / "a.png"))
    assert before is not None
    before_id = before.entry_id

    (tmp_path / "b.txt").unlink()
    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.refresh()

    after = model.entry(model.index_for_path(tmp_path / "a.png"))
    assert after is not None
    assert after.entry_id == before_id


def test_entry_ids_are_never_reused(qtbot, tmp_path: Path) -> None:
    """消えた行のIDを別のエントリへ割り当てないこと。

    ``QFileSystemModel`` の ``internalId()`` は内部ノードのアドレスで、
    ノードが消えると別のエントリへ再利用され得た。
    """
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)
    stale = model.entry(model.index_for_path(tmp_path / "b.txt"))
    assert stale is not None
    stale_id = stale.entry_id

    (tmp_path / "b.txt").unlink()
    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.refresh()
    (tmp_path / "d.txt").write_text("new", encoding="utf-8")
    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.refresh()

    fresh = model.entry(model.index_for_path(tmp_path / "d.txt"))
    assert fresh is not None
    assert fresh.entry_id != stale_id


def test_changed_content_emits_data_changed_for_that_row_only(qtbot, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)
    changes: list[tuple[int, int]] = []
    model.dataChanged.connect(
        lambda top_left, bottom_right, roles=None: changes.append(
            (top_left.row(), bottom_right.row())
        )
    )

    (tmp_path / "a.png").write_bytes(b"much longer content")
    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.refresh()

    target_row = model.index_for_path(tmp_path / "a.png").row()
    assert changes == [(target_row, target_row)]


def test_scan_failure_empties_the_model_and_reports(qtbot, tmp_path: Path) -> None:
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)
    failures: list[tuple[str, str]] = []
    model.scanFailed.connect(lambda path, error: failures.append((path, error)))

    missing = tmp_path / "does-not-exist"
    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.setRootPath(str(missing))

    assert model.rowCount() == 0
    assert len(failures) == 1
    assert failures[0][0] == str(missing)


def test_watcher_rescans_after_an_external_change(qtbot, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)

    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        (tmp_path / "external.txt").write_text("x", encoding="utf-8")

    assert "external.txt" in _names(model)


def test_stop_watching_leaves_the_directory_unwatched(qtbot, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)

    model.stop_watching()
    assert model._watcher.directories() == []

    model.resume_watching()
    assert model._watcher.directories() == [str(tmp_path)]


def test_stale_scan_results_are_ignored(qtbot, tmp_path: Path) -> None:
    """別のディレクトリへ移ったあとに届いた結果を反映しないこと。"""
    other = tmp_path / "other"
    other.mkdir()
    (other / "only.txt").write_text("x", encoding="utf-8")
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, other)

    model._handle_scan_result(str(tmp_path), model._generation, [], None)

    assert _names(model) == ["only.txt"]


def test_contiguous_descending_ranges_groups_and_reverses() -> None:
    assert contiguous_descending_ranges([]) == []
    assert contiguous_descending_ranges([3]) == [(3, 3)]
    # 後ろから消せるよう、降順にまとめる。
    assert contiguous_descending_ranges([0, 1, 2, 5, 6, 9]) == [(9, 9), (5, 6), (0, 2)]
    assert contiguous_descending_ranges([4, 2, 3]) == [(2, 4)]


def test_normalise_entry_key_avoids_filesystem_access(tmp_path: Path) -> None:
    missing = tmp_path / "missing" / "deep.txt"

    assert normalise_entry_key(missing) == normalise_entry_key(
        tmp_path / "missing" / "." / "deep.txt"
    )
    assert normalise_entry_key(missing) != normalise_entry_key(tmp_path / "missing")


def test_row_index_is_consistent_when_removal_is_announced(qtbot, tmp_path: Path) -> None:
    """``rowsRemoved`` を出す時点で ``index_for_path`` が新しい構成を返すこと。

    受け手はその場でパスを引き直す。古い索引のままだと、消えたはずのパスが
    「まだある」と見えてしまう。
    """
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)
    target = tmp_path / "b.txt"
    seen: list[bool] = []
    model.rowsRemoved.connect(
        lambda parent, first, last: seen.append(model.index_for_path(target).isValid())
    )

    target.unlink()
    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.refresh()

    assert seen == [False]


def test_row_index_is_consistent_when_insertion_is_announced(qtbot, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    model = DirectoryModel()
    _load(qtbot, model, tmp_path)
    target = tmp_path / "fresh.txt"
    seen: list[bool] = []
    model.rowsInserted.connect(
        lambda parent, first, last: seen.append(model.index_for_path(target).isValid())
    )

    target.write_text("x", encoding="utf-8")
    with qtbot.waitSignal(model.directoryLoaded, timeout=5000):
        model.refresh()

    assert seen == [True]
