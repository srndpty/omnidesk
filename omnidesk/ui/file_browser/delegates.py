"""Item delegates used by file browser views."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

from pathlib import Path
from typing import Literal

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeyEvent, QMouseEvent, QPainter, QTextCursor, QTextOption
from PyQt6.QtWidgets import (
    QApplication,
    QLineEdit,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextEdit,
)

_ClipboardVisualMode = Literal["copy", "move"]


def _basename_selection_length(name: str, is_dir: bool) -> int:
    """Return how many leading characters to preselect for an in-place rename.

    Mirrors Windows Explorer: files preselect the stem (everything before the
    final extension), while folders and extension-less names select everything.
    """
    if is_dir:
        return len(name)
    stem_length = len(name) - len(Path(name).suffix)
    if 0 < stem_length < len(name):
        return stem_length
    return len(name)


class _InlineRenameLineEdit(QLineEdit):
    """Line edit used for in-place renames with word-wise double-click drag.

    A plain ``QLineEdit`` extends a double-click selection one character at a
    time while dragging. Windows Explorer (and ``Ctrl+Shift+Arrow``) instead
    grows the selection a whole word at a time, which this widget reproduces.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._word_drag = False
        self._word_anchor_start = 0
        self._word_anchor_end = 0

    # -- shared in-place rename editor interface --------------------------
    def rename_value(self) -> str:
        return self.text()

    def set_rename_value(self, name: str) -> None:
        self.setText(name)

    def select_basename(self, length: int) -> None:
        self.setSelection(0, length)

    # --------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        # A fresh press starts a character-wise gesture until a double-click
        # promotes it to word-wise dragging.
        self._word_drag = False
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin_word_drag()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._word_drag and event.buttons() & Qt.MouseButton.LeftButton:
            self._extend_word_selection(self.cursorPositionAt(event.position().toPoint()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        self._word_drag = False

    def _begin_word_drag(self) -> None:
        if not self.hasSelectedText():
            return
        self._word_drag = True
        self._word_anchor_start = self.selectionStart()
        self._word_anchor_end = self._word_anchor_start + len(self.selectedText())

    def _extend_word_selection(self, cursor_pos: int) -> None:
        text_length = len(self.text())
        cursor_pos = max(0, min(cursor_pos, text_length))
        if cursor_pos >= self._word_anchor_end:
            end = self._word_edge(cursor_pos, forward=True)
            self.setSelection(self._word_anchor_start, end - self._word_anchor_start)
        elif cursor_pos <= self._word_anchor_start:
            start = self._word_edge(cursor_pos, forward=False)
            self.setSelection(self._word_anchor_end, start - self._word_anchor_end)
        else:
            self.setSelection(
                self._word_anchor_start, self._word_anchor_end - self._word_anchor_start
            )

    def _word_edge(self, pos: int, *, forward: bool) -> int:
        """Snap ``pos`` to the nearest word boundary using Qt's word semantics."""
        self.setCursorPosition(pos)
        if forward:
            self.cursorWordForward(False)
        else:
            self.cursorWordBackward(False)
        return self.cursorPosition()


class _InlineRenameTextEdit(QTextEdit):
    """Multi-line in-place rename editor that grows to fit the whole name.

    Used by the icon/tile view. Like Windows Explorer, the box is not confined
    to a single tile: it widens (centred on the item) and grows downwards,
    overflowing neighbouring tiles, so the whole name stays readable and
    editable. Only the viewport bounds clamp it. Word-wise double-click dragging
    is provided natively by ``QTextEdit``.
    """

    committed = pyqtSignal()

    EXTRA_HEIGHT = 2
    HORIZONTAL_PADDING = 8
    VIEWPORT_MARGIN = 4

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._anchor_rect: QRect | None = None
        self._viewport_rect = QRect()
        self._fixed_top: int | None = None
        self.document().contentsChanged.connect(self._auto_resize)

    # -- shared in-place rename editor interface --------------------------
    def rename_value(self) -> str:
        return self.toPlainText()

    def set_rename_value(self, name: str) -> None:
        self.setPlainText(name)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)

    def select_basename(self, length: int) -> None:
        cursor = self.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(length, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    # --------------------------------------------------------------------
    def configure_geometry(self, anchor_rect: QRect, viewport_rect: QRect, text_top: int) -> None:
        """Anchor the editor on ``anchor_rect`` and size it within the viewport.

        ``anchor_rect`` is the tile rect (viewport coordinates); the editor is
        centred horizontally on it but allowed to grow wider and taller, capped
        only by ``viewport_rect``.
        """
        self._anchor_rect = anchor_rect
        self._viewport_rect = viewport_rect
        self._fixed_top = text_top
        self._auto_resize()

    def _auto_resize(self) -> None:
        if self._fixed_top is None or self._anchor_rect is None:
            return
        frame = 2 * self.frameWidth()
        width = self._ideal_width(frame)
        height = self._content_height(width, frame)

        max_bottom = self._viewport_rect.bottom() - self.VIEWPORT_MARGIN
        available = max_bottom - self._fixed_top
        if available > 0 and height > available:
            height = available
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        x = self._centered_left(width)
        self.setGeometry(x, self._fixed_top, width, height)

    def _ideal_width(self, frame: int) -> int:
        """Width that fits the longest line, capped to the viewport."""
        document = self.document()
        document.setTextWidth(-1)
        natural = int(document.idealWidth()) + frame + self.HORIZONTAL_PADDING
        max_width = self._viewport_rect.width() - 2 * self.VIEWPORT_MARGIN
        floor = self._anchor_rect.width()
        return max(floor, min(natural, max(floor, max_width)))

    def _centered_left(self, width: int) -> int:
        center_x = self._anchor_rect.center().x()
        left = center_x - width // 2
        min_left = self._viewport_rect.left() + self.VIEWPORT_MARGIN
        max_left = self._viewport_rect.right() - self.VIEWPORT_MARGIN - width + 1
        if max_left < min_left:
            return min_left
        return max(min_left, min(left, max_left))

    def _content_height(self, width: int, frame: int) -> int:
        document = self.document()
        document.setTextWidth(max(1, width - frame))
        return int(document.size().height()) + frame + self.EXTRA_HEIGHT

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            # Filenames are single-line; Enter commits instead of inserting a
            # newline (QTextEdit would otherwise keep it for multi-line text).
            self.committed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _InlineRenameDelegateMixin:
    """Adds an in-place rename editor that commits through the file browser tab."""

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = _InlineRenameLineEdit(parent)
        editor.setAlignment(self._inline_editor_alignment())
        return editor

    def _inline_editor_alignment(self) -> Qt.AlignmentFlag:
        return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    def setEditorData(self, editor, index):  # noqa: N802
        model = index.model()
        name_index = index.siblingAtColumn(0)
        name = model.fileName(name_index) if hasattr(model, "fileName") else name_index.data()
        name = name or ""
        is_dir = bool(getattr(model, "isDir", None)) and model.isDir(name_index)
        editor.set_rename_value(name)
        editor.select_basename(_basename_selection_length(name, is_dir))

    def setModelData(self, editor, model, index):  # noqa: N802
        tab = getattr(self.parent(), "_tab", None)
        if tab is None or not index.isValid():
            return
        original = Path(model.filePath(index.siblingAtColumn(0)))
        new_name = editor.rename_value()
        # Defer the rename so it runs after the view finishes tearing down the
        # editor; the subsequent refresh would otherwise re-enter the view while
        # it is still committing.
        QTimer.singleShot(0, lambda: tab._apply_rename(original, new_name))


class _DropTargetItemDelegate(_InlineRenameDelegateMixin, QStyledItemDelegate):
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


class _TwoLineTileNameDelegate(_InlineRenameDelegateMixin, QStyledItemDelegate):
    LABEL_LINES = 2
    HORIZONTAL_PADDING = 12
    ICON_TOP_PADDING = 4
    LABEL_TOP_PADDING = 8

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = _InlineRenameTextEdit(parent)
        editor.committed.connect(lambda: self._commit_inline_editor(editor))
        return editor

    def _commit_inline_editor(self, editor) -> None:
        self.commitData.emit(editor)
        self.closeEditor.emit(editor)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        # The painted layout (icon + two text lines) must match the item's hit
        # rect, otherwise clicking the second name line falls into dead space
        # between items and fails to select. The tile view drives sizing through
        # its grid size, so honour it directly when available.
        widget = option.widget
        grid = widget.gridSize() if widget is not None else QSize()
        if grid.isValid() and not grid.isEmpty():
            return grid
        view_option = QStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)
        line_height = view_option.fontMetrics.lineSpacing()
        decoration = view_option.decorationSize
        height = (
            self.ICON_TOP_PADDING
            + decoration.height()
            + 1
            + self.LABEL_TOP_PADDING
            + line_height * self.LABEL_LINES
            + self.LABEL_TOP_PADDING
        )
        return QSize(decoration.width() + self.HORIZONTAL_PADDING, height)

    def updateEditorGeometry(self, editor, option, index):  # noqa: N802
        view_option = QStyleOptionViewItem(option)
        self.initStyleOption(view_option, index)
        _, text_rect = self._tile_rects(view_option)
        viewport = option.widget.viewport() if option.widget is not None else None
        if hasattr(editor, "configure_geometry") and viewport is not None:
            editor.configure_geometry(view_option.rect, viewport.rect(), text_rect.y())
        else:
            editor.setGeometry(
                QRect(
                    view_option.rect.x(),
                    text_rect.y(),
                    view_option.rect.width(),
                    text_rect.height(),
                )
            )

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
