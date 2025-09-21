"""QFileSystemModel variant with asynchronous media thumbnails."""

from __future__ import annotations

from pathlib import Path
from typing import Set

from PyQt6.QtCore import Qt, pyqtSignal, QModelIndex
from PyQt6.QtGui import QIcon, QFileSystemModel
from PyQt6.QtWidgets import QApplication

from ..utils.thumbnail_cache import thumbnail_cache
from .media_icon_provider import MediaThumbnailProvider



class MediaFileSystemModel(QFileSystemModel):
    """Extends QFileSystemModel to provide cached media thumbnails."""

    # thumbnailUpdated = pyqtSignal(QModelIndex)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thumbnail_edge = 96
        self._provider = MediaThumbnailProvider(self)
        self._provider.thumbnailReady.connect(self._handle_thumbnail_ready)
        self._pending: Set[str] = set()
        self._failed: Set[str] = set()

    # ------------------------------------------------------------------
    @property
    def media_extensions(self) -> set[str]:
        return self._provider.media_extensions

    def set_thumbnail_edge(self, edge: int) -> None:
        self._thumbnail_edge = max(16, edge)

    # ------------------------------------------------------------------
    def data(self, index, role):
        if role == Qt.ItemDataRole.DecorationRole and index.isValid():
            file_info = self.fileInfo(index)
            if file_info.isFile():
                path_str = file_info.absoluteFilePath()
                key = self._normalise_key(path_str)
                cached = thumbnail_cache.get(key)
                if cached is not None:
                    return cached
                # ★★★ 変更点: ここでサムネイル生成を自動で開始しない ★★★
                # path = Path(key)
                # suffix = path.suffix.lower()
                # if suffix in self.media_extensions:
                #     self._ensure_thumbnail(path, suffix, key)
        # キャッシュにない場合は、常に super() を呼び、OS標準アイコンを返す
        return super().data(index, role)

    # ★★★ 追加: ビューからサムネイル生成をリクエストするための新しいメソッド ★★★
    def prioritize_thumbnail_requests(self, indexes: list[QModelIndex]) -> None:
        """Given a list of visible indexes, request thumbnails for them."""
        for index in indexes:
            if not index.isValid():
                continue
            file_info = self.fileInfo(index)
            if file_info.isFile():
                path = Path(file_info.absoluteFilePath())
                suffix = path.suffix.lower()
                if suffix in self.media_extensions:
                    # _ensure_thumbnail をここで呼び出す
                    self._ensure_thumbnail(path, suffix)

    # ------------------------------------------------------------------
    def _ensure_thumbnail(self, path: Path, suffix: str, key: str | None = None) -> None:
        norm_key = key or self._normalise_key(path)
        if norm_key in self._pending or norm_key in self._failed:
            print(f"[MediaFileSystemModel] skip existing job for {norm_key}", flush=True)
            return
        if suffix in self._provider.VIDEO_EXTENSIONS and not self._provider.video_supported:
            print(f"[MediaFileSystemModel] video not supported, marking failed: {norm_key}", flush=True)
            self._failed.add(norm_key)
            return
        started = self._provider.request_thumbnail(path, self._thumbnail_edge)
        if started:
            print(f"[MediaFileSystemModel] job started for {norm_key}", flush=True)
            self._pending.add(norm_key)
        else:
            print(f"[MediaFileSystemModel] job not started for {norm_key}", flush=True)

    def _handle_thumbnail_ready(self, path: str, icon: QIcon | None) -> None:
        key = self._normalise_key(path)
        self._pending.discard(key)
        if icon is None or icon.isNull():
            print(f"[MediaFileSystemModel] thumbnail failed for {key}", flush=True)
            self._failed.add(key)
            return
        # print(f"[MediaFileSystemModel] thumbnail stored for {key}", flush=True)
        self._failed.discard(key)
        thumbnail_cache.put(key, icon)
        index = self.index(key)
        if index.isValid():
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
            # self.thumbnailUpdated.emit(index)
            # repaint artifact対策
            # これにより、ビューはアイテムの領域全体を正しくクリアしてから再描画するようになる
            self.headerDataChanged.emit(Qt.Orientation.Vertical, index.row(), index.row())
            # ★★★ ここを追加 ★★★
            # イベントループに溜まっている処理を強制的に実行させる
            # これにより、UIの更新が即座に行われる
            # QApplication.processEvents()
    
    @staticmethod
    def _normalise_key(path: Path | str) -> str:
        candidate = path if isinstance(path, Path) else Path(path)
        try:
            return str(candidate.resolve(strict=False))
        except OSError:
            return str(candidate)
