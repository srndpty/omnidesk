"""表示モードコントローラの部品テスト。"""

from omnidesk.ui.file_browser.view_mode_controller import ViewModeController


def test_view_mode_controller_keeps_manual_list_mode(mocker, qtbot, tmp_path) -> None:
    model = mocker.Mock()
    tree_view = mocker.Mock()
    tile_view = mocker.Mock()
    view_stack = mocker.Mock()
    controller = ViewModeController(
        model=model,
        tree_view=tree_view,
        tile_view=tile_view,
        view_stack=view_stack,
        header=mocker.Mock(),
        toggle_button=mocker.Mock(),
        name_column_width=420,
        connect_selection_signals=mocker.Mock(),
        select_pending_or_first_row=mocker.Mock(),
        name_column_width_changed=mocker.Mock(),
        media_ratio_threshold=0.6,
        media_min_count=4,
        media_scan_limit=60,
    )
    controller.media_icon_mode = True
    controller.manual_media_mode = False

    controller.update_media_mode(tmp_path, select_default=False)

    assert not controller.media_icon_mode
    model.set_thumbnail_edge.assert_called_once_with(96)
    view_stack.setCurrentWidget.assert_called_once_with(tree_view)
