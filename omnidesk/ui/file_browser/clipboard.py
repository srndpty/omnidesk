"""Clipboard state helpers for the file browser tab."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, TypedDict, cast

from PyQt6.QtCore import QModelIndex

from .views import _BaseFileViewMixin


class _ClipboardPayload(TypedDict):
    paths: list[Path]
    mode: Literal["copy", "move"]


_ClipboardVisualMode = Literal["copy", "move"]


class FileBrowserClipboardMixin:
    def _paths_from_indexes(self, indexes: list[QModelIndex]) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for index in indexes:
            if not index.isValid():
                continue
            source = index.siblingAtColumn(0)
            path = Path(self._model.filePath(source))
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
        return paths

    def _set_clipboard(self, payload: _ClipboardPayload | None) -> None:
        previous_paths = self._clipboard_path_set
        self._clipboard = payload
        self._clipboard_path_set = self._clipboard_paths_from_payload(payload)
        self._repaint_clipboard_paths(previous_paths | self._clipboard_path_set)
        self._update_action_states()

    def _clipboard_paths_from_payload(self, payload: _ClipboardPayload | None) -> set[Path]:
        if not payload:
            return set()
        return {self._normalise_clipboard_path(path) for path in payload["paths"]}

    def _clipboard_visual_mode_for_index(self, index: QModelIndex) -> _ClipboardVisualMode | None:
        if not self._clipboard or not index.isValid():
            return None
        source = index.siblingAtColumn(0)
        path_text = self._model.filePath(source)
        if not path_text:
            return None
        if self._normalise_clipboard_path(Path(path_text)) not in self._clipboard_path_set:
            return None
        return self._clipboard["mode"]

    def _repaint_clipboard_paths(self, paths: set[Path]) -> None:
        for path in paths:
            index = self._model.index(str(path))
            if index.isValid():
                self._repaint_index_in_views(index.siblingAtColumn(0))

    def _repaint_index_in_views(self, index: QModelIndex) -> None:
        for view in (self._tree_view, self._tile_view):
            if view is self._tree_view:
                rect = cast(_BaseFileViewMixin, view)._drop_target_rect(index)
            else:
                rect = view.visualRect(index)
            if rect.isValid():
                view.viewport().update(rect)

    @staticmethod
    def _normalise_clipboard_path(path: Path) -> Path:
        try:
            return Path(os.path.normcase(os.path.abspath(path)))
        except OSError:
            return path

    def _selected_paths(self) -> list[Path]:
        view = self._active_view()
        return cast(_BaseFileViewMixin, view).selected_paths()

    def _copy_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        self._set_clipboard({"paths": paths, "mode": "copy"})

    def _cut_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        self._set_clipboard({"paths": paths, "mode": "move"})
