"""Keyboard shortcut help dialog."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


@dataclass(frozen=True)
class ShortcutEntry:
    """One keyboard shortcut row shown in the help dialog."""

    shortcut: str
    action: str


SHORTCUT_ENTRIES: tuple[ShortcutEntry, ...] = (
    ShortcutEntry("F1", "ショートカットキー一覧を表示"),
    ShortcutEntry("Ctrl+T", "新しいタブを開く"),
    ShortcutEntry("Ctrl+W / Middle Click", "現在のタブを閉じる"),
    ShortcutEntry("Ctrl+Shift+T", "直近で閉じたタブを復元"),
    ShortcutEntry("Ctrl+Tab / Ctrl+Shift+Tab", "次または前のタブへ移動"),
    ShortcutEntry("Ctrl+Shift+C", "タブビューとカラムビューを切り替え"),
    ShortcutEntry("Backspace / Alt+Left / Alt+Right", "戻るまたは進む履歴へ移動"),
    ShortcutEntry("Alt+D", "アドレスバーへフォーカスし、パスを全選択"),
    ShortcutEntry("Ctrl+A", "すべての項目を選択"),
    ShortcutEntry("Ctrl+C / Ctrl+X / Ctrl+V", "コピー、カット、ペースト"),
    ShortcutEntry("Delete", "選択項目を削除"),
    ShortcutEntry("F2", "選択項目をリネーム"),
    ShortcutEntry("Ctrl+N", "現在のフォルダに新規ファイルを作成"),
    ShortcutEntry("Ctrl+Shift+N", "現在のフォルダに新規フォルダを作成"),
    ShortcutEntry("F5", "表示を更新"),
)


class ShortcutHelpDialog(QDialog):
    """Dialog listing OmniDesk keyboard shortcuts."""

    ROW_HEIGHT = 32

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ショートカットキー一覧")
        self.setModal(False)
        self.resize(620, 460)

        heading = QLabel("ショートカットキー一覧", self)
        heading.setObjectName("shortcutHelpHeading")

        table = QTableWidget(len(SHORTCUT_ENTRIES), 2, self)
        table.setObjectName("shortcutHelpTable")
        table.setHorizontalHeaderLabels(["ショートカット", "操作"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setWordWrap(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setMinimumSectionSize(self.ROW_HEIGHT)
        table.verticalHeader().setDefaultSectionSize(self.ROW_HEIGHT)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(
            """
            QTableWidget {
                background-color: #202226;
                alternate-background-color: #292c31;
                color: #f2f4f8;
                gridline-color: #3a3f46;
                border: 1px solid #3a3f46;
            }
            QTableWidget::item {
                color: #f2f4f8;
                padding: 2px 8px;
            }
            QHeaderView::section {
                background-color: #2d3138;
                color: #f2f4f8;
                border: 0;
                border-right: 1px solid #3a3f46;
                border-bottom: 1px solid #3a3f46;
                padding: 5px 8px;
            }
            """
        )

        for row, entry in enumerate(SHORTCUT_ENTRIES):
            shortcut_item = QTableWidgetItem(entry.shortcut)
            shortcut_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            action_item = QTableWidgetItem(entry.action)
            table.setItem(row, 0, shortcut_item)
            table.setItem(row, 1, action_item)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(heading)
        layout.addWidget(table)
