"""File browser widget that powers each tab."""

from __future__ import annotations

from pathlib import Path

import shutil
from functools import partial
from itertools import count
from pathlib import Path
import os

from PyQt6.QtCore import (
    QDir,
    QItemSelectionModel,
    QModelIndex,
    QSize,
    QUrl,
    Qt,
    pyqtSignal,
    QTimer,
    QMimeData,
)
from PyQt6.QtGui import (
    QCursor,
    QDesktopServices,
    QDrag,
    QKeyEvent,
    QKeySequence,
    QAction,
)
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QStackedWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


from .media_file_system_model import MediaFileSystemModel


class _BaseFileViewMixin:
    """Adds reusable drag-and-drop and context menu behaviours."""

    def _init_file_view(self, tab: "FileBrowserTab") -> None:
        self._tab = tab
        self._drag_start_pos = None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(partial(tab._show_context_menu, self))

    def selected_paths(self) -> list[Path]:
        selection_model = self.selectionModel()
        if not selection_model:
            return []
        rows = selection_model.selectedRows() or selection_model.selectedIndexes()
        if not rows:
            return []
        return self._tab._paths_from_indexes(rows)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_start_pos is not None:
            distance = (event.position() - self._drag_start_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                if self.selected_paths():
                    self.startDrag(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)
                    return
        super().mouseMoveEvent(event)

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # noqa: N802
        print(f"[_BaseFileViewMixin] startDrag: supported_actions={supported_actions}", flush=True)
        paths = self.selected_paths()
        print(f"[_BaseFileViewMixin] startDrag: selected paths={len(paths)}, [0]={paths[0]}", flush=True)
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        default_action = Qt.DropAction.MoveAction
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, default_action)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        print(f"[_BaseFileViewMixin] dragEnterEvent: mime={event.mimeData().formats()}", flush=True)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        print(f"[_BaseFileViewMixin] dragMoveEvent: mime={event.mimeData().formats()}", flush=True)
        if event.mimeData().hasUrls():
            print
            action = (
                Qt.DropAction.CopyAction
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier
                else Qt.DropAction.MoveAction
            )
            event.setDropAction(action)
            event.acceptProposedAction()
        else:
            print(f"[_BaseFileViewMixin] dragMoveEvent ignored: no URLs", flush=True)
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        print(f"[_BaseFileViewMixin] dropEvent: mime={event.mimeData().formats()}", flush=True)
        if not event.mimeData().hasUrls():
            event.ignore()
            print(f"[_BaseFileViewMixin] dropEvent ignored: no URLs", flush=True)
            return
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        if not paths:
            event.ignore()
            print(f"[_BaseFileViewMixin] dropEvent ignored: no local files", flush=True)
            return
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        target_dir = self._tab._current_path
        print(f"[_BaseFileViewMixin] dropEvent at {target_dir}, index valid={index.isValid()}", flush=True)
        if index.isValid():
            file_info = self._tab._model.fileInfo(index)
            print(f"[_BaseFileViewMixin] drop target info: dir={file_info.isDir()} path={file_info.absoluteFilePath()}", flush=True)
            if file_info.isDir():
                target_dir = Path(file_info.absoluteFilePath())
            else:
                target_dir = Path(file_info.absolutePath())
        move = (
            event.dropAction() == Qt.DropAction.MoveAction
            and not event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )
        print(f"[_BaseFileViewMixin] drop move={move} urls={len(paths)}", flush=True)
        self._tab._handle_external_drop(paths, target_dir, move)
        event.setDropAction(Qt.DropAction.MoveAction if move else Qt.DropAction.CopyAction)
        event.acceptProposedAction()

    def dropMimeData(self, data, action, row, column, parent_index: QModelIndex) -> bool:
        print(f"[_BaseFileViewMixin] dropMimeData: action={action} row={row} column={column} parent valid={parent_index.isValid()}", flush=True)
        if action != Qt.DropAction.MoveAction:
            print(f"[_BaseFileViewMixin] dropMimeData ignored: action not move", flush=True)
            return False

        # parent_index はドロップ先のディレクトリのインデックス
        if not parent_index.isValid():
            print(f"[_BaseFileViewMixin] dropMimeData ignored: invalid parent index", flush=True)
            return False

        dest_dir = self.filePath(parent_index)
        if not os.path.isdir(dest_dir):
            print(f"[_BaseFileViewMixin] dropMimeData ignored: destination not a directory: {dest_dir}", flush=True)
            return False

        # data.urls() にドラッグされたファイルのパスが入ってくる
        for url in data.urls():
            src_path = url.toLocalFile()
            basename = os.path.basename(src_path)
            dest_path = os.path.join(dest_dir, basename)

            # 防御的にチェック
            if src_path == dest_path:
                continue
            try:
                # 移動（リネーム）
                os.rename(src_path, dest_path)
            except Exception as e:
                print("Error moving file:", e)
                # 必要ならメッセージなど
                return False

        return True

