from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTableWidget

from omnidesk.ui.shortcuts_dialog import SHORTCUT_ENTRIES, ShortcutHelpDialog


def test_shortcut_help_dialog_lists_shortcuts(qtbot) -> None:
    dialog = ShortcutHelpDialog()
    qtbot.addWidget(dialog)

    table = dialog.findChild(QTableWidget, "shortcutHelpTable")

    assert isinstance(table, QTableWidget)
    assert table.rowCount() == len(SHORTCUT_ENTRIES)
    assert table.columnCount() == 2
    assert table.item(0, 0).text() == "F1"
    assert table.item(0, 1).text() == "ショートカットキー一覧を表示"
    assert table.wordWrap() is False
    assert table.verticalHeader().defaultSectionSize() == ShortcutHelpDialog.ROW_HEIGHT
    assert dialog.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    assert "alternate-background-color: #292c31" in table.styleSheet()
