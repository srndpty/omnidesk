"""Container widget that manages multiple file browser tabs."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtCore import QObject
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QTabBar, QTabWidget, QVBoxLayout, QWidget, QVBoxLayout, QToolButton, QSizePolicy

from .file_browser_tab import FileBrowserTab
from .tab_bar_helpers import local_paths_from_urls, tab_drop_action, wheel_scroll_request


# class _ClosableTabBar(QTabBar):
#     middleClicked = pyqtSignal(int)

#     def mouseReleaseEvent(self, event) -> None:  # noqa: N802
#         if event.button() == Qt.MouseButton.MiddleButton:
#             index = self.tabAt(event.position().toPoint())
#             if index >= 0:
#                 self.middleClicked.emit(index)
#                 event.accept()
#                 return
#         super().mouseReleaseEvent(event)


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
        
        # 4. QTabWidget自体の設定を行う
        self._tabs.setDocumentMode(True)
        self._tabs.setMovable(True)
        self._tabs.setTabsClosable(False)
        self._tabs.setUsesScrollButtons(True) # これは QTabWidget のプロパティ

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
        print("--- TabBar Final State Check ---")
        print(f"  isExpanding: {final_tab_bar.expanding()}")
        print(f"  usesScrollButtons: {self._tabs.usesScrollButtons()}")
        print(f"  elideMode: {final_tab_bar.elideMode()}")
        print("------------------------------")

    # ★★★ このメソッドをまるごとTabContainerクラスに追加 ★★★
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._tabs.tabBar():
            # ドラッグが入ってきたら受け入れ
            if event.type() == QEvent.Type.DragEnter:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
                return False

            # 移動中：どのタブ上かを示し、コピー/移動の種別を決定
            if event.type() == QEvent.Type.DragMove:
                if event.mimeData().hasUrls():
                    idx = obj.tabAt(event.position().toPoint())
                    if idx != -1:
                        obj.setCurrentIndex(idx)  # 視覚的に対象タブをハイライト
                        action = tab_drop_action(event.modifiers())
                        event.setDropAction(action)
                        event.accept()
                        return True
                return False

            # ドロップ：対象タブのフォルダへ移動（またはコピー）
            if event.type() == QEvent.Type.Drop:
                if event.mimeData().hasUrls():
                    idx = obj.tabAt(event.position().toPoint())
                    if idx != -1:
                        target_tab = self._tabs.widget(idx)
                        if isinstance(target_tab, FileBrowserTab):
                            paths = local_paths_from_urls(event.mimeData().urls())
                            move = tab_drop_action(event.modifiers()) == Qt.DropAction.MoveAction
                            dest_dir = target_tab.current_path()
                            target_tab._handle_external_drop(paths, dest_dir, move)
                            event.setDropAction(Qt.DropAction.MoveAction if move
                                                else Qt.DropAction.CopyAction)
                            event.acceptProposedAction()
                            return True
                return False
            if event.type() == QEvent.Type.Wheel:
                wheel: QWheelEvent = event
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
                mouse: QMouseEvent = event
                if mouse.button() == Qt.MouseButton.MiddleButton:
                    index = self._tabs.tabBar().tabAt(mouse.position().toPoint())
                    if index >= 0:
                        self._close_tab(index)
                        return True

        return super().eventFilter(obj, event)


    def _scroll_tabstrip(self, *, go_left: bool, count: int = 1) -> None:
        """内部スクローラーボタンを擬似クリックして帯だけをスクロール"""
        left_btn  = self._tabs.findChild(QToolButton, "qt_tabwidget_scroller_left")
        right_btn = self._tabs.findChild(QToolButton, "qt_tabwidget_scroller_right")

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
    def open_in_new_tab(self, path: Path) -> FileBrowserTab:
        tab = FileBrowserTab(self, name_column_width=self._name_column_width)
        tab.navigate_to(path)
        tab.directoryChanged.connect(self._make_directory_changed_handler(tab))
        tab.requestOpenInNewTab.connect(self.open_in_new_tab)
        tab.nameColumnWidthChanged.connect(
            partial(self._handle_name_column_width_changed, source=tab)
        )
        index = self._tabs.addTab(tab, self._label_for(path))
        self._tabs.setCurrentIndex(index)
        self.tabCountChanged.emit(self._tabs.count())
        return tab

    def close_current_tab(self) -> None:
        if self._tabs.count() <= 1:
            return
        index = self._tabs.currentIndex()
        if index >= 0:
            self._close_tab(index)

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

    def name_column_width(self) -> int:
        return self._name_column_width

    def set_name_column_width(self, width: int) -> None:
        if width <= 0 or width == self._name_column_width:
            return
        self._name_column_width = width
        self._apply_name_column_width(width)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _close_tab(self, index: int) -> None:
        if self._tabs.count() <= 1:
            return
        widget = self._tabs.widget(index)
        if isinstance(widget, FileBrowserTab):
            try:
                widget.deleteLater()
            except RuntimeError:
                pass
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

    def _apply_name_column_width(self, width: int, *, exclude: FileBrowserTab | None = None) -> None:
        for index in range(self._tabs.count()):
            widget = self._tabs.widget(index)
            if not isinstance(widget, FileBrowserTab) or widget is exclude:
                continue
            widget.set_name_column_width(width)

    @staticmethod
    def _label_for(path: Path) -> str:
        label = path.name or path.drive or str(path)
        return label

