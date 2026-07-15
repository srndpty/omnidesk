"""一覧／タイル表示モードと列幅を管理するコントローラ。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QStackedWidget, QToolButton

from ..file_browser_media_mode import (
    calculate_grid_size,
    is_media_heavy_directory,
    media_mode_button_text,
)
from .sort_model import SortedFileSystemModel
from .views import _FileTileView, _FileTreeView


class ViewModeController:
    """表示モード、列幅、表示切替UIの状態を一か所に保持する。"""

    def __init__(
        self,
        *,
        model: SortedFileSystemModel,
        tree_view: _FileTreeView,
        tile_view: _FileTileView,
        view_stack: QStackedWidget,
        header: QHeaderView,
        toggle_button: QToolButton,
        name_column_width: int,
        connect_selection_signals: Callable[[], None],
        select_pending_or_first_row: Callable[[], None],
        name_column_width_changed: Callable[[int], None],
        media_ratio_threshold: float,
        media_min_count: int,
        media_scan_limit: int,
    ) -> None:
        self._model = model
        self._tree_view = tree_view
        self._tile_view = tile_view
        self._view_stack = view_stack
        self._header = header
        self._toggle_button = toggle_button
        self._name_column_width = name_column_width
        self._connect_selection_signals = connect_selection_signals
        self._select_pending_or_first_row = select_pending_or_first_row
        self._name_column_width_changed = name_column_width_changed
        self._media_ratio_threshold = media_ratio_threshold
        self._media_min_count = media_min_count
        self._media_scan_limit = media_scan_limit
        self._media_icon_mode = False
        self._manual_media_mode: bool | None = None

    def active_view(self) -> QAbstractItemView:
        return self._tile_view if self._media_icon_mode else self._tree_view

    def set_name_column_width(self, width: int | None) -> None:
        if not width or width <= 0 or width == self._name_column_width:
            return
        self._name_column_width = width
        self.apply_name_column_width()

    def apply_name_column_width(self) -> None:
        if not self._media_icon_mode and self._name_column_width > 0:
            self._header.resizeSection(0, self._name_column_width)

    def handle_section_resized(self, logical_index: int, new_size: int) -> None:
        if self._media_icon_mode or logical_index != 0:
            return
        if new_size <= 0 or new_size == self._name_column_width:
            return
        self._name_column_width = new_size
        self._name_column_width_changed(new_size)

    def configure_header_sections(self) -> None:
        if self._media_icon_mode:
            return
        count = self._header.count()
        if count == 0:
            return
        self._header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for section in range(1, count):
            self._header.setSectionResizeMode(section, QHeaderView.ResizeMode.ResizeToContents)

    def update_media_mode(self, directory: Path, *, select_default: bool = True) -> None:
        _ = directory
        should_enable = self._manual_media_mode if self._manual_media_mode is not None else True
        if should_enable != self._media_icon_mode:
            self._media_icon_mode = should_enable
            self.apply_media_mode(select_default=select_default)
        elif self._media_icon_mode:
            self.apply_media_mode(select_default=select_default)

    def apply_media_mode(self, *, select_default: bool = True) -> None:
        if self._media_icon_mode:
            icon_edge = 160
            self._model.set_thumbnail_edge(icon_edge)
            self._tile_view.setIconSize(QSize(icon_edge, icon_edge))
            self._tile_view.setGridSize(self.calculate_grid_size(icon_edge))
            self._view_stack.setCurrentWidget(self._tile_view)
        else:
            self._model.set_thumbnail_edge(96)
            self._tree_view.setIconSize(QSize(32, 32))
            self._view_stack.setCurrentWidget(self._tree_view)
        self.update_view_toggle_button()
        self._connect_selection_signals()
        if select_default:
            self._select_pending_or_first_row()

    def handle_view_toggle_clicked(self) -> None:
        target = not self._media_icon_mode
        self._manual_media_mode = target
        self._media_icon_mode = target
        self.apply_media_mode()

    def update_view_toggle_button(self) -> None:
        text, tooltip = media_mode_button_text(self._media_icon_mode)
        self._toggle_button.setText(text)
        self._toggle_button.setToolTip(tooltip)

    def calculate_grid_size(self, edge: int) -> QSize:
        return calculate_grid_size(edge, self._tile_view.fontMetrics().lineSpacing())

    def is_media_heavy(self, directory: Path) -> bool:
        return is_media_heavy_directory(
            directory,
            self._model.media_extensions,
            ratio_threshold=self._media_ratio_threshold,
            min_count=self._media_min_count,
            scan_limit=self._media_scan_limit,
        )

    @property
    def media_icon_mode(self) -> bool:
        return self._media_icon_mode

    @media_icon_mode.setter
    def media_icon_mode(self, value: bool) -> None:
        self._media_icon_mode = value

    @property
    def manual_media_mode(self) -> bool | None:
        return self._manual_media_mode

    @manual_media_mode.setter
    def manual_media_mode(self, value: bool | None) -> None:
        self._manual_media_mode = value

    @property
    def name_column_width(self) -> int:
        return self._name_column_width

    @name_column_width.setter
    def name_column_width(self, value: int) -> None:
        self._name_column_width = value
