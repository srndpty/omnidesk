"""Main window that orchestrates the OmniDesk experience."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QFileDialog, QLabel, QMainWindow, QSizePolicy, QStackedWidget, QToolBar

from ..utils.config import AppSettings, load_settings, save_settings
from ..utils.paths import get_default_start_path
from ..utils.windows_theme import apply_dark_title_bar
from .column_browser import ColumnBrowser
from .file_browser_status import BrowserStatus, browser_status_for, format_browser_details
from .icons import application_icon
from .shortcuts_dialog import ShortcutHelpDialog
from .tab_container import TabContainer


class MainWindow(QMainWindow):
    """Main window that toggles between tab and column browsing."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowIcon(application_icon())
        self.setWindowTitle("OmniDesk")
        self.resize(1200, 720)

        self._settings = AppSettings.from_raw(load_settings())
        name_column_width = self._settings.name_column_width()
        self._status_path = get_default_start_path()
        self._status_summary = BrowserStatus()
        self._status_path_label = QLabel(self)
        self._status_detail_label = QLabel(self)
        self._shortcuts_dialog: ShortcutHelpDialog | None = None

        self._tab_container = TabContainer(self, name_column_width=name_column_width)
        self._tab_container.currentPathChanged.connect(self._update_status_path)
        self._tab_container.statusChanged.connect(self._update_status_summary)
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
        self._setup_status_bar()

        self._restore_initial_state()
        apply_dark_title_bar(self)

    def _restore_initial_state(self) -> None:
        opened = False
        for tab_state in self._settings.session_tab_states():
            candidate = Path(tab_state["path"])
            if not candidate.exists():
                continue
            self._tab_container.open_in_new_tab(candidate, pinned=tab_state["pinned"])
            opened = True
        if opened:
            current = self._tab_container.current_tab()
            active_path = current.current_path() if current else get_default_start_path()
            self._column_browser.set_root_path(active_path)
            if self._settings.view_mode() == "columns":
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

        self._reopen_closed_tab_action = QAction("Reopen Closed Tab", self)
        self._reopen_closed_tab_action.setShortcut(QKeySequence("Ctrl+Shift+T"))
        self._reopen_closed_tab_action.triggered.connect(self._handle_reopen_closed_tab)

        self._open_folder_action = QAction("Open Folder...", self)
        self._open_folder_action.setShortcut(QKeySequence("Ctrl+O"))
        self._open_folder_action.triggered.connect(self._handle_open_folder)

        self._refresh_action = QAction("Reload", self)
        self._refresh_action.setShortcut(QKeySequence(Qt.Key.Key_F5))
        self._refresh_action.triggered.connect(self._handle_refresh)

        self._toggle_view_action = QAction("Switch to Column View", self)
        self._toggle_view_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._toggle_view_action.triggered.connect(self._handle_toggle_view)

        self._next_tab_action = QAction("Next Tab", self)
        self._next_tab_action.setShortcut(QKeySequence("Ctrl+Tab"))
        self._next_tab_action.triggered.connect(self._handle_next_tab)

        self._previous_tab_action = QAction("Previous Tab", self)
        self._previous_tab_action.setShortcut(QKeySequence("Ctrl+Shift+Tab"))
        self._previous_tab_action.triggered.connect(self._handle_previous_tab)

        self._shortcuts_action = QAction("ショートカットキー一覧", self)
        self._shortcuts_action.setShortcut(QKeySequence(Qt.Key.Key_F1))
        self._shortcuts_action.triggered.connect(self._show_shortcuts_dialog)

        for action in (
            self._new_tab_action,
            self._close_tab_action,
            self._reopen_closed_tab_action,
            self._open_folder_action,
            self._refresh_action,
            self._toggle_view_action,
            self._next_tab_action,
            self._previous_tab_action,
            self._shortcuts_action,
        ):
            self.addAction(action)

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar("MainToolbar", self)
        toolbar.setMovable(False)
        toolbar.addAction(self._new_tab_action)
        toolbar.addSeparator()
        toolbar.addAction(self._open_folder_action)
        toolbar.addAction(self._refresh_action)
        toolbar.addSeparator()
        toolbar.addAction(self._toggle_view_action)
        self.addToolBar(toolbar)

    def _setup_status_bar(self) -> None:
        self._status_path_label.setMinimumWidth(0)
        self._status_path_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._status_detail_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._status_detail_label.setSizePolicy(
            QSizePolicy.Policy.Maximum,
            QSizePolicy.Policy.Preferred,
        )
        self.statusBar().addWidget(self._status_path_label, 1)
        self.statusBar().addPermanentWidget(self._status_detail_label, 0)

    # ------------------------------------------------------------------
    def _handle_new_tab(self) -> None:
        current = self._tab_container.current_tab()
        base_path = current.current_path() if current else get_default_start_path()
        self._tab_container.open_in_new_tab(base_path)

    def _handle_close_tab(self) -> None:
        self._tab_container.close_current_tab()

    def _handle_reopen_closed_tab(self) -> None:
        if self._is_tab_mode():
            self._tab_container.reopen_closed_tab()
            self._update_action_state()

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

    def _show_shortcuts_dialog(self) -> None:
        if self._shortcuts_dialog is None:
            self._shortcuts_dialog = ShortcutHelpDialog(self)
            self._shortcuts_dialog.finished.connect(lambda _result: self._clear_shortcuts_dialog())
        self._shortcuts_dialog.show()
        self._shortcuts_dialog.raise_()
        self._shortcuts_dialog.activateWindow()

    def _clear_shortcuts_dialog(self) -> None:
        self._shortcuts_dialog = None

    def _handle_name_column_width_changed(self, width: int) -> None:
        if self._settings.set_name_column_width(width):
            save_settings(self._settings.as_dict())

    # ------------------------------------------------------------------
    def _switch_to_columns(self) -> None:
        target = self._current_active_path()
        self._view_mode = "columns"
        self._column_browser.set_root_path(target)
        self._stack.setCurrentWidget(self._column_browser)
        self._toggle_view_action.setText("Switch to Tab View")
        self._update_action_state()

    def _switch_to_tabs(self) -> None:
        target = self._current_active_path()
        self._view_mode = "tabs"
        self._tab_container.navigate_current_to(target)
        self._stack.setCurrentWidget(self._tab_container)
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
        self._status_path = path
        if self._is_tab_mode():
            self._status_summary = BrowserStatus()
        else:
            self._status_summary = browser_status_for(path)
        self._show_status()
        self.setWindowTitle(f"OmniDesk - {path}")

    def _update_status_summary(self, path: Path, status: object) -> None:
        if not isinstance(status, BrowserStatus):
            return
        self._status_path = path
        self._status_summary = status
        self._show_status()
        self.setWindowTitle(f"OmniDesk - {path}")

    def _show_status(self) -> None:
        path_text = str(self._status_path)
        self._status_path_label.setToolTip(path_text)
        self._status_path_label.setText(self._elided_status_path())
        self._status_detail_label.setText(format_browser_details(self._status_summary))
        self._status_detail_label.setToolTip(self._status_detail_label.text())

    def _elided_status_path(self) -> str:
        path_text = str(self._status_path)
        width = self._status_path_label.width()
        if width <= 0:
            return path_text
        return self._status_path_label.fontMetrics().elidedText(
            path_text,
            Qt.TextElideMode.ElideMiddle,
            width,
        )

    def _update_action_state(self) -> None:
        in_tabs = self._is_tab_mode()
        tab_count = self._tab_container.tab_count()
        self._new_tab_action.setEnabled(in_tabs)
        self._close_tab_action.setEnabled(in_tabs and tab_count > 1)
        self._reopen_closed_tab_action.setEnabled(in_tabs and self._tab_container.has_closed_tabs())
        self._next_tab_action.setEnabled(in_tabs and tab_count > 1)
        self._previous_tab_action.setEnabled(in_tabs and tab_count > 1)

    def _persist_settings(self) -> None:
        self._settings.set_session(
            tabs=[str(path) for path in self._tab_container.tab_paths()],
            pinned_tabs=self._tab_container.tab_pinned_states(),
            view_mode=self._view_mode,
        )
        save_settings(self._settings.as_dict())

    # ------------------------------------------------------------------
    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        if self._is_tab_mode():
            self._tab_container.focus_current()
        else:
            self._column_browser.focus_view()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._show_status()

    def closeEvent(self, event) -> None:  # noqa: N802
        QThreadPool.globalInstance().waitForDone()
        self._persist_settings()
        super().closeEvent(event)
