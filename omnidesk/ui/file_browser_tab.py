"""File browser widget that powers each tab."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import (
    QDir,
    QItemSelectionModel,
    QModelIndex,
    QSize,
    QUrl,
    Qt,
    pyqtSignal,
    QTimer,
)
from PyQt6.QtGui import QDesktopServices, QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListView,
    QMessageBox,
    QStackedWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .media_file_system_model import MediaFileSystemModel


class FileBrowserTab(QWidget):
    """File browser view based on QFileSystemModel."""

    DEFAULT_NAME_COLUMN_WIDTH = 420
    MEDIA_RATIO_THRESHOLD = 0.6
    MEDIA_MIN_COUNT = 4
    MEDIA_SCAN_LIMIT = 60

    directoryChanged = pyqtSignal(Path)
    requestOpenInNewTab = pyqtSignal(Path)
    nameColumnWidthChanged = pyqtSignal(int)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        name_column_width: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = MediaFileSystemModel(self)
        self._model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        # self._model.thumbnailUpdated.connect(self._handle_thumbnail_updated)
        self._model.setResolveSymlinks(True)
        self._model.setReadOnly(True)

        self._tree_view = QTreeView(self)
        self._tree_view.setModel(self._model)
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree_view.doubleClicked.connect(self._handle_index_activated)
        self._tree_view.activated.connect(self._handle_index_activated)
        self._tree_view.setSortingEnabled(True)
        self._tree_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._tree_view.setRootIsDecorated(False)
        self._tree_view.setUniformRowHeights(True)
        self._tree_view.setIconSize(QSize(32, 32))

        self._tile_view = QListView(self)
        self._tile_view.setModel(self._model)
        self._tile_view.setViewMode(QListView.ViewMode.IconMode)
        self._tile_view.setFlow(QListView.Flow.LeftToRight)
        self._tile_view.setWrapping(True)
        self._tile_view.setResizeMode(QListView.ResizeMode.Adjust)
        self._tile_view.setMovement(QListView.Movement.Static)
        self._tile_view.setSpacing(16)
        self._tile_view.setUniformItemSizes(False)
        self._tile_view.setWordWrap(True)
        self._tile_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tile_view.doubleClicked.connect(self._handle_index_activated)
        self._tile_view.activated.connect(self._handle_index_activated)
        self._tile_view.setIconSize(QSize(128, 128))
        self._tile_view.setLayoutMode(QListView.LayoutMode.SinglePass)

        self._view_stack = QStackedWidget(self)
        self._view_stack.addWidget(self._tree_view)
        self._view_stack.addWidget(self._tile_view)

        header = self._tree_view.header()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setMinimumSectionSize(80)
        header.sectionResized.connect(self._handle_section_resized)
        self._header = header

        self._path_edit = QLineEdit(self)
        self._path_edit.setClearButtonEnabled(True)
        self._path_edit.returnPressed.connect(self._handle_path_entered)

        self._up_button = QToolButton(self)
        self._up_button.setText("Up")
        self._up_button.setToolTip("Go to parent directory (Backspace)")
        self._up_button.clicked.connect(self.go_up)

        self._refresh_button = QToolButton(self)
        self._refresh_button.setText("Reload")
        self._refresh_button.setToolTip("Refresh (F5)")
        self._refresh_button.clicked.connect(self.refresh)

        path_bar_layout = QHBoxLayout()
        path_bar_layout.setContentsMargins(0, 0, 0, 0)
        path_bar_layout.setSpacing(6)
        path_bar_layout.addWidget(self._path_edit, stretch=1)
        path_bar_layout.addWidget(self._up_button)
        path_bar_layout.addWidget(self._refresh_button)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)
        root_layout.addLayout(path_bar_layout)
        root_layout.addWidget(self._view_stack, stretch=1)

        self._current_path = Path.home()
        self._name_column_width = (
            name_column_width
            if name_column_width and name_column_width > 0
            else self.DEFAULT_NAME_COLUMN_WIDTH
        )
        self._media_icon_mode = False
        self._bound_selection_model: QItemSelectionModel | None = None

        self._configure_header_sections()
        self._apply_name_column_width()
        self._apply_media_mode()

        # ★★★ 追加: スクロールイベントを遅延処理するためのタイマー ★★★
        self._thumbnail_request_timer = QTimer(self)
        self._thumbnail_request_timer.setInterval(200)  # 200ミリ秒待ってから実行
        self._thumbnail_request_timer.setSingleShot(True)
        self._thumbnail_request_timer.timeout.connect(self._request_visible_thumbnails)

        # ★★★ 追加: 各ビューのスクロールバーにハンドラを接続 ★★★
        self._tree_view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._tile_view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._tree_view.horizontalScrollBar().valueChanged.connect(self._on_scroll)
        self._tile_view.horizontalScrollBar().valueChanged.connect(self._on_scroll)

        self._model.directoryLoaded.connect(self._on_directory_loaded)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def navigate_to(self, path: Path) -> None:
        """Display the given directory as the current root."""
        if not path.exists():
            QMessageBox.warning(self, "Cannot navigate", f"{path} does not exist.")
            return
        target = path if path.is_dir() else path.parent
        self._current_path = target
        self._path_edit.setText(str(target))
        root_index = self._model.setRootPath(str(target))
        self._tree_view.setRootIndex(root_index)
        self._tile_view.setRootIndex(root_index)
        self._update_media_mode(target)
        self._configure_header_sections()
        self._apply_name_column_width()
        self._connect_selection_signals()
        self.directoryChanged.emit(target)
        self._select_first_row()
        # ★★★ 追加: ディレクトリ移動後、最初のサムネイルリクエストをタイマー経由で行う ★★★
        self._thumbnail_request_timer.start()
    # ★★★ 追加: スクロール時にタイマーを開始するスロット ★★★
    def _on_scroll(self) -> None:
        self._thumbnail_request_timer.start()

    # ★★★ 追加: 表示されているアイテムのサムネイルをリクエストするメソッド ★★★
    def _request_visible_thumbnails(self) -> None:
        view = self._active_view()
        if not view:
            return

        visible_indexes = []
        # QTreeView と QListView で表示領域のインデックスを取得する方法
        if isinstance(view, QTreeView):
            top_index = view.indexAt(view.rect().topLeft())
            bottom_index = view.indexAt(view.rect().bottomLeft())
            if top_index.isValid():
                row = top_index.row()
                while row <= bottom_index.row() and row != -1:
                    index = top_index.siblingAtRow(row)
                    if index.isValid():
                        # 全てのカラムのインデックスを追加（将来のため）
                        for col in range(self._model.columnCount()):
                           visible_indexes.append(index.siblingAtColumn(col))
                    row += 1
        elif isinstance(view, QListView):
            # QListViewはよりシンプル
            for i in range(view.model().rowCount(view.rootIndex())):
                index = view.model().index(i, 0, view.rootIndex())
                if view.visualRect(index).intersects(view.viewport().rect()):
                    visible_indexes.append(index)

        if visible_indexes:
            self._model.prioritize_thumbnail_requests(visible_indexes)
                    
    def current_path(self) -> Path:
        return self._current_path

    def refresh(self) -> None:
        """Refresh the current directory view."""
        self.navigate_to(self._current_path)

    def go_up(self) -> None:
        """Navigate to the parent directory."""
        parent = self._current_path.parent
        if parent != self._current_path:
            self.navigate_to(parent)

    def focus_view(self) -> None:
        self._active_view().setFocus(Qt.FocusReason.OtherFocusReason)

    def set_name_column_width(self, width: int | None) -> None:
        """Apply a new preferred width to the name column."""
        if not width or width <= 0:
            return
        if width == self._name_column_width:
            return
        self._name_column_width = width
        self._apply_name_column_width()

    def name_column_width(self) -> int:
        return self._name_column_width

    # ------------------------------------------------------------------
    # internal slots
    # ------------------------------------------------------------------
    def _on_directory_loaded(self, _: str) -> None:
        self._select_first_row()
        self._update_media_mode(self._current_path)
        self._configure_header_sections()
        self._apply_name_column_width()

    def _handle_path_entered(self) -> None:
        entered = self._path_edit.text().strip()
        if not entered:
            return
        target = Path(entered)
        if target.is_file():
            self._open_file(target)
            return
        self.navigate_to(target)

    def _handle_index_activated(self, index: QModelIndex) -> None:
        file_info = self._model.fileInfo(index)
        target = Path(file_info.absoluteFilePath())
        if file_info.isDir():
            self.navigate_to(target)
        else:
            self._open_file(target)

    def _handle_section_resized(self, logical_index: int, _: int, new_size: int) -> None:
        if self._media_icon_mode:
            return
        if logical_index != 0:
            return
        if new_size <= 0 or new_size == self._name_column_width:
            return
        self._name_column_width = new_size
        self.nameColumnWidthChanged.emit(new_size)

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            selected = self._selected_index_path()
            if selected and selected.is_dir():
                self.requestOpenInNewTab.emit(selected)
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _active_view(self) -> QAbstractItemView:
        if self._media_icon_mode:
            return self._tile_view
        return self._tree_view

    def _apply_name_column_width(self) -> None:
        if self._media_icon_mode:
            return
        if self._name_column_width > 0:
            self._header.resizeSection(0, self._name_column_width)

    def _configure_header_sections(self) -> None:
        if self._media_icon_mode:
            return
        count = self._header.count()
        if count == 0:
            return
        self._header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for section in range(1, count):
            self._header.setSectionResizeMode(section, QHeaderView.ResizeMode.ResizeToContents)

    def _selected_index_path(self) -> Path | None:
        selection_model = self._active_view().selectionModel()
        if not selection_model:
            return None
        index = selection_model.currentIndex()
        if not index.isValid():
            return None
        file_info = self._model.fileInfo(index)
        return Path(file_info.absoluteFilePath())

    def _select_first_row(self) -> None:
        view = self._active_view()
        model = view.model()
        if not model:
            return
        selection_model = view.selectionModel()
        root_index = view.rootIndex()
        if selection_model and root_index.isValid():
            first_index = model.index(0, 0, root_index)
            if first_index.isValid():
                selection_model.setCurrentIndex(
                    first_index,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )

    def _handle_thumbnail_updated(self, index: QModelIndex) -> None:
        """Force repaint when a thumbnail icon is ready."""
        for view in (self._tile_view, self._tree_view):
            rect = view.visualRect(index)
            if rect.isValid():
                view.viewport().update(rect)

    def _connect_selection_signals(self) -> None:
        view = self._active_view()
        selection_model = view.selectionModel()
        if not selection_model:
            return
        if self._bound_selection_model is selection_model:
            return
        if self._bound_selection_model is not None:
            try:
                self._bound_selection_model.currentChanged.disconnect(self._handle_current_changed)
            except TypeError:
                pass
        self._bound_selection_model = selection_model
        selection_model.currentChanged.connect(self._handle_current_changed)

    def _handle_current_changed(self, current: QModelIndex, _: QModelIndex) -> None:
        file_info = self._model.fileInfo(current)
        if file_info.isDir():
            self.directoryChanged.emit(Path(file_info.absoluteFilePath()))

    def _open_file(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------------
    def _update_media_mode(self, directory: Path) -> None:
        should_enable = self._is_media_heavy(directory)
        print(f"[FileBrowserTab] media mode check: {directory} -> {should_enable}", flush=True)
        if should_enable != self._media_icon_mode:
            self._media_icon_mode = should_enable
            print(f"[FileBrowserTab] media mode toggled to {self._media_icon_mode}", flush=True)
            self._apply_media_mode()
        elif self._media_icon_mode:
            self._apply_media_mode()

    def _apply_media_mode(self) -> None:
        if self._media_icon_mode:
            icon_edge = 160
            print(f"[FileBrowserTab] apply media mode with edge {icon_edge}", flush=True)
            self._model.set_thumbnail_edge(icon_edge)
            self._tile_view.setIconSize(QSize(icon_edge, icon_edge))
            self._tile_view.setGridSize(self._calculate_grid_size(icon_edge))
            self._view_stack.setCurrentWidget(self._tile_view)
        else:
            print("[FileBrowserTab] apply list mode", flush=True)
            self._model.set_thumbnail_edge(96)
            self._tree_view.setIconSize(QSize(32, 32))
            self._view_stack.setCurrentWidget(self._tree_view)
        self._connect_selection_signals()
        self._select_first_row()

    def _calculate_grid_size(self, edge: int) -> QSize:
        fm = self._tile_view.fontMetrics()
        text_height = fm.lineSpacing() * 2
        padding = 24
        width = edge + padding
        height = edge + padding + text_height
        print(f"[FileBrowserTab] grid size edge={edge} -> {width}x{height}", flush=True)
        return QSize(width, height)

    def _is_media_heavy(self, directory: Path) -> bool:
        try:
            iterator = directory.iterdir()
        except OSError:
            return False
        total_files = 0
        media_files = 0
        extensions = self._model.media_extensions
        for entry in iterator:
            if entry.is_file():
                total_files += 1
                if entry.suffix.lower() in extensions:
                    media_files += 1
            if total_files >= self.MEDIA_SCAN_LIMIT:
                break
        if media_files == 0:
            return False
        if total_files <= self.MEDIA_MIN_COUNT:
            print(f"[FileBrowserTab] media-heavy due to small count: files={total_files} media={media_files}", flush=True)
            return True
        if media_files < self.MEDIA_MIN_COUNT:
            return False
        ratio = media_files / total_files
        print(f"[FileBrowserTab] media ratio check: media={media_files} total={total_files} ratio={ratio:.2f}", flush=True)
        return ratio >= self.MEDIA_RATIO_THRESHOLD
