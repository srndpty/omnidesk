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
