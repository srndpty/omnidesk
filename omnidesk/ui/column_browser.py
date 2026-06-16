"""Finder 風のカラム型ブラウザ。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from PyQt6 import sip
from PyQt6.QtCore import (
    QAbstractAnimation,
    QDir,
    QEasingCurve,
    QModelIndex,
    QPropertyAnimation,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QDesktopServices,
    QKeySequence,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .column_browser_helpers import (
    EMPTY_PLACEHOLDER,
    LOADING_PLACEHOLDER,
    clamp_scroll_maximum,
    column_placeholder_text,
    is_same_or_ancestor_path,
    normalize_directory_key,
    paste_destination,
    viewport_right_to_content_right,
)
from .column_browser_model import _ColumnFileSystemModel
from .column_browser_operations import ColumnBrowserOperationsMixin, _ClipboardPayload
from .column_browser_views import (
    _ColumnListView,
    _DarkColumnView,
)

logger = logging.getLogger(__name__)

__all__ = [
    "EMPTY_PLACEHOLDER",
    "LOADING_PLACEHOLDER",
    "ColumnBrowser",
    "QListView",
    "clamp_scroll_maximum",
    "column_placeholder_text",
    "is_same_or_ancestor_path",
    "normalize_directory_key",
    "paste_destination",
    "viewport_right_to_content_right",
]


class ColumnBrowser(ColumnBrowserOperationsMixin, QWidget):
    """Finder 風のカラムブラウザウィジェット。"""

    currentPathChanged = pyqtSignal(Path)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        enable_local_shortcuts: bool = True,
    ) -> None:
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
        self._reveal_token: object | None = None
        self._settling = False
        self._horizontal_scroll_animation: QPropertyAnimation | None = None
        self._pending_scroll_maximum: int | None = None
        self._previous_horizontal_scroll_value = 0
        self._last_horizontal_scroll_value = 0
        self._previous_horizontal_scroll_maximum = 0
        self._last_horizontal_scroll_maximum = 0
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
        hbar = self._view.horizontalScrollBar()
        hbar.valueChanged.connect(self._handle_horizontal_scroll_value_changed)
        hbar.rangeChanged.connect(self._handle_scroll_range_changed)

        self._path_edit = QLineEdit(self)
        self._path_edit.setClearButtonEnabled(True)
        self._path_edit.returnPressed.connect(self._handle_path_entered)
        self._up_shortcut: QShortcut | None = None
        if enable_local_shortcuts:
            shortcut = QShortcut(QKeySequence("Alt+Up"), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.go_up)
            self._up_shortcut = shortcut

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
        # 古い current/selection とアクティブ列の参照を捨てておく。
        self._view.clear_navigation_state()
        index = self._model.setRootPath(str(target))
        self._view.setRootIndex(index)
        self._connect_selection_signals()
        self._cancel_pending_reveal()
        self._stop_horizontal_scroll_animation()
        self._view.horizontalScrollBar().setValue(0)
        self._single_shot(0, lambda: self._view.horizontalScrollBar().setValue(0))
        self._reset_horizontal_scroll_history()
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

    def _is_alive(self) -> bool:
        return not sip.isdeleted(self) and not sip.isdeleted(self._view)

    def _single_shot(self, msec: int, callback: Callable[[], None]) -> None:
        def run_if_alive() -> None:
            if self._is_alive():
                callback()

        QTimer.singleShot(msec, run_if_alive)

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
            self._cancel_pending_reveal()
            return
        file_info = self._model.fileInfo(current)
        self._current_path = Path(file_info.absoluteFilePath())
        depth = len(self._current_path.parts)
        # フォルダを開くと右端に新しい列が出るので、それが見えるようにする。
        # ファイル選択では列が増えないのでスクロール位置は動かさない。
        if file_info.isDir():
            self._cancel_stale_directory_scans(self._current_path)
            self._view.restore_preview_artifact_constraints()
            if depth < self._last_depth:
                self._cancel_pending_reveal()
                self._restore_horizontal_scroll_before_shallow_animation()
                self._schedule_settle()
            else:
                self._schedule_reveal()
        else:
            self._cancel_pending_reveal()
            leaf_index = QModelIndex(current)
            leaf_path = self._model.filePath(current)
            self._view.suppress_leaf_preview_artifacts(leaf_index)
            self._single_shot(0, lambda: self._suppress_leaf_preview_if_current(leaf_path))
        self._last_depth = depth
        if file_info.isDir():
            self.currentPathChanged.emit(self._current_path)

    def _handle_path_entered(self) -> None:
        entered = self._path_edit.text().strip()
        if not entered:
            return
        self.set_root_path(Path(entered))

    # ------------------------------------------------------------------
    def _suppress_leaf_preview_if_current(self, leaf_path: str) -> None:
        current = self._view.currentIndex()
        if current.isValid() and normalize_directory_key(
            self._model.filePath(current)
        ) == normalize_directory_key(leaf_path):
            self._view.suppress_leaf_preview_artifacts(current)

    def _is_directory_loaded(self, index: QModelIndex) -> bool:
        if not index.isValid():
            return True
        loaded = getattr(self._model, "is_directory_loaded", None)
        key_loaded = normalize_directory_key(self._model.filePath(index)) in self._loaded_dirs
        if callable(loaded):
            return bool(loaded(index)) or key_loaded
        return key_loaded

    def _handle_directory_loaded(self, path: str) -> None:
        self._loaded_dirs.add(normalize_directory_key(path))
        # 読み込みが終わったディレクトリは空になった（プレースホルダが変わる）かも
        # しれないので、列を再描画して「読み込み中…」を「（空のフォルダ）」へ更新する。
        # ここではスクロールには触れない（reveal は range 変化時のみ）。読み込みは
        # レイアウト確定前にも届きうるため、ここで詰めると左へ飛ぶ恐れがある。
        for column in self._view.column_views():
            column.viewport().update()

    def _cancel_stale_directory_scans(self, current: Path) -> None:
        cancel_scans_except = getattr(self._model, "cancel_scans_except", None)
        if not callable(cancel_scans_except):
            return
        allowed = {self._root_path}
        current_path = current
        while current_path != current_path.parent:
            allowed.add(current_path)
            if current_path == self._root_path:
                break
            current_path = current_path.parent
        cancel_scans_except(allowed)

    def _schedule_settle(self) -> None:
        # 余白（デッドスペース）を詰めるだけの settle。reveal はしない。浅い階層へ
        # 戻った時だけ呼ぶことで、縮める方向が常に正しく左への誤スクロールを防ぐ。
        self._single_shot(0, lambda: self._settle_columns(reveal=False))

    def _schedule_reveal(self) -> None:
        self._pending_reveal = True
        token = object()
        self._reveal_token = token
        # QColumnView の列配置は遅延するため、rangeChanged が来ないケースでも少し
        # 待ってから reveal する。rangeChanged が先に消費したら no-op になる。
        self._single_shot(0, lambda: self._single_shot(0, lambda: self._reveal_if_pending(token)))

    def _cancel_pending_reveal(self) -> None:
        self._pending_reveal = False
        self._reveal_token = None

    def _handle_horizontal_scroll_value_changed(self, value: int) -> None:
        if self._horizontal_scroll_animation is not None:
            return
        self._previous_horizontal_scroll_value = self._last_horizontal_scroll_value
        self._last_horizontal_scroll_value = value

    def _stop_horizontal_scroll_animation(self) -> None:
        animation = self._horizontal_scroll_animation
        if animation is None:
            return
        self._horizontal_scroll_animation = None
        self._pending_scroll_maximum = None
        animation.stop()
        animation.deleteLater()

    def _reset_horizontal_scroll_history(self) -> None:
        hbar = self._view.horizontalScrollBar()
        value = hbar.value()
        maximum = hbar.maximum()
        self._previous_horizontal_scroll_value = value
        self._last_horizontal_scroll_value = value
        self._previous_horizontal_scroll_maximum = maximum
        self._last_horizontal_scroll_maximum = maximum

    def _restore_horizontal_scroll_before_shallow_animation(self) -> None:
        hbar = self._view.horizontalScrollBar()
        current = hbar.value()
        start_value = max(
            self._previous_horizontal_scroll_value,
            self._last_horizontal_scroll_value,
            current,
        )
        if start_value <= current:
            logger.debug(
                "Column shallow scroll restore skipped current=%d previous=%d last=%d maximum=%d",
                current,
                self._previous_horizontal_scroll_value,
                self._last_horizontal_scroll_value,
                hbar.maximum(),
            )
            return
        maximum = max(
            self._previous_horizontal_scroll_maximum,
            self._last_horizontal_scroll_maximum,
            hbar.maximum(),
            start_value,
        )
        logger.debug(
            "Column shallow scroll restore current=%d start=%d max=%d previous_max=%d last_max=%d",
            current,
            start_value,
            maximum,
            self._previous_horizontal_scroll_maximum,
            self._last_horizontal_scroll_maximum,
        )
        self._settling = True
        try:
            if hbar.maximum() < maximum:
                hbar.setMaximum(maximum)
            hbar.setValue(start_value)
        finally:
            self._settling = False
        self._previous_horizontal_scroll_value = current
        self._last_horizontal_scroll_value = start_value

    def _reveal_if_pending(self, token: object) -> None:
        if self._reveal_token is not token or not self._pending_reveal:
            return
        self._cancel_pending_reveal()
        self._settle_columns(reveal=True)

    def _handle_scroll_range_changed(self, _minimum: int, maximum: int) -> None:
        self._previous_horizontal_scroll_maximum = self._last_horizontal_scroll_maximum
        self._last_horizontal_scroll_maximum = maximum
        # QColumnView が列の配置を終えてスクロール範囲を更新した時点で発火する。
        # ここで一度 reveal する。ただし QColumnView がこの後さらに値を戻すことが
        # あるため、予約済みの遅延 reveal はキャンセルせず最後にもう一度合わせる。
        self._settle_columns(reveal=self._pending_reveal)

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
            visible_columns = [
                column
                for column in self._view.column_views()
                if column.isVisible() and column.rootIndex().isValid() and column.width() > 0
            ]
            columns = visible_columns
            if reveal:
                reveal_columns = [
                    column
                    for column in self._view.column_views()
                    if column.rootIndex().isValid()
                    and column.width() > 0
                    and self._is_reveal_relevant_column(column)
                ]
                columns = reveal_columns or visible_columns
            hbar = self._view.horizontalScrollBar()
            viewport_right = max((column.x() + column.width() for column in columns), default=0)
            content_right = viewport_right_to_content_right(hbar.value(), viewport_right)
            desired = clamp_scroll_maximum(content_right, self._view.viewport().width())
            if not reveal and hbar.value() > desired:
                logger.debug(
                    "Column shallow scroll animate start=%d target=%d maximum=%d",
                    hbar.value(),
                    desired,
                    hbar.maximum(),
                )
                self._animate_horizontal_scroll_left(desired)
                return
            self._stop_horizontal_scroll_animation()
            if hbar.maximum() > desired or (reveal and hbar.maximum() < desired):
                hbar.setMaximum(desired)
            if reveal:
                hbar.setValue(desired)
        finally:
            self._settling = False

    def _animate_horizontal_scroll_left(self, target_value: int) -> None:
        hbar = self._view.horizontalScrollBar()
        start_value = hbar.value()
        if start_value <= target_value:
            if hbar.maximum() > target_value:
                hbar.setMaximum(target_value)
            return
        self._stop_horizontal_scroll_animation()
        self._pending_scroll_maximum = target_value
        animation = QPropertyAnimation(hbar, b"value", self)
        animation.setStartValue(start_value)
        animation.setEndValue(target_value)
        animation.setDuration(180)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(self._finish_horizontal_scroll_left)
        self._horizontal_scroll_animation = animation
        animation.start(QAbstractAnimation.DeletionPolicy.KeepWhenStopped)

    def _finish_horizontal_scroll_left(self) -> None:
        animation = self._horizontal_scroll_animation
        self._horizontal_scroll_animation = None
        target_value = self._pending_scroll_maximum
        self._pending_scroll_maximum = None
        if animation is not None:
            animation.deleteLater()
        if target_value is None or not self._is_alive():
            return
        hbar = self._view.horizontalScrollBar()
        self._settling = True
        try:
            hbar.setValue(target_value)
            if hbar.maximum() > target_value:
                hbar.setMaximum(target_value)
        finally:
            self._settling = False

    def _is_reveal_relevant_column(self, column: _ColumnListView) -> bool:
        root_path = self._model.filePath(column.rootIndex())
        return is_same_or_ancestor_path(root_path, str(self._current_path))

    def _connect_selection_signals(self) -> None:
        selection_model = self._view.selectionModel()
        if not selection_model:
            return
        with suppress(TypeError):
            selection_model.currentChanged.disconnect(self._handle_selection_changed)
        selection_model.currentChanged.connect(self._handle_selection_changed)
