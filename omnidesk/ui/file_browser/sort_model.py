"""名前順/拡張子順の並べ替えを担うプロキシモデル。

``QFileSystemModel`` はネイティブには「拡張子順」の並べ替えを持たないため、
:class:`SortedFileSystemModel` を ``MediaFileSystemModel`` の上に被せ、表示順だけを
制御する。プロキシは元モデルとほぼ同じ API を転送するので、タブ側のコントローラは
``self._model`` をそのまま使い続けられる（呼び出し箇所の大規模改修が不要）。

比較ロジック自体は Qt 非依存の :mod:`omnidesk.ui.file_browser_sort` に置き、
ここはインデックスのマッピングとメタdata抽出に徹する。
"""

# pyright: reportAttributeAccessIssue=false, reportIncompatibleMethodOverride=false
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal

from ..file_browser_sort import COLUMN_NAME, EntryMeta, SortMode, entry_is_before
from ..media_file_system_model import MediaFileSystemModel


class SortedFileSystemModel(QSortFilterProxyModel):
    """``MediaFileSystemModel`` を包み、名前順/拡張子順を切り替えられるプロキシ。"""

    directoryLoaded = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sort_mode: SortMode = "name"
        # sort() では常に昇順で親クラスを呼び、昇順/降順は lessThan 内で自前処理する。
        # こうするとフォルダ優先が降順でも崩れない。
        self._descending = False
        self.setDynamicSortFilter(True)

    # ------------------------------------------------------------------
    # source model wiring
    # ------------------------------------------------------------------
    def setSourceModel(self, source) -> None:  # noqa: N802 - Qt override
        previous = self.sourceModel()
        if isinstance(previous, MediaFileSystemModel):
            previous.directoryLoaded.disconnect(self.directoryLoaded)
        super().setSourceModel(source)
        if isinstance(source, MediaFileSystemModel):
            source.directoryLoaded.connect(self.directoryLoaded)

    def _media_source(self) -> MediaFileSystemModel:
        source = self.sourceModel()
        assert isinstance(source, MediaFileSystemModel)
        return source

    # ------------------------------------------------------------------
    # sorting
    # ------------------------------------------------------------------
    def set_sort_mode(self, mode: SortMode) -> None:
        """名前順/拡張子順を切り替えて再ソートする。

        名前順/拡張子順はどちらも名前列の並び順なので、直前にサイズ列や更新日時列で
        並べ替えていても、必ず名前列（列0）へ戻してから再ソートする。
        """
        if mode == self._sort_mode:
            return
        self._sort_mode = mode
        self.sort(COLUMN_NAME, self.sortOrder())
        # 列が既に 0 でモードだけ変わった場合でも確実に再ソートさせる。
        self.invalidate()

    def sort_mode(self) -> SortMode:
        return self._sort_mode

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self._descending = order == Qt.SortOrder.DescendingOrder
        # 並べ替えの実体は lessThan が担うので、親には常に昇順を伝える。
        super().sort(column, Qt.SortOrder.AscendingOrder)

    def sortOrder(self) -> Qt.SortOrder:  # noqa: N802 - Qt-style accessor
        return Qt.SortOrder.DescendingOrder if self._descending else Qt.SortOrder.AscendingOrder

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802 - Qt override
        source = self.sourceModel()
        if not isinstance(source, MediaFileSystemModel):
            return super().lessThan(left, right)
        return entry_is_before(
            _entry_meta(source, left),
            _entry_meta(source, right),
            column=left.column(),
            mode=self._sort_mode,
            descending=self._descending,
        )

    # ------------------------------------------------------------------
    # QFileSystemModel 互換の転送（プロキシのインデックスを元モデルへ橋渡し）
    # ------------------------------------------------------------------
    def index(self, *args):  # type: ignore[override]
        # QFileSystemModel.index(path, column=0) のパス版を透過させる。
        if args and isinstance(args[0], str):
            path = args[0]
            column = args[1] if len(args) > 1 else 0
            return self.mapFromSource(self._media_source().index(path, column))
        return super().index(*args)

    def fileInfo(self, index: QModelIndex):  # noqa: N802 - Qt-style API
        return self._media_source().fileInfo(self.mapToSource(index))

    def filePath(self, index: QModelIndex) -> str:  # noqa: N802 - Qt-style API
        return self._media_source().filePath(self.mapToSource(index))

    def fileName(self, index: QModelIndex) -> str:  # noqa: N802 - Qt-style API
        return self._media_source().fileName(self.mapToSource(index))

    def isDir(self, index: QModelIndex) -> bool:  # noqa: N802 - Qt-style API
        return self._media_source().isDir(self.mapToSource(index))

    def setRootPath(self, path: str) -> QModelIndex:  # noqa: N802 - Qt-style API
        return self.mapFromSource(self._media_source().setRootPath(path))

    def rootPath(self) -> str:  # noqa: N802 - Qt-style API
        return self._media_source().rootPath()

    # ------------------------------------------------------------------
    # MediaFileSystemModel 固有メソッドの転送
    # ------------------------------------------------------------------
    def set_thumbnail_edge(self, edge: int) -> None:
        self._media_source().set_thumbnail_edge(edge)

    @property
    def media_extensions(self) -> set[str]:
        return self._media_source().media_extensions

    def set_visible_thumbnail_targets(
        self,
        indexes: list[QModelIndex],
        *,
        request_limit: int | None = None,
        allow_folder_preview: bool = True,
    ) -> int:
        source_indexes = [self.mapToSource(index) for index in indexes]
        return self._media_source().set_visible_thumbnail_targets(
            source_indexes,
            request_limit=request_limit,
            allow_folder_preview=allow_folder_preview,
        )

    def cancel_background_work(self) -> None:
        self._media_source().cancel_background_work()

    def invalidate_folder_thumbnail_preview(self, path: Path) -> None:
        self._media_source().invalidate_folder_thumbnail_preview(path)

    def forget_failed_thumbnails(self) -> None:
        self._media_source().forget_failed_thumbnails()


def _entry_meta(source: MediaFileSystemModel, index: QModelIndex) -> EntryMeta:
    """元モデルのインデックスから並べ替え用メタdataを作る。"""
    info = source.fileInfo(index)
    modified = info.lastModified()
    mtime = modified.toMSecsSinceEpoch() if modified.isValid() else 0
    return EntryMeta(
        is_dir=info.isDir(),
        name=info.fileName(),
        suffix=info.suffix(),
        size=info.size(),
        mtime=mtime,
    )
