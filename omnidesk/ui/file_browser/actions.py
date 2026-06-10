"""Action and context menu wiring for the file browser tab."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import QAbstractItemView, QMenu

from ..file_browser_actions import file_action_states


class FileBrowserActionsMixin:
    def _create_actions(self) -> None:
        self._copy_action = QAction("Copy", self)
        self._copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self._copy_action.triggered.connect(self._copy_selected)

        self._cut_action = QAction("Cut", self)
        self._cut_action.setShortcut(QKeySequence.StandardKey.Cut)
        self._cut_action.triggered.connect(self._cut_selected)

        self._paste_action = QAction("Paste", self)
        self._paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        self._paste_action.triggered.connect(self._paste_into_current)

        self._delete_action = QAction("Delete", self)
        self._delete_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        self._delete_action.triggered.connect(self._delete_selected)

        self._rename_action = QAction("Rename", self)
        self._rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        self._rename_action.triggered.connect(self._rename_selected)

        self._new_file_action = QAction("New File", self)
        self._new_file_action.setShortcut(QKeySequence("Ctrl+N"))
        self._new_file_action.triggered.connect(self._create_new_file)

        self._new_folder_action = QAction("New Folder", self)
        self._new_folder_action.setShortcut(QKeySequence("Ctrl+Shift+N"))
        self._new_folder_action.triggered.connect(self._create_new_folder)

        for action in (
            self._rename_action,
            self._copy_action,
            self._cut_action,
            self._paste_action,
            self._delete_action,
            self._new_file_action,
            self._new_folder_action,
        ):
            self.addAction(action)

        self._setup_shortcuts()
        self._update_action_states()

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+A"), self, self._select_all)
        QShortcut(QKeySequence("Alt+D"), self, self._focus_path_edit)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self, self.go_back)
        QShortcut(QKeySequence("Alt+Left"), self, self.go_back)
        QShortcut(QKeySequence("Alt+Right"), self, self.go_forward)

    def _update_action_states(self) -> None:
        paths = self._selected_paths()
        clipboard_ready = isinstance(self._clipboard, dict) and bool(self._clipboard.get("paths"))
        states = file_action_states(
            len(paths),
            clipboard_has_paths=clipboard_ready,
            current_path_exists=self._current_path.exists(),
        )
        self._copy_action.setEnabled(states["copy"])
        self._cut_action.setEnabled(states["cut"])
        self._delete_action.setEnabled(states["delete"])
        self._rename_action.setEnabled(states["rename"])
        self._paste_action.setEnabled(states["paste"])
        self._new_file_action.setEnabled(states["new_file"])
        self._new_folder_action.setEnabled(states["new_folder"])
        self._update_navigation_button_states()
        self._emit_status_changed(paths)

    def _update_navigation_button_states(self) -> None:
        if not hasattr(self, "_back_button") or not hasattr(self, "_forward_button"):
            return
        self._back_button.setEnabled(bool(self._navigation_history))
        self._forward_button.setEnabled(bool(self._forward_history))

    def _select_all(self) -> None:
        view = self._active_view()
        if view:
            view.selectAll()

    def _focus_path_edit(self) -> None:
        self._path_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._path_edit.selectAll()

    def _show_context_menu(self, view: QAbstractItemView, point) -> None:
        index = view.indexAt(point)
        selection_model = view.selectionModel()
        if index.isValid() and selection_model and not selection_model.isSelected(index):
            selection_model.setCurrentIndex(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect,
            )
        self._update_action_states()
        menu = QMenu(self)
        menu.addAction(self._rename_action)
        menu.addSeparator()
        menu.addAction(self._copy_action)
        menu.addAction(self._cut_action)
        menu.addAction(self._paste_action)
        menu.addSeparator()
        menu.addAction(self._delete_action)
        menu.addSeparator()
        menu.addAction(self._new_file_action)
        menu.addAction(self._new_folder_action)
        menu.exec(view.viewport().mapToGlobal(point))
