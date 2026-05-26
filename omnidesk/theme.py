"""Dark theme definition shared across the application."""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from .utils.resources import resource_path


def _stylesheet_resource_url(*parts: str) -> str:
    return resource_path(*parts).as_posix().replace('"', '\\"')


_SCROLLBAR_ARROW_UP = _stylesheet_resource_url("icons", "scrollbar-arrow-up.svg")
_SCROLLBAR_ARROW_DOWN = _stylesheet_resource_url("icons", "scrollbar-arrow-down.svg")
_SCROLLBAR_ARROW_LEFT = _stylesheet_resource_url("icons", "scrollbar-arrow-left.svg")
_SCROLLBAR_ARROW_RIGHT = _stylesheet_resource_url("icons", "scrollbar-arrow-right.svg")


DARK_STYLESHEET = (
    """
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

QMenu::item:disabled {
    color: #6f7680;
    background-color: transparent;
}

QMenu::item:disabled:selected {
    color: #6f7680;
    background-color: transparent;
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

QScrollBar:vertical {
    background: #1e1f22;
    width: 14px;
    margin: 14px 0px 14px 0px;
}

QScrollBar:horizontal {
    background: #1e1f22;
    height: 14px;
    margin: 0px 14px 0px 14px;
}

QScrollBar::handle:vertical {
    background: #5f6368;
    border: 3px solid #1e1f22;
    border-radius: 7px;
    min-height: 36px;
}

QScrollBar::handle:horizontal {
    background: #5f6368;
    border: 3px solid #1e1f22;
    border-radius: 7px;
    min-width: 36px;
}

QScrollBar::handle:hover {
    background: #8a9099;
}

QScrollBar::add-page,
QScrollBar::sub-page {
    background: transparent;
}

QScrollBar::sub-line:vertical {
    background: #1e1f22;
    border: 0px;
    height: 14px;
    subcontrol-origin: margin;
    subcontrol-position: top;
}

QScrollBar::add-line:vertical {
    background: #1e1f22;
    border: 0px;
    height: 14px;
    subcontrol-origin: margin;
    subcontrol-position: bottom;
}

QScrollBar::sub-line:horizontal {
    background: #1e1f22;
    border: 0px;
    width: 14px;
    subcontrol-origin: margin;
    subcontrol-position: left;
}

QScrollBar::add-line:horizontal {
    background: #1e1f22;
    border: 0px;
    width: 14px;
    subcontrol-origin: margin;
    subcontrol-position: right;
}

QScrollBar::sub-line:hover,
QScrollBar::add-line:hover {
    background: #2b2d30;
}

QScrollBar::up-arrow:vertical {
    image: url("__SCROLLBAR_ARROW_UP__");
    width: 10px;
    height: 10px;
}

QScrollBar::down-arrow:vertical {
    image: url("__SCROLLBAR_ARROW_DOWN__");
    width: 10px;
    height: 10px;
}

QScrollBar::left-arrow:horizontal {
    image: url("__SCROLLBAR_ARROW_LEFT__");
    width: 10px;
    height: 10px;
}

QScrollBar::right-arrow:horizontal {
    image: url("__SCROLLBAR_ARROW_RIGHT__");
    width: 10px;
    height: 10px;
}

QSplitter::handle {
    background: #1e1f22;
}
""".replace("__SCROLLBAR_ARROW_UP__", _SCROLLBAR_ARROW_UP)
    .replace("__SCROLLBAR_ARROW_DOWN__", _SCROLLBAR_ARROW_DOWN)
    .replace("__SCROLLBAR_ARROW_LEFT__", _SCROLLBAR_ARROW_LEFT)
    .replace("__SCROLLBAR_ARROW_RIGHT__", _SCROLLBAR_ARROW_RIGHT)
)


def apply_dark_theme(app: QApplication) -> None:
    """Apply the dark theme to the given QApplication instance."""
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLESHEET)
