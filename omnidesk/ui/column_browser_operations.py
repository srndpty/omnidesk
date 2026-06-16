"""カラムブラウザのクリップボード・ファイル操作 UI オーケストレーション。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

from PyQt6.QtWidgets import QMessageBox

from .column_browser_helpers import paste_destination
from .file_operations import delete_paths, perform_copy_or_move

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QWidget

    from .column_browser_views import _ColumnFileSystemModel, _DarkColumnView

    # 実体は ``ColumnBrowser(ColumnBrowserOperationsMixin, QWidget)`` で混入される。
    # 型チェック時だけ QWidget を基底に見せ、QMessageBox の parent 引数などに
    # ``self`` を渡せるようにする。実行時は object を基底にして MRO を壊さない。
    _MixinBase = QWidget
else:
    _MixinBase = object


class _ClipboardPayload(TypedDict):
    paths: list[Path]
    mode: Literal["copy", "move"]


class ColumnBrowserOperationsMixin(_MixinBase):
    """選択・削除・コピー/カット/貼り付け・再読み込みを担う mixin。

    ``ColumnBrowser`` 本体が保持する ``_view`` / ``_model`` / ``_clipboard`` /
    ``_current_path`` と、reveal・フォーカス系のメソッドに依存する。
    """

    # ``ColumnBrowser`` 側で実体が用意される属性・メソッド。
    _view: _DarkColumnView
    _model: _ColumnFileSystemModel
    _clipboard: _ClipboardPayload | None
    _current_path: Path

    def _cancel_pending_reveal(self) -> None: ...

    def focus_view(self) -> None: ...

    def _selected_paths(self) -> list[Path]:
        selection_model = self._view.selectionModel()
        if not selection_model:
            return []
        paths: list[Path] = []
        for index in selection_model.selectedIndexes():
            if index.column() != 0:
                continue
            info = self._model.fileInfo(index)
            paths.append(Path(info.absoluteFilePath()))
        return paths

    def _delete_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        if (
            QMessageBox.question(
                self,
                "Move to Trash",
                f"Move {len(paths)} item(s) to Trash?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        errors = delete_paths(paths)
        if errors:
            QMessageBox.warning(self, "Move to Trash failed", "\n".join(errors))
        # 削除でスクロール位置が飛ばないように reveal を抑止する。
        self._cancel_pending_reveal()
        self._refresh_directories({path.parent for path in paths})
        self.focus_view()

    def _copy_selected(self) -> None:
        paths = self._selected_paths()
        if paths:
            self._clipboard = {"paths": paths, "mode": "copy"}

    def _cut_selected(self) -> None:
        paths = self._selected_paths()
        if paths:
            self._clipboard = {"paths": paths, "mode": "move"}

    def _paste_into_selection(self) -> None:
        if not self._clipboard:
            return
        paths = self._clipboard["paths"]
        if not paths:
            return
        move = self._clipboard["mode"] == "move"
        # 貼り付け先は最後にフォーカス/クリックされた列のディレクトリを優先する。
        # 空フォルダ列では選択 item がないため root を使い、未記録なら選択中 item
        # の親へフォールバックする。
        dest = self._view.paste_directory() or paste_destination(self._current_path)
        errors = perform_copy_or_move(paths, dest, move=move)
        if errors:
            QMessageBox.warning(self, "Operation issues", "\n".join(errors))
        # 貼り付けでスクロール位置が飛ばないように reveal を抑止する。
        self._cancel_pending_reveal()
        self._refresh_directories({dest} | {path.parent for path in paths})
        if move and not errors:
            self._clipboard = None
        self.focus_view()

    def _refresh_directories(self, directories: set[Path]) -> None:
        refresh = getattr(self._model, "refresh", None)
        if not callable(refresh):
            return
        for directory in directories:
            index = self._model.index(str(directory))
            if index.isValid():
                refresh(index)
