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

import logging
import time
from pathlib import Path

from PyQt6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal

from ..file_browser_sort import (
    COLUMN_NAME,
    EntryMeta,
    SortMode,
    prepared_entry,
    prepared_is_before,
)
from ..media_file_system_model import MediaFileSystemModel

logger = logging.getLogger(__name__)

# これを超える並べ替えは体感でひっかかる。再発時に原因を特定できるよう記録する。
SLOW_SORT_WARNING_MS = 200


class SortedFileSystemModel(QSortFilterProxyModel):
    """``MediaFileSystemModel`` を包み、名前順/拡張子順を切り替えられるプロキシ。"""

    directoryLoaded = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sort_mode: SortMode = "name"
        # sort() では常に昇順で親クラスを呼び、昇順/降順は lessThan 内で自前処理する。
        # こうするとフォルダ優先が降順でも崩れない。
        self._descending = False
        # 比較のたびに QFileInfo を作り直すと、大量ファイルのフォルダで GUI スレッドが
        # 固まる（1 回の並べ替えで O(N log N) 回の比較 × 2 個の QFileInfo）。
        # ソース行ごとのメタdataをキャッシュし、実体の生成を要素数ぶんに抑える。
        self._meta_cache: dict[int, EntryMeta] = {}
        # 比較用のソートキーもキャッシュする。列や並べ替え方式が変わると
        # キーの意味が変わるため、その組み合わせを signature として持つ。
        self._key_cache: dict[int, tuple[bool, object]] = {}
        self._key_signature: tuple[int, SortMode] | None = None
        self.setDynamicSortFilter(True)

    # ------------------------------------------------------------------
    # source model wiring
    # ------------------------------------------------------------------
    def setSourceModel(self, source) -> None:  # noqa: N802 - Qt override
        previous = self.sourceModel()
        if isinstance(previous, MediaFileSystemModel):
            previous.directoryLoaded.disconnect(self.directoryLoaded)
            previous.modelReset.disconnect(self._clear_meta_cache)
            previous.rowsAboutToBeRemoved.disconnect(self._handle_source_rows_removed)
            previous.dataChanged.disconnect(self._handle_source_data_changed)
        self._clear_meta_cache()
        super().setSourceModel(source)
        if isinstance(source, MediaFileSystemModel):
            source.directoryLoaded.connect(self.directoryLoaded)
            source.modelReset.connect(self._clear_meta_cache)
            source.rowsAboutToBeRemoved.connect(self._handle_source_rows_removed)
            source.dataChanged.connect(self._handle_source_data_changed)

    # ------------------------------------------------------------------
    # 並べ替え用メタdataのキャッシュ
    # ------------------------------------------------------------------
    def _clear_meta_cache(self) -> None:
        self._meta_cache.clear()
        self._key_cache.clear()

    def _handle_source_rows_removed(self, parent, first: int, last: int) -> None:
        # ノードが消えると internalId が別のエントリへ再利用され得るため、
        # 行単位ではなく全体を捨てる（行の削除はソートに比べて頻度が低い）。
        _ = (parent, first, last)
        self._clear_meta_cache()

    # ``rowsInserted`` は意図的に接続していない。キャッシュのキーである
    # ``internalId()`` は ``QFileSystemModel`` の内部ノードのアドレスで、
    # 生存中のノードのアドレスが別のエントリへ割り当てられることはない。
    # したがって、
    #   * ノードが消える経路（rowsAboutToBeRemoved / modelReset / setRootPath）
    #   * 中身が変わる経路（dataChanged）
    # を押さえておけば、新規行が既存エントリのキャッシュを踏むことはない。
    # 大量ファイルの増分ロード中は rowsInserted が何度も飛ぶため、ここで
    # 毎回クリアすると並べ替えキャッシュがまったく効かなくなる。
    # （追加・削除・リネーム後の整合性は test_file_browser_sort_model.py で固定）

    def _handle_source_data_changed(self, top_left, bottom_right, roles=None) -> None:
        """名前・サイズ・更新日時が変わり得る通知だけキャッシュを捨てる。

        サムネイル完成時の ``DecorationRole`` だけの通知は並べ替えに影響しない。
        これを無視しないと、サムネイル生成のたびにキャッシュが飛んでしまう。
        """
        _ = (top_left, bottom_right)
        if roles and all(role == Qt.ItemDataRole.DecorationRole for role in roles):
            return
        self._clear_meta_cache()

    def _entry_meta(self, source: MediaFileSystemModel, index: QModelIndex) -> EntryMeta:
        key = index.internalId()
        meta = self._meta_cache.get(key)
        if meta is None:
            meta = _build_entry_meta(source, index)
            self._meta_cache[key] = meta
        return meta

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
        started_at = time.monotonic()
        # 並べ替えの実体は lessThan が担うので、親には常に昇順を伝える。
        super().sort(column, Qt.SortOrder.AscendingOrder)
        self._log_if_slow("sort", started_at, column)

    def invalidate(self) -> None:
        started_at = time.monotonic()
        super().invalidate()
        self._log_if_slow("invalidate", started_at, self.sortColumn())

    def _log_if_slow(self, operation: str, started_at: float, column: int) -> None:
        elapsed_ms = round((time.monotonic() - started_at) * 1000)
        if elapsed_ms < SLOW_SORT_WARNING_MS:
            return
        logger.warning(
            "並べ替えに時間がかかっています: operation=%s column=%d rows=%d elapsed_ms=%d",
            operation,
            column,
            self.rowCount(),
            elapsed_ms,
        )

    def sortOrder(self) -> Qt.SortOrder:  # noqa: N802 - Qt-style accessor
        return Qt.SortOrder.DescendingOrder if self._descending else Qt.SortOrder.AscendingOrder

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802 - Qt override
        source = self.sourceModel()
        if not isinstance(source, MediaFileSystemModel):
            return super().lessThan(left, right)
        signature = (left.column(), self._sort_mode)
        if signature != self._key_signature:
            # 列や並べ替え方式が変わるとキーの意味が変わるので作り直す。
            self._key_signature = signature
            self._key_cache.clear()
        return prepared_is_before(
            self._prepared(source, left, signature),
            self._prepared(source, right, signature),
            descending=self._descending,
        )

    def _prepared(
        self,
        source: MediaFileSystemModel,
        index: QModelIndex,
        signature: tuple[int, SortMode],
    ) -> tuple[bool, object]:
        key_id = index.internalId()
        prepared = self._key_cache.get(key_id)
        if prepared is None:
            column, mode = signature
            prepared = prepared_entry(
                self._entry_meta(source, index),
                column=column,
                mode=mode,
            )
            self._key_cache[key_id] = prepared
        return prepared

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
        self._clear_meta_cache()
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

    def shutdown_background_work(self) -> None:
        self._media_source().shutdown_background_work()

    def invalidate_folder_thumbnail_preview(self, path: Path) -> None:
        self._media_source().invalidate_folder_thumbnail_preview(path)

    def forget_failed_thumbnails(self) -> None:
        self._media_source().forget_failed_thumbnails()


def _build_entry_meta(source: MediaFileSystemModel, index: QModelIndex) -> EntryMeta:
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
