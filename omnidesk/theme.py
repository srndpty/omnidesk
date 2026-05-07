"""Dark theme definition shared across the application."""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

DARK_STYLESHEET = """
QWidget {
    background-color: #1e1f22;
    color: #e8e8e8;
    font-family: "Segoe UI";
    font-size: 10pt;
}

QMenuBar, QMenu {
    background-color: #1e1f22;
    color: #e8e8e8;
}

QMenu::item:selected {
    background-color: #3d7bfd;
}

QTreeView, QColumnView {
    background-color: #25262a;
    alternate-background-color: #2b2d30;
    selection-background-color: #3d7bfd;
    selection-color: #ffffff;
    border: 1px solid #34363c;
    show-decoration-selected: 1;
}

QTreeView::item:selected, QColumnView::item:selected {
    background-color: #3d7bfd;
    color: #ffffff;
}

QTreeView::item:selected:hover, QColumnView::item:selected:hover {
    background-color: #3d7bfd;
    color: #ffffff;
}

QTreeView::item:hover:!selected, QColumnView::item:hover:!selected {
    background-color: #33363b;
}

QLineEdit, QComboBox, QSpinBox {
    background-color: #2b2d30;
    border: 1px solid #3a3d42;
    border-radius: 4px;
    padding: 4px 6px;
    color: #f0f0f0;
}

QToolBar {
    background: #1b1c1f;
    border: 0px;
}

QStatusBar {
    background: #1b1c1f;
    color: #a9adb2;
}

QTabWidget::pane {
    border: 1px solid #34363c;
    background: #1e1f22;
}

QTabBar::tab {
    background: #1e1f22;
    color: #d0d0d0;
    padding: 5px 3px 5px 2px;
    border: 1px solid #34363c;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    min-width: 4.3em;
    font-size: 9pt;
    text-align: left;
}

QTabBar::tab:selected {
    background: #2b2d30;
    color: #ffffff;
}

QScrollBar:vertical, QScrollBar:horizontal {
    background: #1e1f22;
    width: 12px;
    margin: 0px;
}

QScrollBar::handle {
    background: #3d3f45;
    border-radius: 6px;
}

QScrollBar::handle:hover {
    background: #4d6bb3;
}

QSplitter::handle {
    background: #1e1f22;
}
"""


def apply_dark_theme(app: QApplication) -> None:
    """Apply the dark theme to the given QApplication instance."""
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLESHEET)
