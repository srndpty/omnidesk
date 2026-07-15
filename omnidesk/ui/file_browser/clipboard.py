"""クリップボード状態コントローラと互換Mixin。"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast

from PyQt6.QtCore import QModelIndex
from PyQt6.QtWidgets import QAbstractItemView

from .sort_model import SortedFileSystemModel
from .views import _BaseFileViewMixin, _FileTileView, _FileTreeView


class _ClipboardPayload(TypedDict):
    paths: list[Path]
    mode: Literal["copy", "move"]


_ClipboardVisualMode = Literal["copy", "move"]


class ClipboardController:
    """コピー／移動対象と表示上の強調状態を管理する。"""

    def __init__(
        self,
        *,
        model: SortedFileSystemModel,
        tree_view: _FileTreeView,
        tile_view: _FileTileView,
        selected_paths: Callable[[], list[Path]],
        repaint_paths: Callable[[set[Path]], None],
        update_action_states: Callable[[], None],
    ) -> None:
        self._model = model
        self._tree_view = tree_view
        self._tile_view = tile_view
        self._selected_paths = selected_paths
        self._repaint_paths = repaint_paths
        self._update_action_states = update_action_states
        self.payload: _ClipboardPayload | None = None
        self.path_set: set[Path] = set()

    def paths_from_indexes(self, indexes: list[QModelIndex]) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for index in indexes:
            if not index.isValid():
                continue
            path = Path(self._model.filePath(index.siblingAtColumn(0)))
            if path not in seen:
                seen.add(path)
                paths.append(path)
        return paths

    def set_payload(self, payload: _ClipboardPayload | None) -> None:
        previous_paths = self.path_set
        self.payload = payload
        self.path_set = self.paths_from_payload(payload)
        self._repaint_paths(previous_paths | self.path_set)
        self._update_action_states()

    def paths_from_payload(self, payload: _ClipboardPayload | None) -> set[Path]:
        if not payload:
            return set()
        return {self.normalise_path(path) for path in payload["paths"]}

    def visual_mode_for_index(self, index: QModelIndex) -> _ClipboardVisualMode | None:
        if not self.payload or not index.isValid():
            return None
        path_text = self._model.filePath(index.siblingAtColumn(0))
        if not path_text or self.normalise_path(Path(path_text)) not in self.path_set:
            return None
        return self.payload["mode"]

    def repaint_paths(self, paths: set[Path]) -> None:
        for path in paths:
            index = self._model.index(str(path))
            if index.isValid():
                self.repaint_index_in_views(index.siblingAtColumn(0))

    def repaint_index_in_views(self, index: QModelIndex) -> None:
        for view in (self._tree_view, self._tile_view):
            if view is self._tree_view:
                rect = cast(_BaseFileViewMixin, view)._drop_target_rect(index)
            else:
                rect = view.visualRect(index)
            if rect.isValid():
                view.viewport().update(rect)

    @staticmethod
    def normalise_path(path: Path) -> Path:
        try:
            return Path(os.path.normcase(os.path.abspath(path)))
        except OSError:
            return path

    def copy_selected(self) -> None:
        paths = self._selected_paths()
        if paths:
            self.set_payload({"paths": paths, "mode": "copy"})

    def cut_selected(self) -> None:
        paths = self._selected_paths()
        if paths:
            self.set_payload({"paths": paths, "mode": "move"})


class FileBrowserClipboardMixin:
    if TYPE_CHECKING:
        _clipboard_controller: ClipboardController

        def _active_view(self) -> QAbstractItemView: ...

    def _paths_from_indexes(self, indexes: list[QModelIndex]) -> list[Path]:
        return self._clipboard_controller.paths_from_indexes(indexes)

    def _set_clipboard(self, payload: _ClipboardPayload | None) -> None:
        self._clipboard_controller.set_payload(payload)

    def _clipboard_paths_from_payload(self, payload: _ClipboardPayload | None) -> set[Path]:
        return self._clipboard_controller.paths_from_payload(payload)

    def _clipboard_visual_mode_for_index(self, index: QModelIndex) -> _ClipboardVisualMode | None:
        return self._clipboard_controller.visual_mode_for_index(index)

    def _repaint_clipboard_paths(self, paths: set[Path]) -> None:
        self._clipboard_controller.repaint_paths(paths)

    def _repaint_index_in_views(self, index: QModelIndex) -> None:
        self._clipboard_controller.repaint_index_in_views(index)

    @staticmethod
    def _normalise_clipboard_path(path: Path) -> Path:
        return ClipboardController.normalise_path(path)

    def _selected_paths(self) -> list[Path]:
        view = self._active_view()
        return cast(_BaseFileViewMixin, view).selected_paths()

    def _copy_selected(self) -> None:
        self._clipboard_controller.copy_selected()

    def _cut_selected(self) -> None:
        self._clipboard_controller.cut_selected()

    @property
    def _clipboard(self) -> _ClipboardPayload | None:
        return self._clipboard_controller.payload

    @_clipboard.setter
    def _clipboard(self, value: _ClipboardPayload | None) -> None:
        self._clipboard_controller.payload = value

    @property
    def _clipboard_path_set(self) -> set[Path]:
        return self._clipboard_controller.path_set

    @_clipboard_path_set.setter
    def _clipboard_path_set(self, value: set[Path]) -> None:
        self._clipboard_controller.path_set = value
