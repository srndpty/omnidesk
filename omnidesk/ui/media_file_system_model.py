"""QFileSystemModel variant with asynchronous media thumbnails."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from PyQt6.QtCore import QMimeData, QModelIndex, QSize, Qt, QThreadPool
from PyQt6.QtGui import QFileSystemModel, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QFileIconProvider

from ..utils.perf import log_perf, perf_debug_enabled, perf_start
from ..utils.thumbnail_cache import file_thumbnail_cache, folder_preview_cache
from .media_icon_provider import MediaThumbnailProvider
from .thumbnail_jobs import CacheLoadJob, CacheSaveJob, CancellationToken, FolderScanJob

logger = logging.getLogger(__name__)


def folder_thumbnail_rect(base_size: QSize, thumb_size: QSize, edge: int) -> tuple[int, int]:
    """Return the top-left point for a folder preview thumbnail overlay."""
    x = (base_size.width() - thumb_size.width()) // 2
    y = (base_size.height() - thumb_size.height()) // 2 - int(edge * 0.05)
    return x, y


class MediaFileSystemModel(QFileSystemModel):
    """Extends QFileSystemModel to provide cached media thumbnails."""

    # thumbnailUpdated = pyqtSignal(QModelIndex)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thumbnail_edge = 96
        self._provider = MediaThumbnailProvider(self)
        self._provider.thumbnailReady.connect(self._handle_thumbnail_ready)
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        self._visible_keys: set[str] = set()
        self._tokens: dict[str, CancellationToken] = {}
        self._generations: dict[str, int] = {}
        self.setReadOnly(False)
        # ★★★ フォルダアイコン取得用のプロバイダを追加 ★★★
        self._icon_provider = QFileIconProvider()
        self._folder_scans: dict[str, FolderScanJob] = {}
        self._scan_pool = QThreadPool.globalInstance()
        self._cache_jobs: dict[str, CacheLoadJob] = {}
        self._debug_thumbnails = os.environ.get("OMNIDESK_THUMB_DEBUG", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._perf_debug = perf_debug_enabled()

    # ------------------------------------------------------------------
    @property
    def media_extensions(self) -> set[str]:
        return self._provider.media_extensions

    def set_thumbnail_edge(self, edge: int) -> None:
        self._thumbnail_edge = max(16, edge)

    def _debug(self, event: str, key: str, detail: object = "") -> None:
        if self._debug_thumbnails:
            logger.debug("[thumb:%s] %s %s", event, key, detail)

    # ------------------------------------------------------------------
    def data(self, index, role):
        if role == Qt.ItemDataRole.DecorationRole and index.isValid() and index.column() == 0:
            file_info = self.fileInfo(index)
            path_str = file_info.absoluteFilePath()
            key = self._normalise_key(path_str)

            if file_info.isFile():
                cached = file_thumbnail_cache.get_memory(key)
                if cached is not None:
                    return cached
            elif file_info.isDir():
                cached = folder_preview_cache.get_memory(key)
                if cached is not None:
                    return cached

        # リクエストのロジックは prioritize_thumbnail_requests に移譲されたので、
        # ここではキャッシュになければ常にデフォルトアイコンを返す
        return super().data(index, role)

    def _new_token(self, key: str) -> CancellationToken:
        generation = self._generations.get(key, 0) + 1
        self._generations[key] = generation
        token = CancellationToken(generation)
        self._tokens[key] = token
        return token

    def _is_current_request(self, key: str, generation: int) -> bool:
        return key in self._visible_keys and self._generations.get(key) == generation

    def _cancel_thumbnail_key(self, key: str) -> None:
        self._debug("cancel", key)
        token = self._tokens.pop(key, None)
        if token is not None:
            token.cancel()
        self._pending.discard(key)
        self._folder_scans.pop(key, None)
        self._cache_jobs.pop(key, None)
        self._provider.cancel_thumbnail(key)

    def clear_visible_thumbnail_targets(self) -> None:
        for key in list(self._visible_keys):
            self._cancel_thumbnail_key(key)
        self._visible_keys.clear()

    def _cache_for_info(self, is_dir: bool):
        return folder_preview_cache if is_dir else file_thumbnail_cache

    def set_visible_thumbnail_targets(
        self, indexes: list[QModelIndex], *, request_limit: int | None = None
    ) -> None:
        """Request thumbnails only for currently visible model indexes."""
        perf = perf_start() if self._perf_debug else 0.0
        ordered: list[tuple[str, Path, bool]] = []
        seen: set[str] = set()
        for index in indexes:
            if not index.isValid():
                continue
            source = index.siblingAtColumn(0)
            file_info = self.fileInfo(source)
            path = Path(file_info.absoluteFilePath())
            key = self._normalise_key(path)
            if key in seen:
                continue
            seen.add(key)
            ordered.append((key, path, file_info.isDir()))

        new_visible = seen
        for key in self._visible_keys - new_visible:
            self._cancel_thumbnail_key(key)
        self._visible_keys = new_visible
        self._debug(
            "visible",
            str(len(new_visible)),
            f"limit={request_limit} pending={len(self._pending)} failed={len(self._failed)}",
        )

        requested = 0
        folder_count = 0
        file_count = 0
        for key, path, is_dir in ordered:
            if is_dir:
                folder_count += 1
            else:
                file_count += 1
            if request_limit is not None and requested >= request_limit:
                break
            if self._request_visible_key(key, path, is_dir):
                requested += 1
        log_perf(
            logger,
            "thumbnail.visible_targets",
            perf,
            enabled=self._perf_debug,
            indexes=len(indexes),
            visible=len(new_visible),
            folders=folder_count,
            files=file_count,
            requested=requested,
            pending=len(self._pending),
            failed=len(self._failed),
            request_limit=request_limit,
        )

    def _request_visible_key(self, key: str, path: Path, is_dir: bool) -> bool:
        if key in self._pending or key in self._failed:
            self._debug("skip", key, "pending" if key in self._pending else "failed")
            return False
        cache = self._cache_for_info(is_dir)
        if cache.get_memory(key) is not None:
            self._debug("memory-hit", key)
            index = self.index(key)
            if index.isValid():
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
            return False

        disk_path = cache.disk_path(key)
        if disk_path.exists():
            self._debug("disk-load", key, disk_path)
            token = self._new_token(key)
            job = CacheLoadJob(key, disk_path, token)
            job.signals.loaded.connect(
                lambda loaded_key, generation, image, is_dir=is_dir: self._handle_cache_loaded(
                    loaded_key,
                    generation,
                    image,
                    is_dir,
                )
            )
            self._cache_jobs[key] = job
            self._pending.add(key)
            self._scan_pool.start(job)
            return True

        if is_dir:
            if self._thumbnail_edge <= 64:
                return False
            self._debug("folder-scan-start", key)
            self._ensure_folder_thumbnail(path)
            return True

        suffix = path.suffix.lower()
        if suffix not in self.media_extensions:
            return False
        self._debug("image-start", key)
        self._ensure_thumbnail(path, suffix, key)
        return True

    def _ensure_folder_thumbnail(self, path: Path) -> None:
        """フォルダのプレビューサムネイル生成をリクエストする"""
        key = self._normalise_key(path)
        if key in self._folder_scans:
            return

        token = self._new_token(key)
        self._pending.add(key)

        job = FolderScanJob(key, path, self.media_extensions, token)
        job.signals.found.connect(self._handle_folder_scan_result)
        self._folder_scans[key] = job
        self._scan_pool.start(job)

    def _handle_folder_scan_result(
        self, key: str, generation: int, image_path: Path | None
    ) -> None:
        self._folder_scans.pop(key, None)
        if not self._is_current_request(key, generation):
            self._debug("folder-stale", key, generation)
            return

        if image_path:
            self._debug("folder-found", key, image_path)
            started = self._provider.request_thumbnail(
                image_path,
                self._thumbnail_edge,
                result_key=key,
                token=self._tokens.get(key),
            )
            if not started:
                self._pending.discard(key)
                self._failed.add(key)
                self._debug("folder-image-not-started", key, image_path)
            return

        self._pending.discard(key)
        self._failed.add(key)
        self._debug("folder-none", key)

    # ★★★ 追加: ビューからサムネイル生成をリクエストするための新しいメソッド ★★★
    def prioritize_thumbnail_requests(self, indexes: list[QModelIndex]) -> None:
        """Given a list of visible indexes, request thumbnails for them."""
        self.set_visible_thumbnail_targets(indexes)

    def get_path_list(self, indexes: list[QModelIndex]) -> list[str]:
        """Given a list of indexes, return their absolute file paths."""
        paths = []
        for index in indexes:
            if index.isValid():
                file_info = self.fileInfo(index)
                paths.append(file_info.absoluteFilePath())
        return paths

    # ------------------------------------------------------------------
    def _ensure_thumbnail(self, path: Path, suffix: str, key: str | None = None) -> None:
        norm_key = key or self._normalise_key(path)

        if file_thumbnail_cache.get_memory(norm_key) is not None:
            idx = self.index(norm_key)
            if idx.isValid():
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
            return

        if norm_key in self._pending or norm_key in self._failed:
            # print(f"[MediaFileSystemModel] skip existing job for {norm_key}", flush=True)
            return
        if suffix in self._provider.VIDEO_EXTENSIONS and not self._provider.video_supported:
            # print(f"[MediaFileSystemModel] video not supported, marking failed: {norm_key}", flush=True)
            self._failed.add(norm_key)
            return
        token = self._tokens.get(norm_key) or self._new_token(norm_key)
        started = self._provider.request_thumbnail(
            path,
            self._thumbnail_edge,
            result_key=norm_key,
            token=token,
        )
        if started:
            # print(f"[MediaFileSystemModel] job started for {norm_key}", flush=True)
            self._pending.add(norm_key)
        else:
            logger.warning("Thumbnail job not started for %s", norm_key)
            self._debug("image-not-started", norm_key)

    def _handle_cache_loaded(self, key: str, generation: int, image: object, is_dir: bool) -> None:
        if not self._is_current_request(key, generation):
            self._debug("cache-stale", key, generation)
            return
        self._cache_jobs.pop(key, None)
        self._pending.discard(key)
        self._tokens.pop(key, None)
        qimage: QImage | None = image if isinstance(image, QImage) else None
        if qimage is None or qimage.isNull():
            self._debug("cache-miss", key)
            path = Path(key)
            if is_dir:
                self._ensure_folder_thumbnail(path)
            else:
                self._ensure_thumbnail(path, path.suffix.lower(), key)
            return
        pixmap = QPixmap.fromImage(qimage)
        icon = QIcon(pixmap)
        self._cache_for_info(is_dir).put_memory(key, icon, pixmap)
        self._debug("cache-ready", key)
        self._emit_thumbnail_changed(key)

    def _handle_thumbnail_ready(self, path: str, icon: QIcon | None, generation: int) -> None:
        # print(f"[MediaFileSystemModel] thumbnail ready for {path} icon={'Y' if icon else 'N'}", flush=True)
        key = self._normalise_key(path)

        if not self._is_current_request(key, generation):
            self._debug("stale", key, generation)
            return

        self._pending.discard(key)
        self._tokens.pop(key, None)
        if icon is None or icon.isNull():
            # print(f"[MediaFileSystemModel] thumbnail failed for {key}", flush=True)
            self._failed.add(key)
            self._debug("failed", key)
            return
        self._failed.discard(key)

        # ★★★ キーがフォルダかどうかで処理を分岐 ★★★
        target_path = Path(key)
        if target_path.is_dir():
            # --- フォルダプレビューの合成処理 ---
            # 1. ベースとなるフォルダアイコンを取得
            # 1. 文字列のパス(key)から、対応するQModelIndexを取得する
            folder_index = self.index(key)
            if not folder_index.isValid():
                return  # もしインデックスが無効なら、処理を中断

            # 2. 取得したQModelIndexを使って、ファイル情報を取得する
            folder_info = self.fileInfo(folder_index)
            base_icon = self._icon_provider.icon(folder_info)
            base_pixmap = base_icon.pixmap(QSize(self._thumbnail_edge, self._thumbnail_edge))

            # 2. サムネイル画像を取得
            thumb_pixmap = icon.pixmap(QSize(self._thumbnail_edge, self._thumbnail_edge))

            # 3. Painterを使ってアイコンを合成
            painter = QPainter(base_pixmap)
            # 中央に描画
            target_size = int(self._thumbnail_edge * 1.2)  # 少し大きめに
            scaled_thumb = thumb_pixmap.scaled(
                target_size,
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x, y = folder_thumbnail_rect(
                base_pixmap.size(), scaled_thumb.size(), self._thumbnail_edge
            )

            painter.drawPixmap(x, y, scaled_thumb)
            painter.end()

            # 4. 合成したPixmapから新しいQIconを作成してキャッシュ
            final_icon = QIcon(base_pixmap)
            folder_preview_cache.put_memory(key, final_icon, base_pixmap)
            self._save_cache_async(folder_preview_cache, key, base_pixmap)
        else:
            # --- 通常のファイルの処理 (変更なし) ---
            # QIconから元になったPixmapを取得して渡す
            pixmap = icon.pixmap(QSize(self._thumbnail_edge, self._thumbnail_edge))
            file_thumbnail_cache.put_memory(key, icon, pixmap)
            self._save_cache_async(file_thumbnail_cache, key, pixmap)

        self._debug("ready", key)
        self._emit_thumbnail_changed(key)

    def _save_cache_async(self, cache, key: str, pixmap: QPixmap) -> None:
        image = pixmap.toImage()
        if image.isNull():
            return
        self._scan_pool.start(CacheSaveJob(cache.disk_path(key), image, cache.enforce_disk_budget))

    def _emit_thumbnail_changed(self, key: str) -> None:
        index = self.index(key)
        if index.isValid():
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
            self.headerDataChanged.emit(Qt.Orientation.Vertical, index.row(), index.row())

    def supportedDropActions(self) -> Qt.DropAction:
        """このモデルがサポートするドロップアクションを宣言します。"""
        # コピーと移動の両方をサポートすることをビューに伝える
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    def supportedDragActions(self) -> Qt.DropAction:
        """このモデルがサポートするドロップアクションを宣言します。"""
        # コピーと移動の両方をサポートすることをビューに伝える
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """各アイテムの振る舞いを定義するフラグを返します。"""
        # まず、ベースクラスのデフォルトフラグを取得する
        default_flags = super().flags(index)

        if not index.isValid():
            return default_flags

        # すべてのアイテムをドラッグ可能にする
        default_flags |= Qt.ItemFlag.ItemIsDragEnabled

        # もしアイテムがディレクトリであれば、ドロップ先として有効にする
        if self.isDir(index):
            default_flags |= Qt.ItemFlag.ItemIsDropEnabled

        return default_flags

    # ------------------------------------------------------------------
    @staticmethod
    def _normalise_key(path: Path | str) -> str:
        candidate = path if isinstance(path, Path) else Path(path)
        try:
            return str(candidate.resolve(strict=False))
        except OSError:
            return str(candidate)

    def dropMimeData(
        self, data: QMimeData, action: Qt.DropAction, row: int, column: int, parent: QModelIndex
    ) -> bool:
        """ドラッグ＆ドロップによるファイル移動を処理"""
        if action == Qt.DropAction.IgnoreAction:
            logger.debug("drop ignored")
            return True

        if not data.hasUrls():
            logger.warning("drop has no URLs")
            return False

        if not parent.isValid() or not self.isDir(parent):
            logger.warning("drop target is not a valid directory")
            return False

        dest_dir = Path(self.filePath(parent))
        if not dest_dir.exists() or not dest_dir.is_dir():
            logger.warning("drop target directory does not exist: %s", dest_dir)
            return False

        moved = False
        for url in data.urls():
            src_path = Path(url.toLocalFile())
            if not src_path.exists():
                continue

            dest_path = dest_dir / src_path.name

            try:
                # os.rename だとドライブを跨ぐと失敗する → shutil.move を推奨
                shutil.move(str(src_path), str(dest_path))
                moved = True
            except Exception:
                logger.exception("drop move failed: %s -> %s", src_path, dest_path)

        return moved