class _FileTreeView(_BaseFileViewMixin, QTreeView):
    def __init__(self, tab: "FileBrowserTab") -> None:
        super().__init__(tab)
        self._init_file_view(tab)


class _FileTileView(_BaseFileViewMixin, QListView):
    def __init__(self, tab: "FileBrowserTab") -> None:
        super().__init__(tab)
        self._init_file_view(tab)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Free)
        # self.setMovement(QListView.Movement.Static)
        self.setSpacing(16)
        self.setUniformItemSizes(False)
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionRectVisible(False)



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
        self._media_icon_mode = False

        self._model = MediaFileSystemModel(self)
        self._model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        # self._model.thumbnailUpdated.connect(self._handle_thumbnail_updated)
        self._model.setResolveSymlinks(True)
        self._model.setReadOnly(True)

        self._tree_view = _FileTreeView(self)
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

        self._tile_view = _FileTileView(self)
        self._tile_view.setModel(self._model)
        self._tile_view.doubleClicked.connect(self._handle_index_activated)
        self._tile_view.activated.connect(self._handle_index_activated)
        self._tile_view.setIconSize(QSize(128, 128))
        self._tile_view.setLayoutMode(QListView.LayoutMode.SinglePass)

        self._view_stack = QStackedWidget(self)
        self._view_stack.addWidget(self._tree_view)
        self._view_stack.addWidget(self._tile_view)

        self._clipboard: dict[str, object] | None = None
        self._create_actions()

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

    def _create_actions(self) -> None:
        self._copy_action = QAction("Copy", self)
        self._copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self._copy_action.triggered.connect(self._copy_selected)

        self._cut_action = QAction("Cut", self)
        self._cut_action.setShortcut(QKeySequence.StandardKey.Cut)
        self._cut_action.triggered.connect(self._cut_selected)

        self._paste_action = QAction("Paste", self)
        self._paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self._paste_action.triggered.connect(self._paste_into_current)

        self._delete_action = QAction("Delete", self)
        self._delete_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        self._delete_action.triggered.connect(self._delete_selected)

        for action in (
            self._copy_action,
            self._cut_action,
            self._paste_action,
            self._delete_action,
        ):
            self.addAction(action)
        self._update_action_states()

    def _update_action_states(self) -> None:
        has_selection = bool(self._selected_paths())
        clipboard_ready = isinstance(self._clipboard, dict) and bool(self._clipboard.get("paths"))
        self._copy_action.setEnabled(has_selection)
        self._cut_action.setEnabled(has_selection)
        self._delete_action.setEnabled(has_selection)
        self._paste_action.setEnabled(clipboard_ready and self._current_path.exists())

    def _paths_from_indexes(self, indexes: list[QModelIndex]) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for index in indexes:
            if not index.isValid():
                continue
            source = index.siblingAtColumn(0)
            path = Path(self._model.filePath(source))
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def _selected_paths(self) -> list[Path]:
        view = self._active_view()
        return view.selected_paths()

    def _show_context_menu(self, view: QAbstractItemView, point) -> None:
        index = view.indexAt(point)
        selection_model = view.selectionModel()
        if index.isValid() and selection_model and not selection_model.isSelected(index):
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
        self._update_action_states()
        menu = QMenu(self)
        menu.addAction(self._copy_action)
        menu.addAction(self._cut_action)
        menu.addAction(self._paste_action)
        menu.addSeparator()
        menu.addAction(self._delete_action)
        menu.exec(view.viewport().mapToGlobal(point))

    def _copy_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        self._clipboard = {"paths": paths, "mode": "copy"}
        self._update_action_states()

    def _cut_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        self._clipboard = {"paths": paths, "mode": "move"}
        self._update_action_states()

    def _paste_into_current(self) -> None:
        if not self._clipboard:
            return
        paths = [Path(p) for p in self._clipboard.get("paths", [])]
        if not paths:
            return
        move = self._clipboard.get("mode") == "move"
        self._perform_copy_or_move(paths, self._current_path, move=move)
        if move:
            self._clipboard = None
        self.refresh()
        self._update_action_states()

    def _delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        if (
            QMessageBox.question(
                self,
                "Delete",
                f"Delete {len(paths)} item(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        errors: list[str] = []
        for path in paths:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except Exception as exc:  # pragma: no cover - filesystem dependent
                errors.append(f"{path}: {exc}")
        if errors:
            QMessageBox.warning(self, "Delete failed", "\n".join(errors))
        self.refresh()
        self._update_action_states()

    def _perform_copy_or_move(self, sources: list[Path], dest_dir: Path, *, move: bool) -> None:
        errors: list[str] = []
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in sources:
            if not src.exists():
                errors.append(f"Missing: {src}")
                continue
            try:
                if move and src.parent.resolve() == dest_dir.resolve():
                    continue
            except Exception:  # pragma: no cover - resolution failure on some systems
                pass
            try:
                target = self._resolve_destination(dest_dir, src.name, move)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            try:
                if move:
                    shutil.move(str(src), str(target))
                else:
                    if src.is_dir():
                        shutil.copytree(src, target)
                    else:
                        shutil.copy2(src, target)
            except Exception as exc:  # pragma: no cover - filesystem dependent
                errors.append(f"{src} -> {target}: {exc}")
        if errors:
            QMessageBox.warning(self, "Operation issues", "\n".join(errors))

    def _resolve_destination(self, dest_dir: Path, name: str, move: bool) -> Path:
        target = dest_dir / name
        if not target.exists():
            return target
        if move and target.exists():
            raise ValueError(f"Destination already has {name}")
        stem = target.stem
        suffix = target.suffix
        for n in count(1):
            candidate = dest_dir / f"{stem} - Copy {n}{suffix}"
            if not candidate.exists():
                return candidate
        raise ValueError("Unable to resolve destination")

    @staticmethod
    def _is_within(path: Path, potential_parent: Path) -> bool:
        try:
            path.relative_to(potential_parent)
            return True
        except ValueError:
            return False

    def _handle_external_drop(self, paths: list[Path], target_dir: Path, move: bool) -> None:
        if not target_dir.exists():
            QMessageBox.warning(self, "Drop failed", f"Destination {target_dir} does not exist.")
            return
        if move:
            for path in paths:
                try:
                    src_resolved = path.resolve()
                    dest_resolved = target_dir.resolve()
                except Exception:  # pragma: no cover - Windows UNC etc.
                    continue
                if src_resolved == dest_resolved or self._is_within(dest_resolved, src_resolved):
                    QMessageBox.warning(self, "Drop failed", "Cannot move a folder into itself.")
                    return
        self._perform_copy_or_move(paths, target_dir, move=move)
        self.refresh()


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
            try:
                self._bound_selection_model.selectionChanged.disconnect(self._handle_selection_changed)
            except TypeError:
                pass
        self._bound_selection_model = selection_model
        selection_model.currentChanged.connect(self._handle_current_changed)
        selection_model.selectionChanged.connect(self._handle_selection_changed)
        self._update_action_states()

    def _handle_current_changed(self, current: QModelIndex, _: QModelIndex) -> None:
        file_info = self._model.fileInfo(current)
        if file_info.isDir():
            self.directoryChanged.emit(Path(file_info.absoluteFilePath()))

    def _open_file(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ------------------------------------------------------------------
    def _update_media_mode(self, directory: Path) -> None:
        should_enable = self._is_media_heavy(directory)
        if should_enable != self._media_icon_mode:
            self._media_icon_mode = should_enable
            self._apply_media_mode()
        elif self._media_icon_mode:
            self._apply_media_mode()

    def _apply_media_mode(self) -> None:
        if self._media_icon_mode:
            icon_edge = 160
            self._model.set_thumbnail_edge(icon_edge)
            self._tile_view.setIconSize(QSize(icon_edge, icon_edge))
            self._tile_view.setGridSize(self._calculate_grid_size(icon_edge))
            self._view_stack.setCurrentWidget(self._tile_view)
        else:
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
            return True
        if media_files < self.MEDIA_MIN_COUNT:
            return False
        ratio = media_files / total_files
        return ratio >= self.MEDIA_RATIO_THRESHOLD

    def _handle_selection_changed(self, *_args) -> None:
        self._update_action_states()

