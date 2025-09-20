"""Finder-inspired column based browser."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import (
    QDir,
    QModelIndex,
    QUrl,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QDesktopServices,
    QFileSystemModel,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QColumnView,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
    
)


class ColumnBrowser(QWidget):
    """Finder-inspired column browser widget."""

    currentPathChanged = pyqtSignal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = QFileSystemModel(self)
        self._model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self._model.setResolveSymlinks(True)
        self._model.setReadOnly(True)

        self._view = QColumnView(self)
        self._view.setModel(self._model)
        self._view.setAlternatingRowColors(True)
        self._view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._view.activated.connect(self._handle_activated)

        self._path_edit = QLineEdit(self)
        self._path_edit.setClearButtonEnabled(True)
        self._path_edit.returnPressed.connect(self._handle_path_entered)

        self._up_button = QToolButton(self)
        self._up_button.setText("Up")
        self._up_button.setToolTip("Go to parent directory")
        self._up_button.clicked.connect(self.go_up)

        self._refresh_button = QToolButton(self)
        self._refresh_button.setText("Reload")
        self._refresh_button.setToolTip("Refresh")
        self._refresh_button.clicked.connect(self.refresh)

        bar_layout = QHBoxLayout()
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(6)
        bar_layout.addWidget(self._path_edit, stretch=1)
        bar_layout.addWidget(self._up_button)
        bar_layout.addWidget(self._refresh_button)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)
        root_layout.addLayout(bar_layout)
        root_layout.addWidget(self._view, stretch=1)

        self._current_path = Path.home()
        self._connect_selection_signals()

    # ------------------------------------------------------------------
    def set_root_path(self, path: Path) -> None:
        if not path.exists():
            QMessageBox.warning(self, "Cannot navigate", f"{path} does not exist.")
            return
        target = path if path.is_dir() else path.parent
        self._current_path = target
        self._path_edit.setText(str(target))
        index = self._model.setRootPath(str(target))
        self._view.setRootIndex(index)
        self._connect_selection_signals()
        self.currentPathChanged.emit(target)

    def current_path(self) -> Path:
        return self._current_path

    def go_up(self) -> None:
        parent = self._current_path.parent
        if parent != self._current_path:
            self.set_root_path(parent)

    def refresh(self) -> None:
        index = self._model.index(str(self._current_path))
        self._model.refresh(index)

    def focus_view(self) -> None:
        self._view.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------
    def _handle_activated(self, index: QModelIndex) -> None:
        file_info = self._model.fileInfo(index)
        target = Path(file_info.absoluteFilePath())
        if file_info.isDir():
            self.set_root_path(target)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _handle_selection_changed(self, current: QModelIndex, _: QModelIndex) -> None:
        if not current.isValid():
            return
        file_info = self._model.fileInfo(current)
        self._current_path = Path(file_info.absoluteFilePath())
        if file_info.isDir():
            self.currentPathChanged.emit(self._current_path)

    def _handle_path_entered(self) -> None:
        entered = self._path_edit.text().strip()
        if not entered:
            return
        self.set_root_path(Path(entered))

    def _connect_selection_signals(self) -> None:
        selection_model = self._view.selectionModel()
        if not selection_model:
            return
        try:
            selection_model.currentChanged.disconnect(self._handle_selection_changed)
        except TypeError:
            pass
        selection_model.currentChanged.connect(self._handle_selection_changed)
