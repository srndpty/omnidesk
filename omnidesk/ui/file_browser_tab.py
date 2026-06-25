"""File browser widget that powers each tab."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QDir, QItemSelectionModel, QSize, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .file_browser.actions import FileBrowserActionsMixin
from .file_browser.clipboard import FileBrowserClipboardMixin, _ClipboardPayload
from .file_browser.command_runner import FileBrowserCommandRunnerMixin
from .file_browser.navigation_controller import FileBrowserNavigationMixin
from .file_browser.operations_controller import FileBrowserOperationsMixin
from .file_browser.sort_model import SortedFileSystemModel
from .file_browser.status_controller import FileBrowserStatusMixin, _DirectoryCountJob
from .file_browser.thumbnail_controller import FileBrowserThumbnailMixin
from .file_browser.toolbar import _configure_arrow_button
from .file_browser.views import (
    _FileTileView,
    _FileTreeView,
    navigation_cursor_action,
    navigation_event_without_control,
)
from .file_browser_background import FileBrowserThumbnailScheduler
from .file_browser_navigation import DirectoryFingerprint
from .file_operation_jobs import FileOperationJob
from .media_file_system_model import MediaFileSystemModel

logger = logging.getLogger(__name__)

__all__ = [
    "FileBrowserTab",
    "navigation_cursor_action",
    "navigation_event_without_control",
]


class FileBrowserTab(
    FileBrowserActionsMixin,
    FileBrowserClipboardMixin,
    FileBrowserCommandRunnerMixin,
    FileBrowserNavigationMixin,
    FileBrowserOperationsMixin,
    FileBrowserStatusMixin,
    FileBrowserThumbnailMixin,
    QWidget,
):
    """File browser view based on QFileSystemModel."""

    DEFAULT_NAME_COLUMN_WIDTH = 420
    MEDIA_RATIO_THRESHOLD = 0.6
    MEDIA_MIN_COUNT = 4
    MEDIA_SCAN_LIMIT = 60

    directoryChanged = pyqtSignal(Path)
    requestOpenInNewTab = pyqtSignal(Path)
    nameColumnWidthChanged = pyqtSignal(int)
    statusChanged = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        name_column_width: int | None = None,
    ) -> None:
        QWidget.__init__(self, parent)
        self._media_icon_mode = False
        self._current_path = Path.home()
        self._navigation_history: list[Path] = []
        self._forward_history: list[Path] = []
        self._has_loaded_root = False
        self._current_directory_fingerprint: DirectoryFingerprint | None = None
        self._current_directory_has_local_changes = False
        self._is_active = False
        self._pending_selection_path: Path | None = None
        self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        self._settled_scroll_path: Path | None = None
        self._settled_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        self._settled_scroll_retries = 0
        self._refresh_sort_active = False
        self._refresh_sort_retries = 0
        self._refresh_selection_path: Path | None = None
        self._deferred_refresh_target: Path | None = None
        self._preserve_selection_on_refresh = True

        self._source_model = MediaFileSystemModel(self)
        self._source_model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        # self._source_model.thumbnailUpdated.connect(self._handle_thumbnail_updated)
        self._source_model.setResolveSymlinks(True)
        self._source_model.setReadOnly(True)

        # 名前順/拡張子順の並べ替えはプロキシ側で制御し、UI からは従来どおり
        # ``self._model`` を QFileSystemModel と同じ感覚で扱えるようにする。
        self._model = SortedFileSystemModel(self)
        self._model.setSourceModel(self._source_model)

        # モデルのレイアウトが変更されたら、サムネイル要求をトリガーする
        self._model.layoutChanged.connect(self._on_layout_changed)
        self._model.rowsInserted.connect(self._on_rows_inserted)

        self._tree_view = _FileTreeView(self)
        self._tree_view.setModel(self._model)
        self._tree_view.setAlternatingRowColors(True)
        self._tree_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tree_view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree_view.doubleClicked.connect(self._handle_index_activated)
        self._tree_view.activated.connect(self._handle_index_activated)
        self._tree_view.setRootIsDecorated(False)
        self._tree_view.setUniformRowHeights(True)
        self._tree_view.setIconSize(QSize(32, 32))

        header = self._tree_view.header()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setMinimumSectionSize(80)
        header.sectionResized.connect(self._handle_section_resized)
        # NOTE: _tree_view.sortByColumn()よりも先に来なければならない！ 順序変更注意！
        # header.sortIndicatorChanged.connect(self._on_sort_changed)
        self._header = header

        self._tree_view.setSortingEnabled(True)
        self._tree_view.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        self._tile_view = _FileTileView(self)
        self._tile_view.setModel(self._model)
        self._tile_view.doubleClicked.connect(self._handle_index_activated)
        self._tile_view.activated.connect(self._handle_index_activated)
        self._tile_view.setIconSize(QSize(128, 128))

        self._view_stack = QStackedWidget(self)
        self._view_stack.addWidget(self._tree_view)
        self._view_stack.addWidget(self._tile_view)

        self._manual_media_mode: bool | None = None
        self._clipboard: _ClipboardPayload | None = None
        self._clipboard_path_set: set[Path] = set()
        self._status_folder_count = 0
        self._status_file_count = 0
        self._status_count_generation = 0
        self._status_count_jobs: dict[int, _DirectoryCountJob] = {}
        self._status_count_refresh_on_activate = False
        self._status_count_pool = QThreadPool.globalInstance()
        self._file_operation_jobs: list[FileOperationJob] = []
        self._create_actions()
        self._toggle_view_button = QToolButton(self)
        self._toggle_view_button.setText("Tile View")
        self._toggle_view_button.setToolTip("Toggle between tile and list views")
        self._toggle_view_button.clicked.connect(self._handle_view_toggle_clicked)
        self._update_view_toggle_button()

        self._path_edit = QLineEdit(self)
        self._path_edit.setClearButtonEnabled(True)
        self._path_edit.returnPressed.connect(self._handle_path_entered)

        self._back_button = QToolButton(self)
        _configure_arrow_button(
            self._back_button,
            text="←",
            accessible_name="Back",
            tooltip="Go back (Alt+Left)",
        )
        self._back_button.clicked.connect(self.go_back)

        self._forward_button = QToolButton(self)
        _configure_arrow_button(
            self._forward_button,
            text="→",
            accessible_name="Forward",
            tooltip="Go forward (Alt+Right)",
        )
        self._forward_button.clicked.connect(self.go_forward)

        self._up_button = QToolButton(self)
        _configure_arrow_button(
            self._up_button,
            text="↑",
            accessible_name="Up",
            tooltip="Go to parent directory",
        )
        self._up_button.clicked.connect(self.go_up)

        self._refresh_button = QToolButton(self)
        self._refresh_button.setText("Reload")
        self._refresh_button.setToolTip("Refresh (F5)")
        self._refresh_button.clicked.connect(self.refresh)

        path_bar_layout = QHBoxLayout()
        path_bar_layout.setContentsMargins(0, 0, 0, 0)
        path_bar_layout.setSpacing(6)
        path_bar_layout.addWidget(self._back_button)
        path_bar_layout.addWidget(self._forward_button)
        path_bar_layout.addWidget(self._up_button)
        path_bar_layout.addWidget(self._path_edit, stretch=1)
        path_bar_layout.addWidget(self._toggle_view_button)
        path_bar_layout.addWidget(self._refresh_button)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)
        root_layout.addLayout(path_bar_layout)
        root_layout.addWidget(self._view_stack, stretch=1)

        self._name_column_width = (
            name_column_width
            if name_column_width and name_column_width > 0
            else self.DEFAULT_NAME_COLUMN_WIDTH
        )
        self._bound_selection_model: QItemSelectionModel | None = None

        self._configure_header_sections()
        self._apply_name_column_width()
        self._apply_media_mode()

        self._is_scrolling_for_thumbnails = False

        self._thumbnail_request_timer = QTimer(self)
        self._thumbnail_request_timer.setInterval(30)
        self._thumbnail_request_timer.setSingleShot(True)
        self._thumbnail_request_timer.timeout.connect(
            lambda: self._request_visible_thumbnails(scrolling=True)
        )

        self._thumbnail_scroll_settle_timer = QTimer(self)
        self._thumbnail_scroll_settle_timer.setInterval(160)
        self._thumbnail_scroll_settle_timer.setSingleShot(True)
        self._thumbnail_scroll_settle_timer.timeout.connect(self._request_settled_thumbnails)

        self._thumbnail_idle_batch_timer = QTimer(self)
        self._thumbnail_idle_batch_timer.setInterval(220)
        self._thumbnail_idle_batch_timer.setSingleShot(True)
        self._thumbnail_idle_batch_timer.timeout.connect(
            lambda: self._request_visible_thumbnails(scrolling=False)
        )
        self._thumbnail_scheduler = FileBrowserThumbnailScheduler(
            request_timer=self._thumbnail_request_timer,
            scroll_settle_timer=self._thumbnail_scroll_settle_timer,
            idle_batch_timer=self._thumbnail_idle_batch_timer,
            is_active=lambda: self._is_active,
            set_scrolling=self._set_thumbnail_scrolling,
            request_visible=self._request_visible_thumbnail_batch,
        )

        self._selection_restore_timer = QTimer(self)
        self._selection_restore_timer.setSingleShot(True)
        self._selection_restore_timer.timeout.connect(self._select_pending_or_first_row)

        self._settled_scroll_timer = QTimer(self)
        self._settled_scroll_timer.setSingleShot(True)
        self._settled_scroll_timer.setInterval(80)
        self._settled_scroll_timer.timeout.connect(self._apply_settled_scroll)

        self._refresh_sort_timer = QTimer(self)
        self._refresh_sort_timer.setSingleShot(True)
        self._refresh_sort_timer.setInterval(80)
        self._refresh_sort_timer.timeout.connect(self._apply_refresh_sort)

        self._deferred_refresh_timer = QTimer(self)
        self._deferred_refresh_timer.setSingleShot(True)
        self._deferred_refresh_timer.setInterval(0)
        self._deferred_refresh_timer.timeout.connect(self._complete_deferred_refresh)

        self._tree_view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._tile_view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self._tree_view.horizontalScrollBar().valueChanged.connect(self._on_scroll)
        self._tile_view.horizontalScrollBar().valueChanged.connect(self._on_scroll)

        self._model.directoryLoaded.connect(self._on_directory_loaded)
