"""File browser widget that powers each tab."""

from __future__ import annotations

import logging
import os
import shlex
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import Literal, TypedDict, cast

from PyQt6.QtCore import (
    QDir,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QObject,
    QPoint,
    QProcess,
    QRect,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QDrag,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QRubberBand,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .file_browser_actions import file_action_states
from .file_browser_background import FileBrowserThumbnailScheduler
from .file_browser_drop import (
    drop_action_for_modifiers,
    drop_target_directory,
    has_blocked_self_move,
    local_paths_from_urls,
    should_move_from_drop_action,
)
from .file_browser_helpers import (
    deletion_replacement_path,
    resolve_windows_program,
)
from .file_browser_media_mode import (
    calculate_grid_size,
    is_media_heavy_directory,
    media_mode_button_text,
)
from .file_browser_navigation import (
    DirectoryFingerprint,
    directory_fingerprint,
    directory_fingerprint_changed,
    navigation_history_step,
    navigation_target,
    path_to_focus_after_go_up,
    resolve_address_path,
    same_navigation_path,
    should_record_history,
)
from .file_browser_selection import (
    has_selection_path_in_directory,
    pending_selection_action,
    rubber_band_intersecting_rows,
    rubber_band_target_rows,
)
from .file_browser_status import (
    BrowserStatus,
    browser_status_from_counts,
    directory_item_counts,
)
from .file_browser_visible import index_identity, tile_probe_points, tile_probe_step
from .file_operation_jobs import FileOperationJob
from .file_operations import (
    FileOperationRequest,
    FileOperationResult,
    create_file,
    create_folder,
    delete_paths,
    perform_copy_or_move,
    perform_copy_or_move_with_result,
    rename_path,
    resolve_destination,
)
from .media_file_system_model import MediaFileSystemModel

logger = logging.getLogger(__name__)


class _ClipboardPayload(TypedDict):
    paths: list[Path]
    mode: Literal["copy", "move"]


_ClipboardVisualMode = Literal["copy", "move"]


NAVIGATION_SELECTION_KEYS = {
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_Left,
    Qt.Key.Key_Right,
}

NAVIGATION_CURSOR_ACTIONS = {
    Qt.Key.Key_Up: QAbstractItemView.CursorAction.MoveUp,
    Qt.Key.Key_Down: QAbstractItemView.CursorAction.MoveDown,
    Qt.Key.Key_Left: QAbstractItemView.CursorAction.MoveLeft,
    Qt.Key.Key_Right: QAbstractItemView.CursorAction.MoveRight,
}


def navigation_event_without_control(event: QKeyEvent) -> QKeyEvent | None:
    """Return an equivalent navigation key event with Ctrl stripped."""
    if event.key() not in NAVIGATION_SELECTION_KEYS:
        return None
    modifiers = event.modifiers()
    if not modifiers & Qt.KeyboardModifier.ControlModifier:
        return None
    stripped_modifiers = Qt.KeyboardModifier(
        modifiers.value & ~Qt.KeyboardModifier.ControlModifier.value
    )
    return QKeyEvent(
        event.type(),
        event.key(),
        stripped_modifiers,
        event.text(),
        event.isAutoRepeat(),
        event.count(),
    )


def navigation_cursor_action(key: int) -> QAbstractItemView.CursorAction | None:
    """Return the view cursor action for a navigation key."""
    return NAVIGATION_CURSOR_ACTIONS.get(Qt.Key(key))


def _select_path_later(tab: FileBrowserTab, path: Path) -> None:
    tab._select_path(path, QAbstractItemView.ScrollHint.PositionAtCenter)


class _DirectoryCountSignals(QObject):
    counted = pyqtSignal(str, int, int, int)  # path, generation, folders, files


class _DirectoryCountJob(QRunnable):
    def __init__(self, path: Path, generation: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._path = path
        self._generation = generation
        self.signals = _DirectoryCountSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        folder_count, file_count = directory_item_counts(self._path)
        self.signals.counted.emit(
            str(self._path),
            self._generation,
            folder_count,
            file_count,
        )


def _configure_arrow_button(
    button: QToolButton,
    *,
    text: str,
    accessible_name: str,
    tooltip: str,
) -> None:
    button.setText(text)
    button.setAccessibleName(accessible_name)
    button.setToolTip(tooltip)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setFixedSize(QSize(28, 28))
    button.setStyleSheet(
        """
        QToolButton {
            font-size: 16px;
            font-weight: 600;
            padding: 0;
            border: 1px solid #3a3f46;
            border-radius: 4px;
            color: #e6e8eb;
            background: #24282e;
        }
        QToolButton:hover {
            background: #303640;
            border-color: #59616d;
        }
        QToolButton:pressed {
            background: #1c2026;
        }
        QToolButton:disabled {
            color: #6f7680;
            background: #1b1e23;
            border-color: #2b3037;
        }
        """
    )


class _BaseFileViewMixin:
    """Adds reusable drag-and-drop and context menu behaviours."""

    def _init_file_view(self, tab: FileBrowserTab) -> None:
        view = cast(QAbstractItemView, self)
        self._tab = tab
        self._drag_start_pos = None
        self._drag_on_item = False
        self._drag_start_path: Path | None = None
        self._drop_target_index = QModelIndex()
        view.setDragEnabled(True)
        view.setAcceptDrops(True)
        view.viewport().setAcceptDrops(True)
        view.setDropIndicatorShown(True)
        view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        view.setDefaultDropAction(Qt.DropAction.MoveAction)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(partial(tab._show_context_menu, view))

    def selected_paths(self) -> list[Path]:
        view = cast(QAbstractItemView, self)
        selection_model = view.selectionModel()
        if not selection_model:
            return []
        rows = selection_model.selectedRows() or selection_model.selectedIndexes()
        if not rows:
            return []
        return self._tab._paths_from_indexes(rows)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position()
            # クリックされた位置にアイテムが存在するかどうかをチェック
            index_at_pos = cast(QAbstractItemView, self).indexAt(event.position().toPoint())
            self._drag_on_item = index_at_pos.isValid()
            self._drag_start_path = self._path_from_index(index_at_pos)
        super().mousePressEvent(event)  # type: ignore[attr-defined]

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_start_pos is not None:
            distance = (event.position() - self._drag_start_pos).manhattanLength()
            if not self._drag_on_item:
                super().mouseMoveEvent(event)  # type: ignore[attr-defined]
                return
            if distance < QApplication.startDragDistance():
                event.accept()
                return
            if self._drag_on_item:
                self._clear_drag_selection_artifacts()
                self.startDrag(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)
                self._drag_start_pos = None
                self._drag_start_path = None
                return
        super().mouseMoveEvent(event)  # type: ignore[attr-defined]

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        """
        このビュー上でマウスボタンが離されたときに呼び出される。
        戻る/進むボタンを処理し、親のタブコンテナに伝える。
        """
        button = event.button()
        if button == Qt.MouseButton.BackButton:
            logger.debug("Back mouse button pressed")
            self._tab.go_back()
            event.accept()
        elif button == Qt.MouseButton.ForwardButton:
            logger.debug("Forward mouse button pressed")
            self._tab.go_forward()
            event.accept()
        else:
            super().mouseReleaseEvent(event)  # type: ignore[attr-defined]
        self._drag_start_pos = None
        self._drag_start_path = None

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # noqa: N802
        paths = self._drag_paths()
        if not paths:
            logger.debug("Ignoring drag start because no file paths are available")
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        drag = QDrag(cast(QWidget, self))
        drag.setMimeData(mime)
        default_action = Qt.DropAction.MoveAction
        drag.exec(
            Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.TargetMoveAction,
            default_action,
        )
        self._reset_drag_state()

    def _reset_drag_state(self) -> None:
        self._drag_start_pos = None
        self._drag_start_path = None
        self._clear_drag_selection_artifacts()

    def _clear_drag_selection_artifacts(self) -> None:
        view = cast(QAbstractItemView, self)
        view.setState(QAbstractItemView.State.NoState)
        self._set_drop_target_index(QModelIndex())
        view.viewport().update()

    def _drop_target_index_at(self, pos: QPoint) -> QModelIndex:
        index = cast(QAbstractItemView, self).indexAt(pos)
        if not index.isValid():
            return QModelIndex()
        source = index.siblingAtColumn(0)
        try:
            file_info = self._tab._model.fileInfo(source)
        except Exception:
            logger.debug("Could not resolve drop target path", exc_info=True)
            return QModelIndex()
        if not file_info.isDir():
            return QModelIndex()
        return source

    def _set_drop_target_index(self, index: QModelIndex) -> None:
        if self._same_model_index(self._drop_target_index, index):
            return
        previous = self._drop_target_index
        self._drop_target_index = QModelIndex(index) if index.isValid() else QModelIndex()
        self._update_drop_target_rect(previous)
        self._update_drop_target_rect(self._drop_target_index)

    def _update_drop_target_highlight(self, pos: QPoint) -> None:
        self._set_drop_target_index(self._drop_target_index_at(pos))

    def _update_drop_target_rect(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        rect = self._drop_target_rect(index)
        if rect.isValid():
            cast(QAbstractItemView, self).viewport().update(rect)

    def _drop_target_rect(self, index: QModelIndex) -> QRect:
        view = cast(QAbstractItemView, self)
        if isinstance(view, QTreeView):
            model = view.model()
            root = view.rootIndex()
            column_count = model.columnCount(root)
            rect = view.visualRect(index.siblingAtColumn(0))
            for column in range(1, column_count):
                rect = rect.united(view.visualRect(index.siblingAtColumn(column)))
            return rect
        return view.visualRect(index)

    def _is_drop_target_index(self, index: QModelIndex) -> bool:
        return self._same_model_index(index.siblingAtColumn(0), self._drop_target_index)

    def _clipboard_visual_mode(self, index: QModelIndex) -> _ClipboardVisualMode | None:
        return self._tab._clipboard_visual_mode_for_index(index)

    @staticmethod
    def _same_model_index(left: QModelIndex, right: QModelIndex) -> bool:
        return left == right

    def _drag_paths(self) -> list[Path]:
        selected = self.selected_paths()
        if not self._drag_start_path:
            return selected
        if not selected or self._drag_start_path not in selected:
            return [self._drag_start_path]
        return selected

    def _path_from_index(self, index: QModelIndex) -> Path | None:
        if not index.isValid():
            return None
        source = index.siblingAtColumn(0)
        try:
            path = self._tab._model.filePath(source)
        except Exception:
            logger.debug("Could not resolve drag start path", exc_info=True)
            return None
        if not path:
            return None
        return Path(path)

    def event(self, event: QEvent) -> bool:
        # QAbstractItemView can route internal drags through event() before
        # dragEnterEvent()/dragMoveEvent(), so accept URL drops here as well.
        if event.type() == QEvent.Type.DragEnter:
            return self._handle_drag_enter_event(event)
        if event.type() == QEvent.Type.DragMove:
            return self._handle_drag_move_event(event)
        if event.type() == QEvent.Type.Drop:
            return self._handle_drop_event(event)
        if event.type() == QEvent.Type.DragLeave:
            self._reset_drag_state()
        return super().event(event)  # type: ignore[misc]

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        self._handle_drag_enter_event(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        self._handle_drag_move_event(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._handle_drop_event(event)

    def _handle_drag_enter_event(self, event) -> bool:
        if event.mimeData().hasUrls():
            action = drop_action_for_modifiers(event.modifiers())
            event.setDropAction(action)
            event.accept()
            return True
        event.ignore()
        return False

    def _handle_drag_move_event(self, event) -> bool:
        if event.mimeData().hasUrls():
            action = drop_action_for_modifiers(event.modifiers())
            view = cast(QAbstractItemView, self)
            if view.state() != QAbstractItemView.State.NoState:
                self._clear_drag_selection_artifacts()
            self._update_drop_target_highlight(event.position().toPoint())
            event.setDropAction(action)
            event.accept()
            return True
        self._set_drop_target_index(QModelIndex())
        event.ignore()
        return False

    def _handle_drop_event(self, event) -> bool:
        if not event.mimeData().hasUrls():
            self._set_drop_target_index(QModelIndex())
            event.ignore()
            return False
        paths = local_paths_from_urls(event.mimeData().urls())
        if not paths:
            self._set_drop_target_index(QModelIndex())
            event.ignore()
            return False
        pos = event.position().toPoint()
        index = cast(QAbstractItemView, self).indexAt(pos)
        target_dir = self._tab._current_path
        if index.isValid():
            file_info = self._tab._model.fileInfo(index)
            target_dir = drop_target_directory(
                self._tab._current_path,
                Path(file_info.absoluteFilePath()),
                item_is_dir=file_info.isDir(),
            )
        move = should_move_from_drop_action(
            event.dropAction(),
            event.modifiers(),
        )
        self._tab._handle_external_drop(paths, target_dir, move)
        self._set_drop_target_index(QModelIndex())
        event.setDropAction(Qt.DropAction.MoveAction if move else Qt.DropAction.CopyAction)
        event.accept()
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        modifiers = event.modifiers()
        navigation_modifiers = modifiers & ~Qt.KeyboardModifier.KeypadModifier
        if navigation_modifiers in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.ControlModifier,
        ) and self._select_single_navigation_target(event.key()):
            event.accept()
            return
        replacement_event = navigation_event_without_control(event)
        if replacement_event is not None:
            super().keyPressEvent(replacement_event)  # type: ignore[attr-defined]
            event.setAccepted(replacement_event.isAccepted())
            return
        super().keyPressEvent(event)  # type: ignore[attr-defined]

    def _select_single_navigation_target(self, key: int) -> bool:
        if isinstance(self, QTreeView) and key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            return False
        action = navigation_cursor_action(key)
        if action is None:
            return False
        view = cast(QAbstractItemView, self)
        selection_model = view.selectionModel()
        if selection_model is None:
            return False
        target = view.moveCursor(action, Qt.KeyboardModifier.NoModifier)
        if not target.isValid():
            return False
        selection_model.setCurrentIndex(target, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        view.scrollTo(target)
        return True


class _FileTreeView(_BaseFileViewMixin, QTreeView):
    def __init__(self, tab: FileBrowserTab) -> None:
        QTreeView.__init__(self, tab)
        self._init_file_view(tab)
        self.setItemDelegate(_DropTargetItemDelegate(self))

        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._rubber_band_origin: QPoint | None = None

        self._last_selection = QItemSelection()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)  # Mixinの処理を先に呼ぶ

        if not self._drag_on_item and event.button() == Qt.MouseButton.LeftButton:
            origin = event.pos()
            self._rubber_band_origin = origin
            self._rubber_band.setGeometry(QRect(origin, QSize()))
            self._rubber_band.show()

            self._last_selection = QItemSelection(self.selectionModel().selection())

            event.accept()
            return

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._rubber_band.isVisible() and self._rubber_band_origin is not None:
            rect = QRect(self._rubber_band_origin, event.pos()).normalized()
            self._rubber_band.setGeometry(rect)

            self._update_rubber_band_selection(event.modifiers())

            event.accept()
            return

        super().mouseMoveEvent(event)  # MixinのD&D処理

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._rubber_band.isVisible():
            self._rubber_band.hide()

            self._last_selection = QItemSelection()

            event.accept()
            return

        super().mouseReleaseEvent(event)  # Mixinの処理

    def _update_rubber_band_selection(self, modifiers: Qt.KeyboardModifier) -> None:
        """ラバーバンド内のアイテムをリアルタイムで選択するメソッド"""
        selection_rect = self._rubber_band.geometry()
        root = self.rootIndex()
        model = self.model()
        column_count = model.columnCount()
        row_rects: list[tuple[int, QRect]] = []

        for row in range(model.rowCount(root)):
            first_col_index = model.index(row, 0, root)
            if not first_col_index.isValid():
                continue
            last_col_index = model.index(row, column_count - 1, root)
            full_row_rect = self.visualRect(first_col_index).united(self.visualRect(last_col_index))
            row_rects.append((row, full_row_rect))

        current_rows = rubber_band_intersecting_rows(
            selection_rect, self.viewport().rect(), row_rects
        )
        current_indexes = {(row, column) for row in current_rows for column in range(column_count)}
        previous_indexes = {
            (index.row(), index.column()) for index in self._last_selection.indexes()
        }
        target_rows = rubber_band_target_rows(
            current_indexes,
            previous_indexes,
            control_pressed=bool(modifiers & Qt.KeyboardModifier.ControlModifier),
        )

        target_selection = QItemSelection()
        for row in target_rows:
            start_index = model.index(row, 0, root)
            end_index = model.index(row, column_count - 1, root)
            if start_index.isValid() and end_index.isValid():
                target_selection.select(start_index, end_index)
        selection_model = self.selectionModel()
        selection_model.select(target_selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)


class _DropTargetItemDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        view_option = QStyleOptionViewItem(option)
        is_drop_target = self._is_drop_target(view_option, index)
        if is_drop_target:
            view_option.state |= QStyle.StateFlag.State_Selected
            view_option.state |= QStyle.StateFlag.State_Active
        clipboard_mode = self._clipboard_visual_mode(view_option, index)
        if clipboard_mode == "move" and not is_drop_target:
            painter.save()
            painter.setOpacity(0.45)
            super().paint(painter, view_option, index)
            painter.restore()
            return
        super().paint(painter, view_option, index)
        if clipboard_mode == "copy" and index.column() == 0:
            self._draw_copy_marker(painter, view_option)

    @staticmethod
    def _is_drop_target(option: QStyleOptionViewItem, index: QModelIndex) -> bool:
        checker = getattr(option.widget, "_is_drop_target_index", None)
        return bool(checker and checker(index))

    @staticmethod
    def _clipboard_visual_mode(
        option: QStyleOptionViewItem, index: QModelIndex
    ) -> _ClipboardVisualMode | None:
        checker = getattr(option.widget, "_clipboard_visual_mode", None)
        mode = checker(index) if checker else None
        return mode if mode in ("copy", "move") else None

    @staticmethod
    def _draw_copy_marker(painter: QPainter, option: QStyleOptionViewItem) -> None:
        accent = option.palette.highlight().color()
        border = QColor(accent)
        border.setAlpha(130)
        marker_rect = QRect(option.rect.left(), option.rect.top(), 3, option.rect.height())
        painter.save()
        painter.fillRect(marker_rect, border)
        painter.restore()


class _FileTileView(_BaseFileViewMixin, QListView):
    LAYOUT_BATCH_SIZE = 128

    def __init__(self, tab: FileBrowserTab) -> None:
        QListView.__init__(self, tab)
        self._init_file_view(tab)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setLayoutMode(QListView.LayoutMode.SinglePass)
        self.setBatchSize(self.LAYOUT_BATCH_SIZE)
        self.setSpacing(16)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionRectVisible(True)
        self.setItemDelegate(_TwoLineTileNameDelegate(self))


class _TwoLineTileNameDelegate(QStyledItemDelegate):
    LABEL_LINES = 2
    HORIZONTAL_PADDING = 12
    ICON_TOP_PADDING = 4
    LABEL_TOP_PADDING = 8

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        view_option = QStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)

        style = view_option.widget.style() if view_option.widget else QApplication.style()
        is_drop_target = self._is_drop_target(view_option, index)
        clipboard_mode = self._clipboard_visual_mode(view_option, index)
        icon_mode = (
            QIcon.Mode.Selected
            if view_option.state & QStyle.StateFlag.State_Selected or is_drop_target
            else QIcon.Mode.Normal
        )
        icon_rect, text_rect = self._tile_rects(
            view_option,
            icon_size=self._stable_thumbnail_icon_size(view_option, icon_mode),
        )
        text = self._two_line_text(view_option.text, view_option.fontMetrics, text_rect.width())

        painter.save()
        self._draw_tile_background(painter, view_option, style)
        if is_drop_target:
            self._draw_drop_target_background(painter, view_option)
        elif clipboard_mode == "copy":
            self._draw_copy_background(painter, view_option)

        if clipboard_mode == "move" and not is_drop_target:
            painter.setOpacity(0.45)

        self._draw_icon(painter, view_option.icon, icon_rect, icon_mode)

        if view_option.state & QStyle.StateFlag.State_Selected or is_drop_target:
            painter.fillRect(text_rect, view_option.palette.highlight())
            painter.setPen(view_option.palette.highlightedText().color())
        else:
            painter.fillRect(text_rect, view_option.palette.base())
            painter.setPen(view_option.palette.text().color())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            text,
        )
        painter.restore()

    @staticmethod
    def _stable_thumbnail_icon_size(
        option: QStyleOptionViewItem, icon_mode: QIcon.Mode
    ) -> QSize | None:
        # QListView can transiently shrink option.rect after jumping to the
        # bottom while keeping decorationSize at 160. For generated thumbnails
        # we intentionally keep the decoration height so they are not repainted
        # smaller. Platform folder icons stay clipped to the item rect because
        # they may not have a generated 160px pixmap.
        decoration = option.decorationSize
        if decoration.isEmpty() or option.rect.height() >= decoration.height():
            return None
        available = option.icon.availableSizes(icon_mode, QIcon.State.Off)
        if decoration in available:
            return decoration
        normal_available = option.icon.availableSizes(QIcon.Mode.Normal, QIcon.State.Off)
        if decoration in normal_available:
            return decoration
        return None

    @staticmethod
    def _is_drop_target(option: QStyleOptionViewItem, index: QModelIndex) -> bool:
        checker = getattr(option.widget, "_is_drop_target_index", None)
        return bool(checker and checker(index))

    def _draw_tile_background(
        self, painter: QPainter, option: QStyleOptionViewItem, style: QStyle
    ) -> None:
        background_option = QStyleOptionViewItem(option)
        background_option.text = ""
        background_option.icon = QIcon()
        background_option.state &= ~QStyle.StateFlag.State_Selected
        background_option.state &= ~QStyle.StateFlag.State_HasFocus
        style.drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem,
            background_option,
            painter,
            option.widget,
        )

    def _draw_drop_target_background(self, painter: QPainter, option: QStyleOptionViewItem) -> None:
        fill_color = option.palette.highlight().color()
        fill_color.setAlpha(70)
        border_color = option.palette.highlight().color()
        painter.fillRect(option.rect.adjusted(1, 1, -1, -1), fill_color)
        painter.setPen(border_color)
        painter.drawRect(option.rect.adjusted(1, 1, -2, -2))

    def _draw_copy_background(self, painter: QPainter, option: QStyleOptionViewItem) -> None:
        fill_color = option.palette.highlight().color()
        fill_color.setAlpha(32)
        border_color = option.palette.highlight().color()
        border_color.setAlpha(145)
        painter.fillRect(option.rect.adjusted(1, 1, -1, -1), fill_color)
        painter.setPen(border_color)
        painter.drawRect(option.rect.adjusted(1, 1, -2, -2))

    @staticmethod
    def _draw_icon(
        painter: QPainter,
        icon: QIcon,
        icon_rect: QRect,
        icon_mode: QIcon.Mode,
    ) -> None:
        painter.save()
        painter.setClipRect(icon_rect)
        icon.paint(
            painter,
            icon_rect,
            Qt.AlignmentFlag.AlignCenter,
            icon_mode,
            QIcon.State.Off,
        )
        painter.restore()

    @staticmethod
    def _clipboard_visual_mode(
        option: QStyleOptionViewItem, index: QModelIndex
    ) -> _ClipboardVisualMode | None:
        checker = getattr(option.widget, "_clipboard_visual_mode", None)
        mode = checker(index) if checker else None
        return mode if mode in ("copy", "move") else None

    def _tile_rects(
        self, option: QStyleOptionViewItem, *, icon_size: QSize | None = None
    ) -> tuple[QRect, QRect]:
        rect = option.rect
        tile_icon_size = icon_size or option.decorationSize
        icon_width = min(tile_icon_size.width(), rect.width())
        icon_height = (
            min(tile_icon_size.height(), rect.height())
            if icon_size is None
            else min(tile_icon_size.height(), option.decorationSize.height())
        )
        icon_x = rect.x() + (rect.width() - icon_width) // 2
        icon_y = rect.y() + self.ICON_TOP_PADDING
        icon_rect = QRect(icon_x, icon_y, icon_width, icon_height)

        line_height = option.fontMetrics.lineSpacing()
        text_height = line_height * self.LABEL_LINES
        text_y = icon_rect.bottom() + 1 + self.LABEL_TOP_PADDING
        text_rect = QRect(
            rect.x() + self.HORIZONTAL_PADDING // 2,
            text_y,
            max(1, rect.width() - self.HORIZONTAL_PADDING),
            text_height,
        )
        return icon_rect, text_rect

    @classmethod
    def _two_line_text(cls, text: str, font_metrics, width: int) -> str:
        if not text:
            return text

        remaining = text
        lines: list[str] = []
        for _line_number in range(cls.LABEL_LINES):
            line, remaining = cls._take_line(remaining, font_metrics, width)
            lines.append(line)
            if not remaining:
                return "\n".join(lines)

        lines[-1] = font_metrics.elidedText(
            lines[-1] + remaining, Qt.TextElideMode.ElideRight, width
        )
        return "\n".join(lines)

    @staticmethod
    def _take_line(text: str, font_metrics, width: int) -> tuple[str, str]:
        line = ""
        for index, character in enumerate(text):
            candidate = line + character
            if line and font_metrics.horizontalAdvance(candidate) > width:
                return line.rstrip(), text[index:].lstrip()
            line = candidate
        return line.rstrip(), ""


class FileBrowserTab(QWidget):
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
        super().__init__(parent)
        self._media_icon_mode = False
        self._current_path = Path.home()
        self._navigation_history: list[Path] = []
        self._forward_history: list[Path] = []
        self._has_loaded_root = False
        self._current_directory_fingerprint: DirectoryFingerprint | None = None
        self._current_directory_has_local_changes = False
        self._is_active = False

        self._model = MediaFileSystemModel(self)
        self._model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        # self._model.thumbnailUpdated.connect(self._handle_thumbnail_updated)
        self._model.setResolveSymlinks(True)
        self._model.setReadOnly(True)

        # モデルのレイアウトが変更されたら、サムネイル要求をトリガーする
        self._model.layoutChanged.connect(self._on_layout_changed)

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
        self._manual_media_mode: bool | None = None
        self._clipboard: _ClipboardPayload | None = None
        self._clipboard_path_set: set[Path] = set()
        self._pending_selection_path: Path | None = None
        self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        self._status_folder_count = 0
        self._status_file_count = 0
        self._status_count_generation = 0
        self._status_count_jobs: dict[int, _DirectoryCountJob] = {}
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

        self._rename_action = QAction("Rename", self)
        self._rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        self._rename_action.triggered.connect(self._rename_selected)

        self._new_file_action = QAction("New File", self)
        self._new_file_action.setShortcut(QKeySequence("Ctrl+N"))
        self._new_file_action.triggered.connect(self._create_new_file)

        self._new_folder_action = QAction("New Folder", self)
        self._new_folder_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self._new_folder_action.triggered.connect(self._create_new_folder)

        for action in (
            self._rename_action,
            self._copy_action,
            self._cut_action,
            self._paste_action,
            self._delete_action,
            self._new_file_action,
            self._new_folder_action,
        ):
            self.addAction(action)

        self._setup_shortcuts()
        self._update_action_states()

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+A"), self, self._select_all)
        QShortcut(QKeySequence("Alt+D"), self, self._focus_path_edit)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self, self.go_back)
        QShortcut(QKeySequence("Alt+Left"), self, self.go_back)
        QShortcut(QKeySequence("Alt+Right"), self, self.go_forward)

    def _update_action_states(self) -> None:
        paths = self._selected_paths()
        clipboard_ready = isinstance(self._clipboard, dict) and bool(self._clipboard.get("paths"))
        states = file_action_states(
            len(paths),
            clipboard_has_paths=clipboard_ready,
            current_path_exists=self._current_path.exists(),
        )
        self._copy_action.setEnabled(states["copy"])
        self._cut_action.setEnabled(states["cut"])
        self._delete_action.setEnabled(states["delete"])
        self._rename_action.setEnabled(states["rename"])
        self._paste_action.setEnabled(states["paste"])
        self._new_file_action.setEnabled(states["new_file"])
        self._new_folder_action.setEnabled(states["new_folder"])
        self._update_navigation_button_states()
        self._emit_status_changed(paths)

    def _update_navigation_button_states(self) -> None:
        if not hasattr(self, "_back_button") or not hasattr(self, "_forward_button"):
            return
        self._back_button.setEnabled(bool(self._navigation_history))
        self._forward_button.setEnabled(bool(self._forward_history))

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

    def _set_clipboard(self, payload: _ClipboardPayload | None) -> None:
        previous_paths = self._clipboard_path_set
        self._clipboard = payload
        self._clipboard_path_set = self._clipboard_paths_from_payload(payload)
        self._repaint_clipboard_paths(previous_paths | self._clipboard_path_set)
        self._update_action_states()

    def _clipboard_paths_from_payload(self, payload: _ClipboardPayload | None) -> set[Path]:
        if not payload:
            return set()
        return {self._normalise_clipboard_path(path) for path in payload["paths"]}

    def _clipboard_visual_mode_for_index(self, index: QModelIndex) -> _ClipboardVisualMode | None:
        if not self._clipboard or not index.isValid():
            return None
        source = index.siblingAtColumn(0)
        path_text = self._model.filePath(source)
        if not path_text:
            return None
        if self._normalise_clipboard_path(Path(path_text)) not in self._clipboard_path_set:
            return None
        return self._clipboard["mode"]

    def _repaint_clipboard_paths(self, paths: set[Path]) -> None:
        for path in paths:
            index = self._model.index(str(path))
            if index.isValid():
                self._repaint_index_in_views(index.siblingAtColumn(0))

    def _repaint_index_in_views(self, index: QModelIndex) -> None:
        for view in (self._tree_view, self._tile_view):
            if view is self._tree_view:
                rect = cast(_BaseFileViewMixin, view)._drop_target_rect(index)
            else:
                rect = view.visualRect(index)
            if rect.isValid():
                view.viewport().update(rect)

    @staticmethod
    def _normalise_clipboard_path(path: Path) -> Path:
        try:
            return Path(os.path.normcase(os.path.abspath(path)))
        except OSError:
            return path

    def _selected_paths(self) -> list[Path]:
        view = self._active_view()
        return cast(_BaseFileViewMixin, view).selected_paths()

    def status_summary(self) -> BrowserStatus:
        return browser_status_from_counts(
            self._status_folder_count,
            self._status_file_count,
            self._selected_paths(),
        )

    def _select_all(self) -> None:
        view = self._active_view()
        if view:
            view.selectAll()

    def _focus_path_edit(self) -> None:
        self._path_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._path_edit.selectAll()

    def _rename_selected(self) -> None:
        paths = self._selected_paths()
        if len(paths) != 1:
            return
        original = paths[0]
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=original.name)
        if not ok or not new_name or new_name == original.name:
            return
        target, error = rename_path(original, new_name)
        if error:
            QMessageBox.warning(self, "Rename failed", error)
            return
        if target is None:
            return
        self._mark_changed_directories([original.parent, target.parent])
        self.refresh()
        self._select_path(target)

    def _create_new_file(self) -> None:
        if not self._current_path.exists():
            return
        name, ok = QInputDialog.getText(self, "New File", "File name:", text="New File.txt")
        if not ok or not name.strip():
            return
        target, error = create_file(self._current_path, name.strip())
        if error:
            QMessageBox.warning(self, "Create file failed", error)
            return
        if target is None:
            return
        self._mark_directory_changed(self._current_path)
        self.refresh()
        self._select_path(target)

    def _create_new_folder(self) -> None:
        if not self._current_path.exists():
            return
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:", text="New Folder")
        if not ok or not name.strip():
            return
        target, error = create_folder(self._current_path, name.strip())
        if error:
            QMessageBox.warning(self, "Create folder failed", error)
            return
        if target is None:
            return
        self._mark_directory_changed(self._current_path)
        self.refresh()
        self._select_path(target)

    def _select_path(
        self,
        path: Path,
        scroll_hint: QAbstractItemView.ScrollHint = QAbstractItemView.ScrollHint.EnsureVisible,
    ) -> bool:
        index = self._model.index(str(path))
        if not index.isValid():
            return False
        view = self._active_view()
        selection_model = view.selectionModel()
        if selection_model:
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
        view.scrollTo(index, scroll_hint)
        return True

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
        menu.addAction(self._rename_action)
        menu.addSeparator()
        menu.addAction(self._copy_action)
        menu.addAction(self._cut_action)
        menu.addAction(self._paste_action)
        menu.addSeparator()
        menu.addAction(self._delete_action)
        menu.addSeparator()
        menu.addAction(self._new_file_action)
        menu.addAction(self._new_folder_action)
        menu.exec(view.viewport().mapToGlobal(point))

    def _copy_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        self._set_clipboard({"paths": paths, "mode": "copy"})

    def _cut_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        self._set_clipboard({"paths": paths, "mode": "move"})

    def _paste_into_current(self) -> None:
        if not self._clipboard:
            return
        paths = self._clipboard["paths"]
        if not paths:
            return
        move = self._clipboard["mode"] == "move"
        result = self._perform_copy_or_move_with_result(paths, self._current_path, move=move)
        self._mark_changed_directories(result.changed_dirs)
        if move:
            self._set_clipboard(None)
        else:
            self._update_action_states()
        self.refresh()

    def _delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        select_after_delete = self._selection_path_before_deleted_items(paths)
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
        errors = delete_paths(paths)
        if errors:
            QMessageBox.warning(self, "Delete failed", "\n".join(errors))
        self._mark_changed_directories([path.parent for path in paths if not path.exists()])
        self._pending_selection_path = select_after_delete
        self.refresh()
        self._update_action_states()

    def _perform_copy_or_move(
        self, sources: list[Path], dest_dir: Path, *, move: bool
    ) -> list[str]:
        errors = perform_copy_or_move(sources, dest_dir, move=move)
        if errors:
            QMessageBox.warning(self, "Operation issues", "\n".join(errors))
        return errors

    def _perform_copy_or_move_with_result(
        self, sources: list[Path], dest_dir: Path, *, move: bool
    ) -> FileOperationResult:
        result = perform_copy_or_move_with_result(sources, dest_dir, move=move)
        if result.errors:
            QMessageBox.warning(self, "Operation issues", "\n".join(result.errors))
        return result

    def _start_file_operation(
        self,
        request: FileOperationRequest,
        *,
        select_after: list[Path] | None = None,
    ) -> FileOperationJob:
        job = FileOperationJob(request)

        def handle_finished(result: object) -> None:
            with suppress(ValueError):
                self._file_operation_jobs.remove(job)
            if not isinstance(result, FileOperationResult):
                return
            self._handle_file_operation_finished(result, select_after=select_after)

        job.signals.finished.connect(handle_finished)
        self._file_operation_jobs.append(job)
        QThreadPool.globalInstance().start(job)
        return job

    def _handle_file_operation_finished(
        self,
        result: FileOperationResult,
        *,
        select_after: list[Path] | None = None,
    ) -> None:
        self._mark_changed_directories(result.changed_dirs)
        if result.errors:
            QMessageBox.warning(self, "Operation issues", "\n".join(result.errors))
        if not result.errors and select_after:
            self._pending_selection_path = next(
                (path for path in select_after if path.exists()),
                None,
            )
        self.refresh()
        if self._pending_selection_path is not None:
            self._select_path(self._pending_selection_path)

    def _resolve_destination(self, dest_dir: Path, name: str, move: bool) -> Path:
        return resolve_destination(dest_dir, name, move)

    def _mark_current_directory_changed(self) -> None:
        self._current_directory_has_local_changes = True

    def _mark_directory_changed(self, directory: Path) -> None:
        if same_navigation_path(directory, self._current_path):
            self._mark_current_directory_changed()
            return
        self._model.invalidate_folder_thumbnail_preview(directory)

    def _mark_changed_directories(self, directories: list[Path]) -> None:
        seen: list[Path] = []
        for directory in directories:
            if any(same_navigation_path(directory, known) for known in seen):
                continue
            seen.append(directory)
            self._mark_directory_changed(directory)

    @staticmethod
    def _is_within(path: Path, potential_parent: Path) -> bool:
        try:
            return path.resolve().is_relative_to(potential_parent.resolve())
        except Exception:
            return False

    def _handle_external_drop(
        self,
        paths: list[Path],
        target_dir: Path,
        move: bool,
        *,
        select_after: list[Path] | None = None,
    ) -> bool:
        if not target_dir.exists():
            QMessageBox.warning(self, "Drop failed", f"Destination {target_dir} does not exist.")
            return False
        if move and has_blocked_self_move(paths, target_dir):
            logger.info(
                "Blocked moving a folder into itself: paths=%s target=%s", paths, target_dir
            )
            return False
        result = self._perform_copy_or_move_with_result(paths, target_dir, move=move)
        self._mark_changed_directories(result.changed_dirs)
        if not result.errors and select_after:
            self._pending_selection_path = next(
                (path for path in select_after if path.exists()),
                None,
            )
        self.refresh()
        if self._pending_selection_path is not None:
            self._select_path(self._pending_selection_path)
        return not bool(result.errors)

    def selection_replacement_for_removed_paths(self, paths: list[Path]) -> Path | None:
        return self._selection_path_before_deleted_items(paths)

    def restore_selection_after_removed_paths(
        self,
        removed_paths: list[Path],
        replacement: Path | None,
    ) -> None:
        if replacement is None:
            return
        if not any(
            path.parent == self._current_path and not path.exists() for path in removed_paths
        ):
            return
        self._pending_selection_path = replacement
        self.refresh()
        self._select_path(replacement)
        self.focus_view()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def navigate_to(self, path: Path, *, from_history: bool = False) -> bool:
        """Display the given directory as the current root."""
        if not path.exists():
            QMessageBox.warning(self, "Cannot navigate", f"{path} does not exist.")
            return False

        current = self._current_path
        target = navigation_target(path)
        target_is_current = self._has_loaded_root and same_navigation_path(current, target)
        should_invalidate_current_preview = (
            self._has_loaded_root
            and not target_is_current
            and (
                self._current_directory_has_local_changes
                or directory_fingerprint_changed(current, self._current_directory_fingerprint)
            )
        )
        if should_invalidate_current_preview:
            self._model.invalidate_folder_thumbnail_preview(current)
        if self._has_loaded_root and should_record_history(
            current, target, from_history=from_history
        ):
            self._navigation_history.append(current)
            self._forward_history.clear()

        self._current_path = target
        if not target_is_current:
            self._current_directory_has_local_changes = False
        if not target_is_current or self._current_directory_fingerprint is None:
            self._current_directory_fingerprint = directory_fingerprint(target)
        self._has_loaded_root = True
        self._path_edit.setText(str(target))

        root_index = self._model.setRootPath(str(target))
        self._tree_view.setRootIndex(root_index)
        self._tile_view.setRootIndex(root_index)
        self._update_media_mode(target, select_default=False)
        self._configure_header_sections()
        self._apply_name_column_width()
        self._connect_selection_signals()
        self.directoryChanged.emit(target)

        deferred_selection = from_history and self._pending_selection_path is not None
        if deferred_selection:
            self._schedule_select_pending_or_first_row()
        else:
            self._select_pending_or_first_row()

        self._restart_thumbnail_requests()  # ナビゲート後にサムネイル要求を再開
        self._update_navigation_button_states()
        logger.debug("Navigated to %s and restarted thumbnail requests", target)
        return True

    def activate(self) -> None:
        """Start visible-item thumbnail work when this tab becomes active."""
        if self._is_active:
            return
        logger.debug("Activating tab for %s", self._current_path)
        self._is_active = True
        self._restart_thumbnail_requests()

    def deactivate(self) -> None:
        """Stop visible-item thumbnail work when this tab becomes inactive."""
        if not self._is_active:
            return
        logger.debug("Deactivating tab for %s", self._current_path)
        self._is_active = False
        self.cancel_background_work()

    def cancel_background_work(self) -> None:
        """Cancel thumbnail, status, and file-operation work owned by this tab."""
        self._thumbnail_request_timer.stop()
        self._thumbnail_scheduler.cancel()
        self._model.cancel_background_work()
        self._status_count_generation += 1
        self._status_count_jobs.clear()
        for job in self._file_operation_jobs:
            job.cancel()
        self._file_operation_jobs.clear()

    def _on_layout_changed(self) -> None:
        """Restart visible thumbnail requests after model layout changes."""
        self._restart_thumbnail_requests()

    def _on_scroll(self) -> None:
        if not hasattr(self, "_thumbnail_scheduler"):
            return
        self._thumbnail_scheduler.handle_scroll()

    def _restart_thumbnail_requests(self) -> None:
        """Manually trigger a re-evaluation of visible items for thumbnail requests."""
        if not hasattr(self, "_thumbnail_scheduler"):
            return
        self._thumbnail_scheduler.restart()

    def _request_settled_thumbnails(self) -> None:
        if not hasattr(self, "_thumbnail_scheduler"):
            return
        self._set_thumbnail_scrolling(False)
        self._request_visible_thumbnails(scrolling=False)

    def _request_visible_thumbnails(self, *, scrolling: bool = False) -> None:
        if not hasattr(self, "_thumbnail_scheduler"):
            return
        self._thumbnail_scheduler.request_visible(scrolling=scrolling)

    def _set_thumbnail_scrolling(self, scrolling: bool) -> None:
        self._is_scrolling_for_thumbnails = scrolling

    def _request_visible_thumbnail_batch(self, scrolling: bool) -> int:
        view = self._active_view()
        if not view:
            return 0

        visible_indexes: list[QModelIndex] = []
        if isinstance(view, QTreeView):
            visible_indexes = self._visible_tree_indexes(view)
        elif isinstance(view, QListView):
            visible_indexes = self._visible_tile_indexes(view)

        request_limit = 6
        requested = self._model.set_visible_thumbnail_targets(
            visible_indexes,
            request_limit=request_limit,
            allow_folder_preview=not scrolling,
        )
        return requested

    def _visible_tree_indexes(self, view: QTreeView) -> list[QModelIndex]:
        indexes: list[QModelIndex] = []
        viewport = view.viewport()
        height = max(1, view.sizeHintForRow(0))
        y = 0
        seen_rows: set[int] = set()
        while y < viewport.height():
            index = view.indexAt(QPoint(0, y))
            if index.isValid() and index.row() not in seen_rows:
                seen_rows.add(index.row())
                indexes.append(index.siblingAtColumn(0))
            y += height
        bottom = view.indexAt(QPoint(0, max(0, viewport.height() - 1)))
        if bottom.isValid() and bottom.row() not in seen_rows:
            indexes.append(bottom.siblingAtColumn(0))
        return indexes

    def _visible_tile_indexes(self, view: QListView) -> list[QModelIndex]:
        indexes: list[QModelIndex] = []
        viewport = view.viewport()
        rect = viewport.rect()
        # QListView::indexAt only returns an item when the probe point is inside
        # the painted item rect. A tile-sized stride can skip every item if the
        # probes fall in gutters, so use a small viewport-local stride instead
        # of scanning model rows.
        step = tile_probe_step(view.iconSize().width())
        seen: set[tuple[int, int, int]] = set()

        for point in tile_probe_points(rect, step):
            index = view.indexAt(point)
            if index.isValid():
                key = index_identity(index.row(), index.column(), index.internalId())
                if key not in seen:
                    seen.add(key)
                    indexes.append(index.siblingAtColumn(0))
        return indexes

    def current_path(self) -> Path:
        return self._current_path

    def refresh(self) -> None:
        """Refresh the current directory view."""
        self.navigate_to(self._current_path)

    def go_back(self) -> None:
        """Navigate to the previous directory in this tab's history."""
        step = navigation_history_step(
            self._navigation_history,
            self._forward_history,
            self._current_path,
            direction="back",
        )
        if step is None:
            return
        previous_path = self._current_path
        old_pending = self._pending_selection_path
        old_scroll_hint = self._pending_selection_scroll_hint
        if has_selection_path_in_directory(previous_path, step.target):
            self._pending_selection_path = previous_path
            self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.PositionAtCenter
        if self.navigate_to(step.target, from_history=True):
            self._navigation_history = step.back_history
            self._forward_history = step.forward_history
            self._update_navigation_button_states()
        else:
            self._pending_selection_path = old_pending
            self._pending_selection_scroll_hint = old_scroll_hint

    def go_forward(self) -> None:
        """Navigate to the next directory in this tab's history."""
        step = navigation_history_step(
            self._navigation_history,
            self._forward_history,
            self._current_path,
            direction="forward",
        )
        if step is None:
            return
        if self.navigate_to(step.target, from_history=True):
            self._navigation_history = step.back_history
            self._forward_history = step.forward_history
            self._update_navigation_button_states()

    def go_up(self) -> None:
        """Navigate to the parent directory."""

        target = path_to_focus_after_go_up(self._current_path)
        if target is None:
            return
        parent, path_to_focus = target
        self.navigate_to(parent, from_history=True)
        QTimer.singleShot(0, partial(_select_path_later, self, path_to_focus))

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
        self._request_status_item_counts(self._current_path)
        self._update_media_mode(self._current_path, select_default=False)

        deferred_selection = self._selection_restore_timer.isActive()
        if not deferred_selection:
            self._select_pending_or_first_row()

        self._configure_header_sections()
        self._apply_name_column_width()

    def _handle_path_entered(self) -> None:
        text = self._path_edit.text().strip()
        if not text:
            return

        candidate = resolve_address_path(text, self._current_path)

        if candidate.exists():
            if candidate.is_file():
                self._open_file(candidate)
            else:
                self.navigate_to(candidate)
            return

        # ここまで来たら「コマンド」と見なす
        self._execute_address_command(text)

    def _execute_address_command(self, cmdline: str) -> None:
        # 例: 'zapall -f' / 'cmd' / 'powershell -NoExit'
        try:
            parts = shlex.split(cmdline, posix=False)
        except ValueError:
            logger.exception("Cannot parse address bar command: %s", cmdline)
            QMessageBox.warning(self, "Command", f"Cannot parse command line:\n{cmdline}")
            return
        if not parts:
            return

        program, *args = parts
        logger.debug("Executing address bar command program=%s args=%s", program, args)

        # 特例: 'cmd' 単体なら現在のフォルダで起動
        if program.lower() in ("cmd", "cmd.exe"):
            comspec = os.environ.get("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
            if not QProcess.startDetached(comspec, [], str(self._current_path)):
                logger.error("Failed to start cmd from %s", self._current_path)
                QMessageBox.warning(self, "Command", f"Failed to start:\n{cmdline}")
            return

        # 実行ファイルの解決
        resolved, is_batch = self._resolve_program_for_windows(program)
        if not resolved:
            logger.warning("Address bar command was not found: %s", program)
            QMessageBox.warning(
                self, "Command not found", f"'{program}' is not found in current folder or PATH."
            )
            return

        if is_batch:
            # .bat/.cmd はシェル経由で
            comspec = os.environ.get("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
            if not QProcess.startDetached(
                comspec, ["/C", resolved, *args], str(self._current_path)
            ):
                logger.error("Failed to start batch command: %s args=%s", resolved, args)
                QMessageBox.warning(self, "Command", f"Failed to start:\n{cmdline}")
        else:
            if not QProcess.startDetached(resolved, args, str(self._current_path)):
                logger.error("Failed to start command: %s args=%s", resolved, args)
                QMessageBox.warning(self, "Command", f"Failed to start:\n{cmdline}")

    def _resolve_program_for_windows(self, program: str) -> tuple[str | None, bool]:
        """
        実行ファイルのフルパスを返す。見つからなければ (None, False)。
        返り値の第2要素は .bat / .cmd かどうか。
        """
        return resolve_windows_program(program, self._current_path)

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
        logger.debug("keyPressEvent key=%s modifiers=%s", event.key(), event.modifiers())
        # Ctrl+Enter で選択中のフォルダを新しいタブで開く
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            logger.debug("Ctrl+Enter detected")
            selected = self._selected_index_path()
            if selected and selected.is_dir():
                self.requestOpenInNewTab.emit(selected)
                return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        """ウィンドウサイズが変更されたときに呼び出される"""
        # 親クラスの元のリサイズ処理を必ず呼び出す
        # スクロール時と同じタイマーを開始し、可視範囲のサムネイル要求をスケジュールする
        if self._is_active:  # アクティブなタブだけがリサイズに応答
            self._restart_thumbnail_requests()
            logger.debug("Resize event restarted thumbnail requests")
            return
        super().resizeEvent(event)

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

    def _select_pending_or_first_row(self) -> None:
        pending = self._pending_selection_path
        pending_scroll_hint = self._pending_selection_scroll_hint
        pending_exists = bool(pending and pending.exists())
        selected_pending = bool(
            pending and pending_exists and self._select_path(pending, pending_scroll_hint)
        )
        action = pending_selection_action(
            pending,
            pending_exists=pending_exists,
            selected_in_current_directory=self._has_current_selection_in_current_directory(),
            pending_select_succeeded=selected_pending,
        )
        if action == "selected_pending":
            self._pending_selection_path = None
            self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
            return
        if action == "wait_for_pending":
            return
        if action == "keep_current":
            return
        self._pending_selection_path = None
        self._pending_selection_scroll_hint = QAbstractItemView.ScrollHint.EnsureVisible
        self._select_first_row()

    def _has_current_selection_in_current_directory(self) -> bool:
        selection_model = self._active_view().selectionModel()
        if not selection_model:
            return False
        index = selection_model.currentIndex()
        if not index.isValid():
            return False
        return has_selection_path_in_directory(
            Path(self._model.filePath(index)), self._current_path
        )

    def _selection_path_before_deleted_items(self, deleted_paths: list[Path]) -> Path | None:
        view = self._active_view()
        model = view.model()
        selection_model = view.selectionModel()
        if not model or not selection_model:
            return None

        root_index = view.rootIndex()
        selected_rows = {
            index.siblingAtColumn(0).row()
            for index in selection_model.selectedRows() or selection_model.selectedIndexes()
            if index.isValid()
        }
        if not selected_rows:
            return None

        ordered_paths: list[Path] = []
        row_count = model.rowCount(root_index)
        for row in range(row_count):
            index = model.index(row, 0, root_index)
            if index.isValid():
                ordered_paths.append(Path(self._model.filePath(index)))

        deleted = {path.resolve() for path in deleted_paths}
        return deletion_replacement_path(ordered_paths, selected_rows, deleted)

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
            with suppress(TypeError):
                self._bound_selection_model.currentChanged.disconnect(self._handle_current_changed)
            with suppress(TypeError):
                self._bound_selection_model.selectionChanged.disconnect(
                    self._handle_selection_changed
                )
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
    def _update_media_mode(self, directory: Path, *, select_default: bool = True) -> None:
        # should_enable = self._is_media_heavy(directory)
        should_enable = True  # 常にサムネイルモードにする
        if should_enable != self._media_icon_mode:
            self._media_icon_mode = should_enable
            self._apply_media_mode(select_default=select_default)
        elif self._media_icon_mode:
            self._apply_media_mode(select_default=select_default)

    def _apply_media_mode(self, *, select_default: bool = True) -> None:
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
        self._update_view_toggle_button()
        self._connect_selection_signals()
        if select_default:
            self._select_pending_or_first_row()

    def _handle_view_toggle_clicked(self) -> None:
        target = not self._media_icon_mode
        self._manual_media_mode = target
        self._media_icon_mode = target
        self._apply_media_mode()

    def _update_view_toggle_button(self) -> None:
        text, tooltip = media_mode_button_text(self._media_icon_mode)
        self._toggle_view_button.setText(text)
        self._toggle_view_button.setToolTip(tooltip)

    def _calculate_grid_size(self, edge: int) -> QSize:
        fm = self._tile_view.fontMetrics()
        return calculate_grid_size(edge, fm.lineSpacing())

    def _is_media_heavy(self, directory: Path) -> bool:
        return is_media_heavy_directory(
            directory,
            self._model.media_extensions,
            ratio_threshold=self.MEDIA_RATIO_THRESHOLD,
            min_count=self.MEDIA_MIN_COUNT,
            scan_limit=self.MEDIA_SCAN_LIMIT,
        )

    def _handle_selection_changed(
        self, selected: QItemSelection | None = None, deselected: QItemSelection | None = None
    ) -> None:
        self._repaint_selection_delta(selected, deselected)
        self._update_action_states()

    def _repaint_selection_delta(
        self, selected: QItemSelection | None, deselected: QItemSelection | None
    ) -> None:
        view = self._active_view()
        viewport = view.viewport()
        if view is self._tile_view:
            viewport.update()
            return
        for selection in (selected, deselected):
            if selection is None:
                continue
            for index in selection.indexes():
                source = index.siblingAtColumn(0)
                rect = view.visualRect(source)
                if rect.isValid():
                    viewport.update(rect)

    def _emit_status_changed(self, selected_paths: list[Path] | None = None) -> None:
        self.statusChanged.emit(
            browser_status_from_counts(
                self._status_folder_count,
                self._status_file_count,
                selected_paths,
            )
        )

    def _schedule_select_pending_or_first_row(self) -> None:
        self._selection_restore_timer.stop()
        self._selection_restore_timer.start(0)

    def _request_status_item_counts(self, path: Path) -> None:
        self._status_count_generation += 1
        generation = self._status_count_generation
        self._status_folder_count = 0
        self._status_file_count = 0
        self._emit_status_changed(self._selected_paths())
        job = _DirectoryCountJob(path, generation)
        job.signals.counted.connect(self._handle_status_item_counts_ready)
        self._status_count_jobs[generation] = job
        self._status_count_pool.start(job)

    def _handle_status_item_counts_ready(
        self,
        path_text: str,
        generation: int,
        folder_count: int,
        file_count: int,
    ) -> None:
        self._status_count_jobs.pop(generation, None)
        path = Path(path_text)
        if generation != self._status_count_generation or path != self._current_path:
            return
        self._status_folder_count = folder_count
        self._status_file_count = file_count
        self._emit_status_changed(self._selected_paths())

    def _update_status_item_counts(self) -> None:
        self._status_folder_count, self._status_file_count = directory_item_counts(
            self._current_path
        )
