"""Finder 風カラムブラウザの Qt ビュー部品。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from PyQt6 import sip
from PyQt6.QtCore import (
    QEvent,
    QModelIndex,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFocusEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QColumnView,
    QListView,
    QWidget,
)

from .column_browser_helpers import column_placeholder_text
from .column_browser_model import _ColumnFileSystemModel

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


class _ColumnListView(QListView):
    """中身が無いときに「読み込み中／空」プレースホルダを重ねて描く1列分のビュー。"""

    def __init__(
        self,
        parent: QWidget | None,
        is_directory_loaded: Callable[[QModelIndex], bool],
        directory_error: Callable[[QModelIndex], str | None],
        column_view: _DarkColumnView,
    ) -> None:
        super().__init__(parent)
        self._is_directory_loaded = is_directory_loaded
        self._directory_error = directory_error
        self._column_view = column_view
        # 項目数の多いフォルダ（数万件）でフリーズしないよう、リスト列を仮想化する。
        # ファイル列はアイテム高さが一定なので uniform item sizes が安全に効き、
        # 全件の sizeHint 計算を避けられる。さらに Batched レイアウトで初回配置を
        # 分割し、イベントループをブロックしないようにする。
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.LayoutMode.Batched)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        # フォーカスは内側の列にあるため、共有ショートカット（Delete, Ctrl+C/X/V）は
        # 列本来のナビゲーションより先にここで消費する必要がある。
        self._column_view.set_focused_column_root(self.rootIndex())
        if self._column_view.handle_shortcut_key(event):
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._column_view.set_focused_column_root(self.rootIndex())
        super().focusInEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._column_view.set_focused_column_root(self.rootIndex())
        super().mousePressEvent(event)

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
                    error=self._directory_error(root),
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
        self._directory_error: Callable[[QModelIndex], str | None] = lambda _index: None
        self._active_column: _ColumnListView | None = None
        self._focused_column_root = QModelIndex()

    def set_directory_loaded_predicate(self, predicate: Callable[[QModelIndex], bool]) -> None:
        self._is_directory_loaded = predicate

    def set_directory_error_provider(self, provider: Callable[[QModelIndex], str | None]) -> None:
        self._directory_error = provider

    def createColumn(self, index: QModelIndex) -> QAbstractItemView:  # noqa: N802
        self.restore_preview_artifact_constraints()
        view = _ColumnListView(
            self.viewport(), self._is_directory_loaded, self._directory_error, self
        )
        self.initializeColumn(view)
        view.setRootIndex(index)
        # ホイールイベントはカーソル直下のウィジェットに届くため、内側の列のリスト
        # 領域やスクロールバー上での Shift+ホイールもここへ転送する必要がある。
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)
        view.verticalScrollBar().installEventFilter(self)
        return view

    def updatePreviewWidget(self, index: QModelIndex) -> None:  # noqa: N802
        """Leaf item 用の空 preview 領域を表示しない。

        QColumnView は子を持たない current index に preview widget 用の右側領域を
        用意する。OmniDesk の column view ではファイル選択時に右側へ空カラムを
        出したくないため、既存 preview を隠して標準実装は呼ばない。
        """
        widget = self.previewWidget()
        if widget is not None:
            widget.hide()
            widget.setFixedWidth(0)
        self.suppress_leaf_preview_artifacts(index)

    def suppress_leaf_preview_artifacts(self, current: QModelIndex | None = None) -> None:
        """ファイル選択時に QColumnView が残す空 preview/item view を畳む。"""
        target = current if current is not None else self.currentIndex()
        model = self.model()
        if not target.isValid() or not isinstance(model, _ColumnFileSystemModel):
            return
        if model.isDir(target):
            return
        for view in self._preview_artifact_views():
            view.hide()
            view.setFixedWidth(0)
            view.viewport().hide()
            view.verticalScrollBar().hide()
            view.horizontalScrollBar().hide()

    def restore_preview_artifact_constraints(self) -> None:
        """ファイル preview 抑止で 0 幅にした Qt 内部 view を再利用可能に戻す。"""
        for view in self._preview_artifact_views():
            view.setMinimumWidth(0)
            view.setMaximumWidth(16777215)
            view.show()
            view.viewport().show()
            view.updateGeometry()

    def _preview_artifact_views(self) -> list[QAbstractItemView]:
        views = cast("list[QAbstractItemView]", self.findChildren(QAbstractItemView))
        return [view for view in views if not isinstance(view, _ColumnListView)]

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
        path = cast(_ColumnFileSystemModel, model).filePath(index.parent())
        return Path(path) if path else None

    def set_focused_column_root(self, index: QModelIndex) -> None:
        """最後にフォーカスまたはクリックされた列の root index を記録する。"""
        self._focused_column_root = QModelIndex(index) if index.isValid() else QModelIndex()

    def paste_directory(self) -> Path | None:
        """貼り付け先として使うディレクトリを返す。

        空フォルダ列では選択 item がないため currentIndex() だけでは貼り付け先を
        表せない。最後に操作された列の root を優先し、未記録なら選択中 item の親へ
        フォールバックする。
        """
        model = self.model()
        if model is not None and self._focused_column_root.isValid():
            path = cast(_ColumnFileSystemModel, model).filePath(self._focused_column_root)
            if path:
                return Path(path)
        return self.active_directory()

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
        self._focused_column_root = QModelIndex()

    def clear_navigation_state(self) -> None:
        """ルート変更前に古い current/selection を破棄する。"""
        self.clear_active_column()
        selection_model = self.selectionModel()
        if selection_model is not None:
            selection_model.clear()
        self.setCurrentIndex(QModelIndex())
        for column in self.column_views():
            if sip.isdeleted(column):
                continue
            column_selection_model = column.selectionModel()
            if column_selection_model is not None:
                column_selection_model.clear()
            column.setCurrentIndex(QModelIndex())

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
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copyRequested.emit()
            event.accept()
            return True
        if event.matches(QKeySequence.StandardKey.Cut):
            self.cutRequested.emit()
            event.accept()
            return True
        if event.matches(QKeySequence.StandardKey.Paste):
            self.pasteRequested.emit()
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
