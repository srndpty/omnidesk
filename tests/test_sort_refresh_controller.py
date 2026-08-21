"""ソート／refreshコントローラの状態遷移テスト。"""

from pathlib import Path

from PyQt6.QtCore import QObject

from omnidesk.ui.file_browser.sort_refresh_controller import SortRefreshController


def test_sort_refresh_controller_stops_when_user_selection_changed(
    mocker,
    qtbot,
    tmp_path: Path,
) -> None:
    restore_target = tmp_path / "restore.txt"
    current = tmp_path / "current.txt"
    restore_target.write_text("restore", encoding="utf-8")
    current.write_text("current", encoding="utf-8")
    parent = QObject()
    controller = SortRefreshController(
        parent,
        model=mocker.Mock(),
        header=mocker.Mock(),
        tree_view=mocker.Mock(),
        tile_view=mocker.Mock(),
        selected_path=lambda: current,
        select_path=mocker.Mock(return_value=True),
    )
    controller.begin_refresh_sort(restore_target)

    assert not controller.can_restore_refresh_selection(restore_target)
    assert not controller.active
    assert controller.retries == 0
    assert controller.selection_path is None


def test_sort_refresh_controller_reselects_name_column_for_same_mode(mocker, qtbot) -> None:
    model = mocker.Mock()
    model.sort_mode.return_value = "name"
    header = mocker.Mock()
    header.sortIndicatorSection.return_value = 2
    tree_view = mocker.Mock()
    parent = QObject()
    controller = SortRefreshController(
        parent,
        model=model,
        header=header,
        tree_view=tree_view,
        tile_view=mocker.Mock(),
        selected_path=lambda: None,
        select_path=mocker.Mock(return_value=True),
    )

    assert controller.set_sort_mode("name")
    tree_view.sortByColumn.assert_called_once()
    model.set_sort_mode.assert_called_once_with("name")


def test_sort_refresh_controller_does_not_resort_on_selection_retry(
    mocker,
    qtbot,
    tmp_path: Path,
) -> None:
    """選択復元のリトライで並べ替えを繰り返さないこと。

    以前は 80ms 間隔のリトライごとに ``sort_current_directory`` を呼んでおり、
    1回の refresh で最大10回の並べ替えとタイルの再レイアウトが走っていた。
    7,500件のフォルダでは ``lessThan`` が1回あたり約9万回呼ばれるため、
    これだけで数秒のGUI停止になる。
    """
    restore_target = tmp_path / "restore.txt"
    restore_target.write_text("restore", encoding="utf-8")
    model = mocker.Mock()
    header = mocker.Mock()
    header.sortIndicatorSection.return_value = 0
    parent = QObject()
    controller = SortRefreshController(
        parent,
        model=model,
        header=header,
        tree_view=mocker.Mock(),
        tile_view=mocker.Mock(),
        selected_path=lambda: None,
        # 行がまだ現れておらず、復元に失敗し続ける状況を再現する。
        select_path=mocker.Mock(return_value=False),
    )
    controller.begin_refresh_sort(restore_target)

    for _ in range(3):
        controller.apply_refresh_sort()

    assert model.sort.call_count == 0
    # リトライ自体は消費される（行が現れるのを待ち続ける）。
    assert controller.retries == 7


def test_sort_refresh_controller_sorts_once_on_explicit_request(mocker, qtbot) -> None:
    """並べ替えの実行そのものは ``sort_current_directory`` 側に残っていること。"""
    model = mocker.Mock()
    header = mocker.Mock()
    header.sortIndicatorSection.return_value = 0
    parent = QObject()
    controller = SortRefreshController(
        parent,
        model=model,
        header=header,
        tree_view=mocker.Mock(),
        tile_view=mocker.Mock(),
        selected_path=lambda: None,
        select_path=mocker.Mock(return_value=False),
    )

    controller.sort_current_directory(reason="refresh-in-place")

    assert model.sort.call_count == 1
