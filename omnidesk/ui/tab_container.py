"""Container widget that manages multiple file browser tabs."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QContextMenuEvent,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
    QPainter,
    QWheelEvent,
)
from PyQt6.QtWidgets import (
    QMenu,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .file_browser_tab import FileBrowserTab
from .tab_bar_helpers import local_paths_from_urls, tab_drop_action, wheel_scroll_request

logger = logging.getLogger(__name__)

PINNED_TAB_DATA = "pinned"
PINNED_TAB_ACCENT = QColor("#f59e0b")
DRAG_HOVER_ACTIVATE_MS = 1200


class _PinnedTabBar(QTabBar):
    """Tab bar that marks pinned tabs without consuming tab title width."""

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(PINNED_TAB_ACCENT)
        for index in range(self.count()):
            if self.tabData(index) != PINNED_TAB_DATA:
                continue
            rect = self.tabRect(index)
            accent_rect = rect.adjusted(1, 0, -1, 0)
            accent_rect.setHeight(3)
            painter.drawRect(accent_rect)


class TabContainer(QWidget):
    """Container for multiple FileBrowserTab widgets."""

    currentPathChanged = pyqtSignal(Path)
    tabCountChanged = pyqtSignal(int)
    nameColumnWidthChanged = pyqtSignal(int)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        name_column_width: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._tabs = QTabWidget(self)
        self._tabs.setTabBar(_PinnedTabBar(self._tabs))
        self._closed_tabs: list[tuple[Path, bool]] = []
        self._drag_hover_tab_index: int | None = None
        self._drag_hover_activate_ms = DRAG_HOVER_ACTIVATE_MS
        self._drag_hover_timer = QTimer(self)
        self._drag_hover_timer.setSingleShot(True)
        self._drag_hover_timer.timeout.connect(self._activate_drag_hover_tab)

        # 4. QTabWidget自体の設定を行う
        self._tabs.setDocumentMode(True)
        self._tabs.setMovable(True)
        self._tabs.setTabsClosable(False)
        self._tabs.setUsesScrollButtons(True)  # これは QTabWidget のプロパティ

        # 2. デフォルトのタブバーを取得し、設定を適用する
        default_tab_bar = self._tabs.tabBar()
        default_tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        default_tab_bar.setExpanding(False)

        # 1. 現在のサイズポリシーを取得
        policy = default_tab_bar.sizePolicy()
        # 2. 水平方向のポリシーを「Minimum」に設定
        #    これにより、タブバーは自身のsizeHint（理想サイズ）よりは縮まなくなる
        policy.setHorizontalPolicy(QSizePolicy.Policy.Preferred)
        # 3. 変更したポリシーをタブバーに再設定
        default_tab_bar.setSizePolicy(policy)
        # 5. イベントフィルタは、セットした後のタブバーにインストールする
        bar = self._tabs.tabBar()
        bar.installEventFilter(self)
        bar.setAcceptDrops(True)
        bar.setStyleSheet(
            "QTabBar::tab { font-size: 9pt; padding: 5px 3px 5px 2px; "
            "min-width: 4.3em; max-width: 220px; text-align: left; }"
        )
        # (C) ホイールをタブバーで受ける（フォーカス不要でも届くように）
        bar.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        bar.installEventFilter(self)

        # ★★★ 再構築はここまで ★★★

        self._tabs.tabCloseRequested.connect(self._close_tab)
        self._tabs.currentChanged.connect(self._handle_current_tab_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

        self._name_column_width = (
            name_column_width
            if name_column_width and name_column_width > 0
            else FileBrowserTab.DEFAULT_NAME_COLUMN_WIDTH
        )

        final_tab_bar = self._tabs.tabBar()
        logger.debug(
            "TabBar initialized expanding=%s usesScrollButtons=%s elideMode=%s",
            final_tab_bar.expanding(),
            self._tabs.usesScrollButtons(),
            final_tab_bar.elideMode(),
        )

    # ★★★ このメソッドをまるごとTabContainerクラスに追加 ★★★
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        tab_bar = self._tabs.tabBar()
        if obj is tab_bar:
            # ドラッグが入ってきたら受け入れ
            if event.type() == QEvent.Type.DragEnter:
                drag_event = cast(QDragEnterEvent, event)
                if drag_event.mimeData().hasUrls():
                    self._clear_drag_hover_tab()
                    drag_event.acceptProposedAction()
                    return True
                return False

            # 移動中：どのタブ上かを示し、コピー/移動の種別を決定
            if event.type() == QEvent.Type.DragMove:
                drag_event = cast(QDragMoveEvent, event)
                if drag_event.mimeData().hasUrls():
                    idx = tab_bar.tabAt(drag_event.position().toPoint())
                    if idx != -1:
                        self._schedule_drag_hover_tab(idx)
                        action = tab_drop_action(drag_event.modifiers())
                        drag_event.setDropAction(action)
                        drag_event.accept()
                        return True
                    self._clear_drag_hover_tab()
                return False

            # ドロップ：対象タブのフォルダへ移動（またはコピー）
            if event.type() == QEvent.Type.Drop:
                drop_event = cast(QDropEvent, event)
                self._clear_drag_hover_tab()
                if drop_event.mimeData().hasUrls():
                    idx = tab_bar.tabAt(drop_event.position().toPoint())
                    if idx != -1:
                        target_tab = self._tabs.widget(idx)
                        if isinstance(target_tab, FileBrowserTab):
                            paths = local_paths_from_urls(drop_event.mimeData().urls())
                            move = (
                                tab_drop_action(drop_event.modifiers()) == Qt.DropAction.MoveAction
                            )
                            self._handle_tab_bar_drop(
                                target_index=idx,
                                target_tab=target_tab,
                                paths=paths,
                                move=move,
                            )
                            drop_event.setDropAction(
                                Qt.DropAction.MoveAction if move else Qt.DropAction.CopyAction
                            )
                            drop_event.acceptProposedAction()
                            return True
                return False
            if event.type() == QEvent.Type.DragLeave:
                self._clear_drag_hover_tab()
                return False
            if event.type() == QEvent.Type.Wheel:
                wheel = cast(QWheelEvent, event)
                ad = wheel.angleDelta()
                pd = wheel.pixelDelta()
                request = wheel_scroll_request(ad.x(), ad.y(), pd.x(), pd.y())
                if request is not None:
                    go_left, count = request
                    self._scroll_tabstrip(go_left=go_left, count=count)
                    event.accept()
                    return True

            # ここに他の処理（中クリックでクローズ等）があれば続けてOK
            elif event.type() == QEvent.Type.MouseButtonRelease:
                mouse = cast(QMouseEvent, event)
                if mouse.button() == Qt.MouseButton.MiddleButton:
                    index = tab_bar.tabAt(mouse.position().toPoint())
                    if index >= 0:
                        self._close_tab(index)
                        return True

            elif event.type() == QEvent.Type.ContextMenu:
                context_event = cast(QContextMenuEvent, event)
                index = tab_bar.tabAt(context_event.pos())
                if index >= 0:
                    self._show_tab_context_menu(index, context_event.globalPos())
                    return True

        return super().eventFilter(obj, event)

    def _schedule_drag_hover_tab(self, index: int) -> None:
        if index == self._tabs.currentIndex():
            self._clear_drag_hover_tab()
            return
        if self._drag_hover_tab_index == index and self._drag_hover_timer.isActive():
            return
        self._drag_hover_tab_index = index
        self._drag_hover_timer.start(self._drag_hover_activate_ms)

    def _clear_drag_hover_tab(self) -> None:
        self._drag_hover_tab_index = None
        self._drag_hover_timer.stop()

    def _activate_drag_hover_tab(self) -> None:
        index = self._drag_hover_tab_index
        self._drag_hover_tab_index = None
        if index is None or not 0 <= index < self._tabs.count():
            return
        self._tabs.setCurrentIndex(index)

    def _handle_tab_bar_drop(
        self,
        *,
        target_index: int,
        target_tab: FileBrowserTab,
        paths: list[Path],
        move: bool,
    ) -> None:
        source_tab = self.current_tab()
        select_in_target = target_index == self._tabs.currentIndex()
        source_replacement = (
            source_tab.selection_replacement_for_removed_paths(paths)
            if move and source_tab is not None and source_tab is not target_tab
            else None
        )
        dest_dir = target_tab.current_path()
        select_after = [dest_dir / path.name for path in paths] if move and select_in_target else []
        succeeded = target_tab._handle_external_drop(
            paths,
            dest_dir,
            move,
            select_after=select_after,
        )
        if not move:
            return
        if select_in_target:
            if succeeded:
                target_tab.focus_view()
            return
        if source_tab is not None and source_tab is not target_tab and not select_in_target:
            source_tab.restore_selection_after_removed_paths(paths, source_replacement)

    def _scroll_tabstrip(self, *, go_left: bool, count: int = 1) -> None:
        """内部スクローラーボタンを擬似クリックして帯だけをスクロール"""
        left_btn = cast(
            QToolButton | None,
            self._tabs.findChild(QToolButton, "qt_tabwidget_scroller_left"),
        )
        right_btn = cast(
            QToolButton | None,
            self._tabs.findChild(QToolButton, "qt_tabwidget_scroller_right"),
        )

        # ボタンが見つからない場合のフォールバック（選択タブを動かす）
        if not left_btn or not right_btn:
            idx = self._tabs.currentIndex()
            if go_left:
                self._tabs.setCurrentIndex(max(0, idx - count))
            else:
                self._tabs.setCurrentIndex(min(self._tabs.count() - 1, idx + count))
            return

        target = left_btn if go_left else right_btn
        for _ in range(count):
            target.click()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def open_in_new_tab(self, path: Path, *, pinned: bool = False) -> FileBrowserTab:
        tab = FileBrowserTab(self, name_column_width=self._name_column_width)
        tab.navigate_to(path)
        tab.directoryChanged.connect(self._make_directory_changed_handler(tab))
        tab.requestOpenInNewTab.connect(self._open_requested_path_in_new_tab)
        tab.nameColumnWidthChanged.connect(
            partial(self._handle_name_column_width_changed, source=tab)
        )
        index = self._tabs.addTab(tab, self._label_for(path))
        self._set_tab_pinned(index, pinned=pinned)
        self._tabs.setCurrentIndex(index)
        self.tabCountChanged.emit(self._tabs.count())
        return tab

    def _open_requested_path_in_new_tab(self, path: Path) -> None:
        self.open_in_new_tab(path)

    def close_current_tab(self) -> None:
        if self._tabs.count() <= 1:
            return
        index = self._tabs.currentIndex()
        if index >= 0:
            self._close_tab(index)

    def reopen_closed_tab(self) -> bool:
        if not self._closed_tabs:
            return False
        path, pinned = self._closed_tabs.pop()
        self.open_in_new_tab(path, pinned=pinned)
        return True

    def has_closed_tabs(self) -> bool:
        return bool(self._closed_tabs)

    def tab_count(self) -> int:
        return self._tabs.count()

    def current_tab(self) -> FileBrowserTab | None:
        widget = self._tabs.currentWidget()
        if isinstance(widget, FileBrowserTab):
            return widget
        return None

    def go_up(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.go_up()

    def refresh(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.refresh()

    def focus_current(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.focus_view()

    def navigate_current_to(self, path: Path) -> None:
        tab = self.current_tab()
        if tab:
            tab.navigate_to(path)

    def select_next_tab(self) -> None:
        count = self._tabs.count()
        if count <= 1:
            return
        next_index = (self._tabs.currentIndex() + 1) % count
        self._tabs.setCurrentIndex(next_index)

    def select_previous_tab(self) -> None:
        count = self._tabs.count()
        if count <= 1:
            return
        prev_index = (self._tabs.currentIndex() - 1) % count
        self._tabs.setCurrentIndex(prev_index)

    def tab_paths(self) -> list[Path]:
        paths: list[Path] = []
        for index in range(self._tabs.count()):
            widget = self._tabs.widget(index)
            if isinstance(widget, FileBrowserTab):
                paths.append(widget.current_path())
        return paths

    def tab_pinned_states(self) -> list[bool]:
        return [self.is_tab_pinned(index) for index in range(self._tabs.count())]

    def name_column_width(self) -> int:
        return self._name_column_width

    def set_name_column_width(self, width: int) -> None:
        if width <= 0 or width == self._name_column_width:
            return
        self._name_column_width = width
        self._apply_name_column_width(width)

    def is_tab_pinned(self, index: int) -> bool:
        return (
            0 <= index < self._tabs.count()
            and self._tabs.tabBar().tabData(index) == PINNED_TAB_DATA
        )

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _show_tab_context_menu(self, index: int, global_pos: QPoint) -> None:
        menu = self._create_tab_context_menu(index)
        menu.exec(global_pos)

    def _create_tab_context_menu(self, index: int) -> QMenu:
        menu = QMenu(self)
        pin_action = menu.addAction("Unpin Tab" if self.is_tab_pinned(index) else "Pin Tab")
        pin_action.triggered.connect(partial(self._toggle_tab_pinned, index))
        close_action = menu.addAction("Close Tab")
        close_action.setEnabled(self._can_close_tab(index))
        close_action.triggered.connect(partial(self._close_tab, index))
        return menu

    def _toggle_tab_pinned(self, index: int) -> None:
        if not 0 <= index < self._tabs.count():
            return
        self._set_tab_pinned(index, pinned=not self.is_tab_pinned(index))

    def _set_tab_pinned(self, index: int, *, pinned: bool) -> None:
        if not 0 <= index < self._tabs.count():
            return
        self._tabs.tabBar().setTabData(index, PINNED_TAB_DATA if pinned else None)
        self._tabs.tabBar().update(self._tabs.tabBar().tabRect(index))

    def _can_close_tab(self, index: int) -> bool:
        return (
            0 <= index < self._tabs.count()
            and self._tabs.count() > 1
            and not self.is_tab_pinned(index)
        )

    def _close_tab(self, index: int) -> None:
        if not self._can_close_tab(index):
            return
        widget = self._tabs.widget(index)
        if isinstance(widget, FileBrowserTab):
            self._closed_tabs.append((widget.current_path(), self.is_tab_pinned(index)))
            with suppress(RuntimeError):
                widget.deleteLater()
        self._tabs.removeTab(index)
        self.tabCountChanged.emit(self._tabs.count())
        # self._emit_current_path(self._tabs.currentIndex())

    # def _emit_current_path(self, index: int) -> None:
    #     widget = self._tabs.widget(index)
    #     if isinstance(widget, FileBrowserTab):
    #         self.currentPathChanged.emit(widget.current_path())

    # ★★★ 3. _emit_current_path を _handle_current_tab_changed に統合・置換 ★★★
    def _handle_current_tab_changed(self, index: int) -> None:
        """タブが切り替わったときに呼び出される中央ハンドラ"""
        # すべてのタブをループ
        for i in range(self._tabs.count()):
            widget = self._tabs.widget(i)
            if not isinstance(widget, FileBrowserTab):
                continue

            # 新しくカレントになったタブを activate する
            if i == index:
                widget.activate()
                # 以前 _emit_current_path がやっていた処理もここで行う
                self.currentPathChanged.emit(widget.current_path())
            # それ以外のタブは deactivate する
            else:
                widget.deactivate()

    def _make_directory_changed_handler(self, tab: FileBrowserTab) -> Callable[[Path], None]:
        def handler(path: Path) -> None:
            tab_index = self._tabs.indexOf(tab)
            if tab_index == -1:
                return
            root_path = tab.current_path()
            self._tabs.setTabText(tab_index, self._label_for(root_path))
            if tab_index == self._tabs.currentIndex():
                self.currentPathChanged.emit(path)

        return handler

    def _handle_name_column_width_changed(self, width: int, *, source: FileBrowserTab) -> None:
        if width <= 0 or width == self._name_column_width:
            return
        self._name_column_width = width
        self.nameColumnWidthChanged.emit(width)
        self._apply_name_column_width(width, exclude=source)

    def _apply_name_column_width(
        self, width: int, *, exclude: FileBrowserTab | None = None
    ) -> None:
        for index in range(self._tabs.count()):
            widget = self._tabs.widget(index)
            if not isinstance(widget, FileBrowserTab) or widget is exclude:
                continue
            widget.set_name_column_width(width)

    @staticmethod
    def _label_for(path: Path) -> str:
        label = path.name or path.drive or str(path)
        return label
