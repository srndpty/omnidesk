"""Tree and tile views used by the file browser tab."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import Literal, cast

from PyQt6.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    QUrl,
)
from PyQt6.QtGui import QDrag, QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListView,
    QRubberBand,
    QTreeView,
    QWidget,
)

from ..file_browser_drop import (
    drop_action_for_modifiers,
    drop_target_directory,
    local_paths_from_urls,
    should_move_from_drop_action,
)
from ..file_browser_selection import rubber_band_intersecting_rows, rubber_band_target_rows
from .delegates import _DropTargetItemDelegate, _TwoLineTileNameDelegate

logger = logging.getLogger(__name__)
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


class _BaseFileViewMixin:
    """Adds reusable drag-and-drop and context menu behaviours."""

    def _init_file_view(self, tab) -> None:
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
    def __init__(self, tab) -> None:
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


class _FileTileView(_BaseFileViewMixin, QListView):
    LAYOUT_BATCH_SIZE = 128

    def __init__(self, tab) -> None:
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
