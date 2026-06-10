"""Item delegates used by file browser views."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

from typing import Literal

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

_ClipboardVisualMode = Literal["copy", "move"]


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
