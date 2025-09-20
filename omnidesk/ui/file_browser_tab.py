"""File browser widget that powers each tab."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import (
    QDir,
    QItemSelectionModel,
    QModelIndex,
    QUrl,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QDesktopServices, QFileSystemModel, QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)


class FileBrowserTab(QWidget):
    """File browser view based on QFileSystemModel."""

    DEFAULT_NAME_COLUMN_WIDTH = 420

    directoryChanged = pyqtSignal(Path)
    requestOpenInNewTab = pyqtSignal(Path)
    nameColumnWidthChanged = pyqtSignal(int)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        name_column_width: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._model = QFileSystemModel(self)
        self._model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self._model.setResolveSymlinks(True)
        self._model.setReadOnly(True)

        self._view = QTreeView(self)
        self._view.setModel(self._model)
        self._view.setAlternatingRowColors(True)
        self._view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._view.doubleClicked.connect(self._handle_index_activated)
        self._view.activated.connect(self._handle_index_activated)
        self._view.setSortingEnabled(True)
        self._view.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._view.setRootIsDecorated(False)
        self._view.setUniformRowHeights(True)

        header = self._view.header()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        header.setMinimumSectionSize(80)
        header.sectionResized.connect(self._handle_section_resized)
        self._header = header

        self._path_edit = QLineEdit(self)
        self._path_edit.setClearButtonEnabled(True)
        self._path_edit.returnPressed.connect(self._handle_path_entered)

        self._up_button = QToolButton(self)
        self._up_button.setText("Up")
        self._up_button.setToolTip("Go to parent directory (Backspace)")
        self._up_button.clicked.connect(self.go_up)

        self._refresh_button = QToolButton(self)
        self._refresh_button.setText("Reload")
        self._refresh_button.setToolTip("Refresh (F5)")
        self._refresh_button.clicked.connect(self.refresh)

        path_bar_layout = QHBoxLayout()
        path_bar_layout.setContentsMargins(0, 0, 0, 0)
        path_bar_layout.setSpacing(6)
        path_bar_layout.addWidget(self._path_edit, stretch=1)
        path_bar_layout.addWidget(self._up_button)
        path_bar_layout.addWidget(self._refresh_button)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)
        root_layout.addLayout(path_bar_layout)
        root_layout.addWidget(self._view, stretch=1)

        self._current_path = Path.home()
        self._name_column_width = (
            name_column_width
            if name_column_width and name_column_width > 0
            else self.DEFAULT_NAME_COLUMN_WIDTH
        )
        self._configure_header_sections()
        self._apply_name_column_width()

        self._model.directoryLoaded.connect(self._on_directory_loaded)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def navigate_to(self, path: Path) -> None:
        """Display the given directory as the current root."""
        if not path.exists():
            QMessageBox.warning(self, "Cannot navigate", f"{path} does not exist.")
            return
        target = path if path.is_dir() else path.parent
        self._current_path = target
        self._path_edit.setText(str(target))
        root_index = self._model.setRootPath(str(target))
        self._view.setRootIndex(root_index)
        self._connect_selection_signals()
        self._configure_header_sections()
        self._apply_name_column_width()
        self.directoryChanged.emit(target)
        self._select_first_row()

    def current_path(self) -> Path:
        return self._current_path

    def refresh(self) -> None:
        """Refresh the current directory view."""
        self.navigate_to(self._current_path)

    def go_up(self) -> None:
        """Navigate to the parent directory."""
        parent = self._current_path.parent
        if parent != self._current_path:
            self.navigate_to(parent)

    def focus_view(self) -> None:
        self._view.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_name_column_width(self, width: int | None) -> None:
        """Apply a new preferred width to the name column."""
        if not width or width <= 0:
            return
        if width == self._name_column_width:
            return
        self._name_column_width = width
        self._apply_name_column_width()

    def name_column_width(self) -> int:
        return self._name_column_width

    # ------------------------------------------------------------------
    # internal slots
    # ------------------------------------------------------------------
    def _on_directory_loaded(self, _: str) -> None:
        self._select_first_row()
        self._configure_header_sections()
        self._apply_name_column_width()

    def _handle_path_entered(self) -> None:
        entered = self._path_edit.text().strip()
        if not entered:
            return
        target = Path(entered)
        if target.is_file():
            self._open_file(target)
            return
        self.navigate_to(target)

    def _handle_index_activated(self, index: QModelIndex) -> None:
        file_info = self._model.fileInfo(index)
        target = Path(file_info.absoluteFilePath())
        if file_info.isDir():
            self.navigate_to(target)
        else:
            self._open_file(target)

    def _handle_section_resized(self, logical_index: int, _: int, new_size: int) -> None:
        if logical_index != 0:
            return
        if new_size <= 0 or new_size == self._name_column_width:
            return
        self._name_column_width = new_size
        self.nameColumnWidthChanged.emit(new_size)

    # ------------------------------------------------------------------
    # QWidget overrides
    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            selected = self._selected_index_path()
            if selected and selected.is_dir():
                self.requestOpenInNewTab.emit(selected)
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _apply_name_column_width(self) -> None:
        if self._name_column_width > 0:
            self._header.resizeSection(0, self._name_column_width)

    def _configure_header_sections(self) -> None:
        count = self._header.count()
        if count == 0:
            return
        self._header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        for section in range(1, count):
            self._header.setSectionResizeMode(section, QHeaderView.ResizeMode.ResizeToContents)

    def _selected_index_path(self) -> Path | None:
        selection_model = self._view.selectionModel()
        if not selection_model:
            return None
        index = selection_model.currentIndex()
        if not index.isValid():
            return None
        file_info = self._model.fileInfo(index)
        return Path(file_info.absoluteFilePath())

    def _select_first_row(self) -> None:
        if not self._view.model():
            return
        selection_model = self._view.selectionModel()
        root_index = self._view.rootIndex()
        if selection_model and root_index.isValid():
            first_index = self._view.model().index(0, 0, root_index)
            if first_index.isValid():
                selection_model.setCurrentIndex(
                    first_index,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect,
                )

    def _connect_selection_signals(self) -> None:
        selection_model = self._view.selectionModel()
        if not selection_model:
            return
        try:
            selection_model.currentChanged.disconnect(self._handle_current_changed)
        except TypeError:
            pass
        selection_model.currentChanged.connect(self._handle_current_changed)

    def _handle_current_changed(self, current: QModelIndex, _: QModelIndex) -> None:
        file_info = self._model.fileInfo(current)
        if file_info.isDir():
            self.directoryChanged.emit(Path(file_info.absoluteFilePath()))

    def _open_file(self, path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
