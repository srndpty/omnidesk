"""Main window that orchestrates the OmniDesk experience."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QFileDialog, QMainWindow, QStackedWidget, QToolBar

from ..utils.config import load_settings, save_settings
from ..utils.paths import get_default_start_path
from ..utils.windows_theme import apply_dark_title_bar
from .column_browser import ColumnBrowser
from .tab_container import TabContainer


class MainWindow(QMainWindow):
    """Main window that toggles between tab and column browsing."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OmniDesk")
        self.resize(1200, 720)

        self._settings: dict[str, Any] = load_settings()
        file_browser_config = self._settings.get("file_browser", {})
        name_column_width = file_browser_config.get("name_column_width")
        if not isinstance(name_column_width, int) or name_column_width <= 0:
            name_column_width = None

        self._tab_container = TabContainer(self, name_column_width=name_column_width)
        self._tab_container.currentPathChanged.connect(self._update_status_path)
        self._tab_container.tabCountChanged.connect(self._update_action_state)
        self._tab_container.nameColumnWidthChanged.connect(self._handle_name_column_width_changed)

        self._column_browser = ColumnBrowser(self)
        self._column_browser.currentPathChanged.connect(self._update_status_path)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._tab_container)
        self._stack.addWidget(self._column_browser)
        self.setCentralWidget(self._stack)
        self._view_mode = "tabs"

        self._create_actions()
        self._setup_toolbar()

        self.statusBar().showMessage("Starting...")

        self._restore_initial_state()
        apply_dark_title_bar(self)

    def _restore_initial_state(self) -> None:
        session = self._settings.get("session", {}) if isinstance(self._settings, dict) else {}
        tab_entries = session.get("tabs") if isinstance(session, dict) else None
        opened = False
        if isinstance(tab_entries, list):
            for raw in tab_entries:
                try:
                    candidate = Path(raw)
                except Exception:  # pragma: no cover - defensive
                    continue
                if not candidate.exists():
                    continue
                self._tab_container.open_in_new_tab(candidate)
                opened = True
            if opened:
                current = self._tab_container.current_tab()
                active_path = current.current_path() if current else get_default_start_path()
                self._column_browser.set_root_path(active_path)
                if session.get("view_mode") == "columns":
                    self._switch_to_columns()
                else:
                    self._switch_to_tabs()
                self._update_status_path(self._current_active_path())
                self._update_action_state()
                return

        initial_path = get_default_start_path()
        self._tab_container.open_in_new_tab(initial_path)
        self._column_browser.set_root_path(initial_path)
        self._stack.setCurrentWidget(self._tab_container)
        self._update_status_path(initial_path)
        self._update_action_state()

    # ------------------------------------------------------------------
    def _create_actions(self) -> None:
        self._new_tab_action = QAction("New Tab", self)
        self._new_tab_action.setShortcut(QKeySequence("Ctrl+T"))
        self._new_tab_action.triggered.connect(self._handle_new_tab)

        self._close_tab_action = QAction("Close Tab", self)
        self._close_tab_action.setShortcut(QKeySequence("Ctrl+W"))
        self._close_tab_action.triggered.connect(self._handle_close_tab)

        self._open_folder_action = QAction("Open Folder...", self)
        self._open_folder_action.setShortcut(QKeySequence("Ctrl+O"))
        self._open_folder_action.triggered.connect(self._handle_open_folder)

        self._refresh_action = QAction("Reload", self)
        self._refresh_action.setShortcut(QKeySequence(Qt.Key.Key_F5))
        self._refresh_action.triggered.connect(self._handle_refresh)

        self._go_up_action = QAction("Go Up", self)
        self._go_up_action.setShortcut(QKeySequence(Qt.Key.Key_Backspace))
        self._go_up_action.triggered.connect(self._handle_go_up)

        self._toggle_view_action = QAction("Switch to Column View", self)
        self._toggle_view_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._toggle_view_action.triggered.connect(self._handle_toggle_view)

        self._next_tab_action = QAction("Next Tab", self)
        self._next_tab_action.setShortcut(QKeySequence("Ctrl+Tab"))
        self._next_tab_action.triggered.connect(self._handle_next_tab)

        self._previous_tab_action = QAction("Previous Tab", self)
        self._previous_tab_action.setShortcut(QKeySequence("Ctrl+Shift+Tab"))
        self._previous_tab_action.triggered.connect(self._handle_previous_tab)

        for action in (
            self._new_tab_action,
            self._close_tab_action,
            self._open_folder_action,
            self._refresh_action,
            self._go_up_action,
            self._toggle_view_action,
            self._next_tab_action,
            self._previous_tab_action,
        ):
            self.addAction(action)

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar("MainToolbar", self)
        toolbar.setMovable(False)
        toolbar.addAction(self._new_tab_action)
        toolbar.addAction(self._close_tab_action)
        toolbar.addSeparator()
        toolbar.addAction(self._open_folder_action)
        toolbar.addAction(self._go_up_action)
        toolbar.addAction(self._refresh_action)
        toolbar.addSeparator()
        toolbar.addAction(self._toggle_view_action)
        self.addToolBar(toolbar)

    # ------------------------------------------------------------------
    def _handle_new_tab(self) -> None:
        current = self._tab_container.current_tab()
        base_path = current.current_path() if current else get_default_start_path()
        self._tab_container.open_in_new_tab(base_path)

    def _handle_close_tab(self) -> None:
        self._tab_container.close_current_tab()

    def _handle_open_folder(self) -> None:
        start_path = self._current_active_path()
        directory = QFileDialog.getExistingDirectory(self, "Open Folder", str(start_path))
        if not directory:
            return
        target = Path(directory)
        if self._is_tab_mode():
            self._tab_container.navigate_current_to(target)
        else:
            self._column_browser.set_root_path(target)

    def _handle_refresh(self) -> None:
        if self._is_tab_mode():
            self._tab_container.refresh()
        else:
            self._column_browser.refresh()

    def _handle_go_up(self) -> None:
        if self._is_tab_mode():
            self._tab_container.go_up()
        else:
            self._column_browser.go_up()

    def _handle_toggle_view(self) -> None:
        if self._is_tab_mode():
            self._switch_to_columns()
        else:
            self._switch_to_tabs()

    def _handle_next_tab(self) -> None:
        if self._is_tab_mode():
            self._tab_container.select_next_tab()

    def _handle_previous_tab(self) -> None:
        if self._is_tab_mode():
            self._tab_container.select_previous_tab()

    def _handle_name_column_width_changed(self, width: int) -> None:
        if width <= 0:
            return
        file_browser_config = self._settings.setdefault("file_browser", {})
        if file_browser_config.get("name_column_width") == width:
            return
        file_browser_config["name_column_width"] = width
        save_settings(self._settings)

    # ------------------------------------------------------------------
    def _switch_to_columns(self) -> None:
        self._column_browser.set_root_path(self._current_active_path())
        self._stack.setCurrentWidget(self._column_browser)
        self._view_mode = "columns"
        self._toggle_view_action.setText("Switch to Tab View")
        self._update_action_state()

    def _switch_to_tabs(self) -> None:
        self._tab_container.navigate_current_to(self._current_active_path())
        self._stack.setCurrentWidget(self._tab_container)
        self._view_mode = "tabs"
        self._toggle_view_action.setText("Switch to Column View")
        self._update_action_state()

    def _is_tab_mode(self) -> bool:
        return self._view_mode == "tabs"

    def _current_active_path(self) -> Path:
        if self._is_tab_mode():
            current = self._tab_container.current_tab()
            if current:
                return current.current_path()
        return self._column_browser.current_path()

    def _update_status_path(self, path: Path) -> None:
        self.statusBar().showMessage(str(path))
        self.setWindowTitle(f"OmniDesk - {path}")

    def _update_action_state(self) -> None:
        in_tabs = self._is_tab_mode()
        tab_count = self._tab_container.tab_count()
        self._new_tab_action.setEnabled(in_tabs)
        self._close_tab_action.setEnabled(in_tabs and tab_count > 1)
        self._next_tab_action.setEnabled(in_tabs and tab_count > 1)
        self._previous_tab_action.setEnabled(in_tabs and tab_count > 1)

    def _persist_settings(self) -> None:
        session = self._settings.setdefault("session", {})
        if isinstance(session, dict):
            session["tabs"] = [str(path) for path in self._tab_container.tab_paths()]
            session["view_mode"] = self._view_mode
        save_settings(self._settings)

    # ------------------------------------------------------------------
    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        if self._is_tab_mode():
            self._tab_container.focus_current()
        else:
            self._column_browser.focus_view()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._persist_settings()
        super().closeEvent(event)

