"""Container widget that manages multiple file browser tabs."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .file_browser_tab import FileBrowserTab


class TabContainer(QWidget):
    """Container for multiple FileBrowserTab widgets."""

    currentPathChanged = pyqtSignal(Path)
    tabCountChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(True)
        self._tabs.setMovable(True)
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.currentChanged.connect(self._emit_current_path)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def open_in_new_tab(self, path: Path) -> FileBrowserTab:
        tab = FileBrowserTab(self)
        tab.navigate_to(path)
        tab.directoryChanged.connect(self._make_directory_changed_handler(tab))
        tab.requestOpenInNewTab.connect(self.open_in_new_tab)
        index = self._tabs.addTab(tab, self._label_for(path))
        self._tabs.setCurrentIndex(index)
        self.tabCountChanged.emit(self._tabs.count())
        return tab

    def close_current_tab(self) -> None:
        if self._tabs.count() <= 1:
            return
        index = self._tabs.currentIndex()
        if index >= 0:
            self._tabs.removeTab(index)
            self.tabCountChanged.emit(self._tabs.count())

    def tab_count(self) -> int:
        return self._tabs.count()

    def current_tab(self) -> FileBrowserTab | None:
        widget = self._tabs.currentWidget()
        if isinstance(widget, FileBrowserTab):
            return widget
        return None

    def go_up(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.go_up()

    def refresh(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.refresh()

    def focus_current(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.focus_view()

    def navigate_current_to(self, path: Path) -> None:
        tab = self.current_tab()
        if tab:
            tab.navigate_to(path)

    def select_next_tab(self) -> None:
        count = self._tabs.count()
        if count <= 1:
            return
        next_index = (self._tabs.currentIndex() + 1) % count
        self._tabs.setCurrentIndex(next_index)

    def select_previous_tab(self) -> None:
        count = self._tabs.count()
        if count <= 1:
            return
        prev_index = (self._tabs.currentIndex() - 1) % count
        self._tabs.setCurrentIndex(prev_index)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _close_tab(self, index: int) -> None:
        if self._tabs.count() <= 1:
            return
        widget = self._tabs.widget(index)
        if isinstance(widget, FileBrowserTab):
            try:
                widget.deleteLater()
            except RuntimeError:
                pass
        self._tabs.removeTab(index)
        self.tabCountChanged.emit(self._tabs.count())
        self._emit_current_path(self._tabs.currentIndex())

    def _emit_current_path(self, index: int) -> None:
        widget = self._tabs.widget(index)
        if isinstance(widget, FileBrowserTab):
            self.currentPathChanged.emit(widget.current_path())

    def _make_directory_changed_handler(self, tab: FileBrowserTab) -> Callable[[Path], None]:
        def handler(path: Path) -> None:
            tab_index = self._tabs.indexOf(tab)
            if tab_index == -1:
                return
            self._tabs.setTabText(tab_index, self._label_for(path))
            if tab_index == self._tabs.currentIndex():
                self.currentPathChanged.emit(path)
        return handler

    @staticmethod
    def _label_for(path: Path) -> str:
        label = path.name or path.drive or str(path)
        return label
