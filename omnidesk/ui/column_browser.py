"""Finder 風のカラム型ブラウザ。"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Literal, TypedDict, cast

from PyQt6 import sip
from PyQt6.QtCore import (
    QDir,
    QEvent,
    QModelIndex,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QFileSystemModel,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QColumnView,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .file_operations import delete_paths, perform_copy_or_move

logger = logging.getLogger(__name__)


class _ClipboardPayload(TypedDict):
    paths: list[Path]
    mode: Literal["copy", "move"]


_COLUMN_VIEW_STYLESHEET = """
QColumnView,
QColumnView QListView {
    background-color: #25262a;
    alternate-background-color: #2f3034;
    color: #f2f2f2;
}

QColumnView QListView::item:selected {
    color: #ffffff;
}
"""

_PLACEHOLDER_COLOR = "#9aa0a6"
_ACTIVE_COLUMN_BORDER_COLOR = "#3d6fb4"
LOADING_PLACEHOLDER = "読み込み中…"
EMPTY_PLACEHOLDER = "（空のフォルダ）"


def column_placeholder_text(*, row_count: int, loaded: bool) -> str | None:
    """空の列に重ねて表示する文言を返す。

    読み込み中のディレクトリと、読み込みが終わって中身が無いディレクトリを
    区別し、両者が見た目で見分けられるようにする。
    """
    if row_count > 0:
        return None
    return EMPTY_PLACEHOLDER if loaded else LOADING_PLACEHOLDER


def clamp_scroll_maximum(content_right: int, viewport_width: int) -> int:
    """見えている列だけをちょうど覆う水平スクロール最大値を返す。

    ``content_right`` はコンテンツ座標での「一番右の可視列の右端」。ビューポート
    より広い分はスクロール可能で、それ以外は到達不要なデッドスペース。
    """
    return max(0, content_right - viewport_width)


def viewport_right_to_content_right(scroll_value: int, viewport_right: int) -> int:
    """ビューポート相対の右端座標をコンテンツ座標の右端へ変換する。"""
    return scroll_value + viewport_right


def normalize_directory_key(path: str) -> str:
    """Qt/OS による表記揺れを吸収してディレクトリパスを比較するための正規化キー。"""
    return os.path.normcase(os.path.normpath(path))


def paste_destination(selected: Path) -> Path:
    """選択中アイテムに対して貼り付け先となるディレクトリを返す。

    フォルダへの貼り付けはその中へ、ファイルへの貼り付けは同じ階層（親）へ。
    """
    return selected if selected.is_dir() else selected.parent


class _ColumnFileSystemModel(QFileSystemModel):
    """ディレクトリを常に「子を持つ」と報告するファイルシステムモデル。

    ``QColumnView`` は現在の index が子を持つ場合だけ子の列を作る。すべての
    ディレクトリに子があると報告させることで、空のディレクトリでも列を必ず作り
    （「空のフォルダ」表示を出せる）、一方ファイルは子なしのままにして、ファイル
    選択では列が増えないようにする。
    """

    def hasChildren(self, parent: QModelIndex | None = None) -> bool:  # noqa: N802
        if parent is None:
            parent = QModelIndex()
        if not parent.isValid():
            return super().hasChildren(parent)
        return self.isDir(parent)


class _ColumnListView(QListView):
    """中身が無いときに「読み込み中／空」プレースホルダを重ねて描く1列分のビュー。"""

    def __init__(
        self,
        parent: QWidget | None,
        is_directory_loaded: Callable[[QModelIndex], bool],
        column_view: _DarkColumnView,
    ) -> None:
        super().__init__(parent)
        self._is_directory_loaded = is_directory_loaded
        self._column_view = column_view

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # フォーカスは内側の列にあるため、共有ショートカット（Delete, Ctrl+C/X/V）は
        # 列本来のナビゲーションより先にここで消費する必要がある。
        if self._column_view.handle_shortcut_key(event):
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        model = self.model()
        root = self.rootIndex()
        painter = QPainter(self.viewport())
        try:
            if model is not None and root.isValid():
                text = column_placeholder_text(
                    row_count=model.rowCount(root),
                    loaded=self._is_directory_loaded(root),
                )
                if text:
                    painter.setPen(QColor(_PLACEHOLDER_COLOR))
                    painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, text)
            if self.property("activeColumn") is True:
                painter.setPen(QPen(QColor(_ACTIVE_COLUMN_BORDER_COLOR), 1))
                painter.drawRect(self.viewport().rect().adjusted(0, 0, -1, -1))
        finally:
            painter.end()


class _DarkColumnView(QColumnView):
    """ダークスタイル・キーボードショートカット・独自列を備えたカラムビュー。"""

    deleteRequested = pyqtSignal()
    copyRequested = pyqtSignal()
    cutRequested = pyqtSignal()
    pasteRequested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(_COLUMN_VIEW_STYLESHEET)
        self._is_directory_loaded: Callable[[QModelIndex], bool] = lambda _index: True
        self._active_column: _ColumnListView | None = None

    def set_directory_loaded_predicate(self, predicate: Callable[[QModelIndex], bool]) -> None:
        self._is_directory_loaded = predicate

    def createColumn(self, index: QModelIndex) -> QAbstractItemView:  # noqa: N802
        view = _ColumnListView(self.viewport(), self._is_directory_loaded, self)
        self.initializeColumn(view)
        view.setRootIndex(index)
        # ホイールイベントはカーソル直下のウィジェットに届くため、内側の列のリスト
        # 領域やスクロールバー上での Shift+ホイールもここへ転送する必要がある。
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        view.verticalScrollBar().installEventFilter(self)
        model = self.model()
        if model is not None and model.canFetchMore(index):
            model.fetchMore(index)
        return view

    # -- アクティブ列 --------------------------------------------------
    def active_directory(self) -> Path | None:
        """選択を保持している列が表示しているディレクトリを返す。

        これは現在の index の親、つまり選択中アイテム自体がフォルダかどうかに
        関わらず、貼り付け先となるべきフォルダ。
        """
        index = self.currentIndex()
        if not index.isValid():
            return None
        model = self.model()
        if model is None:
            return None
        path = cast(QFileSystemModel, model).filePath(index.parent())
        return Path(path) if path else None

    def column_views(self) -> list[_ColumnListView]:
        """内側のディレクトリごとの列を、作成順で返す。"""
        return cast("list[_ColumnListView]", self.findChildren(_ColumnListView))

    def update_active_column(self, current: QModelIndex) -> None:
        """``current`` を保持している列をアクティブとして強調表示する。"""
        parent = current.parent() if current.isValid() else QModelIndex()
        active: _ColumnListView | None = None
        if current.isValid():
            for column in self.column_views():
                if column.isVisible() and column.rootIndex() == parent:
                    active = column
                    break
        self._set_active_column(active)

    def clear_active_column(self) -> None:
        """ウィジェットには触れずにアクティブ列の参照だけを捨てる。

        ルートが変わって QColumnView が列を破棄する際に使う。古い列はすでに
        （これから）削除されるので、スタイルを当てようとするとクラッシュする。
        """
        self._active_column = None

    def _set_active_column(self, column: _ColumnListView | None) -> None:
        if column is self._active_column:
            return
        for candidate in (self._active_column, column):
            # 直前のアクティブ列は列の作り直しで破棄され、Python 側のラッパーだけが
            # 宙に浮いていることがある。クラッシュを避けてスキップする。
            if candidate is None or sip.isdeleted(candidate):
                continue
            candidate.setProperty("activeColumn", candidate is column)
            style = candidate.style()
            style.unpolish(candidate)
            style.polish(candidate)
            candidate.viewport().update()
        self._active_column = column

    # -- 入力処理の共有 ------------------------------------------------
    def handle_shift_wheel(self, event: QWheelEvent) -> bool:
        """Shift+ホイールを水平スクロールへ変換する。True で消費。"""
        if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return False
        delta = event.angleDelta().y() or event.angleDelta().x()
        if not delta:
            return False
        hbar = self.horizontalScrollBar()
        hbar.setValue(hbar.value() - delta)
        event.accept()
        return True

    def handle_shortcut_key(self, event: QKeyEvent) -> bool:
        """共有ショートカットに対応するリクエストを発行する。True で消費。

        フォーカスを持つ内側の列と、カラムビュー本体とで、同じキーを1か所に
        集約して処理するために共有している。
        """
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key.Key_Delete and modifiers == Qt.KeyboardModifier.NoModifier:
            self.deleteRequested.emit()
            event.accept()
            return True
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            signal = {
                Qt.Key.Key_C: self.copyRequested,
                Qt.Key.Key_X: self.cutRequested,
                Qt.Key.Key_V: self.pasteRequested,
            }.get(Qt.Key(key))
            if signal is not None:
                signal.emit()
                event.accept()
                return True
        return False

    def eventFilter(self, a0, a1) -> bool:  # noqa: N802
        if (
            a1 is not None
            and a1.type() == QEvent.Type.Wheel
            and self.handle_shift_wheel(cast(QWheelEvent, a1))
        ):
            return True
        return super().eventFilter(a0, cast(QEvent, a1))

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self.handle_shift_wheel(event):
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.handle_shortcut_key(event):
            return
        super().keyPressEvent(event)


class ColumnBrowser(QWidget):
    """Finder 風のカラムブラウザウィジェット。"""

    currentPathChanged = pyqtSignal(Path)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = _ColumnFileSystemModel(self)
        self._model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self._model.setResolveSymlinks(True)
        self._model.setReadOnly(True)
        self._loaded_dirs: set[str] = set()
        self._clipboard: _ClipboardPayload | None = None
        # フォルダを開いた直後の1回だけ、新しい列が見えるようにスクロールするための
        # ワンショットフラグ。貼り付けや再読み込みでは立てないので視点が飛ばない。
        self._pending_reveal = False
        self._settling = False
        # 直前の選択の階層の深さ。浅い方へ移動した時だけ余白を詰める判断に使う。
        self._last_depth = 0
        # 表示中のベースディレクトリ（左端の列）。選択中アイテムとは区別して保持する。
        self._root_path = Path.home()
        self._model.directoryLoaded.connect(self._handle_directory_loaded)

        self._view = _DarkColumnView(self)
        self._view.set_directory_loaded_predicate(self._is_directory_loaded)
        self._view.setModel(self._model)
        self._view.setAlternatingRowColors(True)
        self._view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._view.activated.connect(self._handle_activated)
        self._view.deleteRequested.connect(self._delete_selected)
        self._view.copyRequested.connect(self._copy_selected)
        self._view.cutRequested.connect(self._cut_selected)
        self._view.pasteRequested.connect(self._paste_into_selection)
        # QColumnView は列を遅延配置するため、スクロール範囲が確定するのは
        # スクロールバーの range が変わった時点。新しい列の表示はその時に行う。
        self._view.horizontalScrollBar().rangeChanged.connect(self._handle_scroll_range_changed)

        self._path_edit = QLineEdit(self)
        self._path_edit.setClearButtonEnabled(True)
        self._path_edit.returnPressed.connect(self._handle_path_entered)
        self._up_shortcut: QShortcut | None = None
        if parent is None:
            # MainWindow 配下では同じ Alt+Up を ApplicationShortcut の QAction で
            # 扱う。単体利用時だけローカルショートカットを作り、二重登録を避ける。
            self._up_shortcut = QShortcut(QKeySequence("Alt+Up"), self)
            self._up_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self._up_shortcut.activated.connect(self.go_up)

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
        self._root_path = target
        self._current_path = target
        self._path_edit.setText(str(target))
        # ルートを変えると列が作り直されて既存の列は破棄される。ぶら下がる前に
        # アクティブ列の参照を捨てておく。
        self._view.clear_active_column()
        index = self._model.setRootPath(str(target))
        self._view.setRootIndex(index)
        self._connect_selection_signals()
        self._pending_reveal = True
        self._last_depth = len(target.parts)
        self.currentPathChanged.emit(target)

    def current_path(self) -> Path:
        return self._current_path

    def go_up(self) -> None:
        # 親へ移動する基準は「選択中アイテム」ではなく「表示中のベースディレクトリ」。
        # 選択中フォルダを基準にすると、その親＝今表示中のディレクトリになり、
        # 1回目の Alt+Up が見かけ上なにも動かない不具合になる。
        parent = self._root_path.parent
        if parent != self._root_path:
            self.set_root_path(parent)
            # フォーカスを列ビューに残し、アドレスバーへ移らないようにする。
            self.focus_view()

    def refresh(self) -> None:
        index = self._model.index(str(self._current_path))
        refresh = getattr(self._model, "refresh", None)
        if callable(refresh):
            refresh(index)
            return
        self._view.setRootIndex(self._model.setRootPath(str(self._current_path)))

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
        self._view.update_active_column(current)
        if not current.isValid():
            self._pending_reveal = False
            return
        file_info = self._model.fileInfo(current)
        self._current_path = Path(file_info.absoluteFilePath())
        # フォルダを開くと右端に新しい列が出るので、それが見えるようにする。
        # ファイル選択では列が増えないのでスクロール位置は動かさない。
        self._pending_reveal = file_info.isDir()
        # 浅い階層へ戻った時だけ余白（デッドスペース）を詰める。深い階層へ進む時に
        # 詰めようとすると、まだ新しい列が配置されていない古いジオメトリを基に
        # スクロール最大値を縮めてしまい、左端へ強制スクロールされる不具合になる。
        depth = len(self._current_path.parts)
        if depth < self._last_depth:
            self._schedule_settle()
        self._last_depth = depth
        if file_info.isDir():
            self.currentPathChanged.emit(self._current_path)

    def _handle_path_entered(self) -> None:
        entered = self._path_edit.text().strip()
        if not entered:
            return
        self.set_root_path(Path(entered))

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
        self._pending_reveal = False
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
        # 貼り付け先はアクティブ列のディレクトリ（選択中アイテムの親）。これにより
        # フォルダのコピーは自分自身の中ではなく兄弟として「- Copy」が作られ、別の
        # 列をクリックしてからの貼り付けはその列のフォルダに入る。
        dest = self._view.active_directory() or paste_destination(self._current_path)
        errors = perform_copy_or_move(paths, dest, move=move)
        if errors:
            QMessageBox.warning(self, "Operation issues", "\n".join(errors))
        # 貼り付けでスクロール位置が飛ばないように reveal を抑止する。
        self._pending_reveal = False
        self._refresh_directories({dest} | {path.parent for path in paths})
        if move:
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

    # ------------------------------------------------------------------
    def _is_directory_loaded(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return True
        return normalize_directory_key(self._model.filePath(index)) in self._loaded_dirs

    def _handle_directory_loaded(self, path: str) -> None:
        self._loaded_dirs.add(normalize_directory_key(path))
        # 読み込みが終わったディレクトリは空になった（プレースホルダが変わる）かも
        # しれないので、列を再描画して「読み込み中…」を「（空のフォルダ）」へ更新する。
        # ここではスクロールには触れない（reveal は range 変化時のみ）。読み込みは
        # レイアウト確定前にも届きうるため、ここで詰めると左へ飛ぶ恐れがある。
        for column in self._view.column_views():
            column.viewport().update()

    def _schedule_settle(self) -> None:
        # 余白（デッドスペース）を詰めるだけの settle。reveal はしない。浅い階層へ
        # 戻った時だけ呼ぶことで、縮める方向が常に正しく左への誤スクロールを防ぐ。
        QTimer.singleShot(0, lambda: self._settle_columns(reveal=False))

    def _handle_scroll_range_changed(self, _minimum: int, _maximum: int) -> None:
        # QColumnView が列の配置を終えてスクロール範囲を更新した時点で発火する。
        # ここでのジオメトリは正確なので、新しく開いた列の表示はここで1回だけ行う。
        reveal = self._pending_reveal
        self._pending_reveal = False
        self._settle_columns(reveal=reveal)

    def _settle_columns(self, *, reveal: bool) -> None:
        """余分なスクロール領域を詰め、必要なら開いた列を画面内に表示する。

        QColumnView は列を遅延配置し、スクロール最大値を「過去に表示した最も深い
        パス」のまま残すため、浅いフォルダへ戻ると右側に空のスクロール領域が残る。
        実際に見えている列の右端から範囲を計算して縮める。``reveal`` が真のときだけ、
        開いた列が右端にそろうようにスクロールする（収まる場合は 0＝左端のまま）。
        ``reveal`` をワンショットにし range 変化時のみ適用することで、貼り付けや
        読み込み完了で視点が飛ぶのを防ぐ。
        """
        if self._settling:
            return
        self._settling = True
        try:
            columns = [column for column in self._view.column_views() if column.isVisible()]
            hbar = self._view.horizontalScrollBar()
            viewport_right = max((column.x() + column.width() for column in columns), default=0)
            content_right = viewport_right_to_content_right(hbar.value(), viewport_right)
            desired = clamp_scroll_maximum(content_right, self._view.viewport().width())
            if hbar.maximum() > desired:
                hbar.setMaximum(desired)
            if reveal:
                hbar.setValue(desired)
        finally:
            self._settling = False

    def _connect_selection_signals(self) -> None:
        selection_model = self._view.selectionModel()
        if not selection_model:
            return
        with suppress(TypeError):
            selection_model.currentChanged.disconnect(self._handle_selection_changed)
        selection_model.currentChanged.connect(self._handle_selection_changed)
