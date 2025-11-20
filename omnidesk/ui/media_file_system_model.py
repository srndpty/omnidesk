"""QFileSystemModel variant with asynchronous media thumbnails."""

from __future__ import annotations

from pathlib import Path
from typing import Set

from PyQt6.QtCore import Qt, pyqtSignal, QModelIndex, QRunnable, QThreadPool, QObject
from PyQt6.QtGui import QIcon, QFileSystemModel
from PyQt6.QtWidgets import QApplication

from ..utils.thumbnail_cache import folder_preview_cache, file_thumbnail_cache
from .media_icon_provider import MediaThumbnailProvider
import shutil
from PyQt6.QtCore import QMimeData, QSize

from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QFileIconProvider


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
        self.setReadOnly(False)
        # ★★★ フォルダアイコン取得用のプロバイダを追加 ★★★
        self._icon_provider = QFileIconProvider()
        # フォルダのプレビュー用に、バックグラウンドスキャンのプールと状態を保持
        self._scan_pool = QThreadPool.globalInstance()
        self._scanning: Set[str] = set()

    # ------------------------------------------------------------------
    @property
    def media_extensions(self) -> set[str]:
        return self._provider.media_extensions

    def set_thumbnail_edge(self, edge: int) -> None:
        self._thumbnail_edge = max(16, edge)

    # ------------------------------------------------------------------
    def data(self, index, role):
        if role == Qt.ItemDataRole.DecorationRole and index.isValid() and index.column() == 0:
            file_info = self.fileInfo(index)
            path_str = file_info.absoluteFilePath()
            key = self._normalise_key(path_str)

            if file_info.isFile():
                cached = file_thumbnail_cache.get(key)
                if cached is not None:
                    return cached
            elif file_info.isDir():
                # ★★★ ここを修正 ★★★
                # フォルダの場合も、ただキャッシュを確認するだけにする
                cached = folder_preview_cache.get(key)
                if cached is not None:
                    return cached

        # リクエストのロジックは prioritize_thumbnail_requests に移譲されたので、
        # ここではキャッシュになければ常にデフォルトアイコンを返す
        return super().data(index, role)
    
    def _ensure_folder_thumbnail(self, path: Path) -> None:
        """フォルダのプレビューサムネイル生成をバックグラウンドで準備する"""
        key = self._normalise_key(path)
        if key in self._scanning:
            return

        self._pending.add(key)  # 先にペンディング状態にする
        self._scanning.add(key)

        job = _FolderScanJob(path, self.media_extensions)
        job.signals.finished.connect(self._handle_folder_scan_finished)
        self._scan_pool.start(job)

    # ★★★ 追加: ビューからサムネイル生成をリクエストするための新しいメソッド ★★★
    def prioritize_thumbnail_requests(self, indexes: list[QModelIndex]) -> None:
        """Given a list of visible indexes, request thumbnails for them."""
        for index in indexes:
            if not index.isValid():
                continue
            
            file_info = self.fileInfo(index)
            path = Path(file_info.absoluteFilePath())
            key = self._normalise_key(path)

            if key in self._pending or key in self._failed:
                continue

            # ★★★ ここからが修正されたロジック ★★★
            if file_info.isFile():
                # ★ 追加: ここでも軽くガード（get() はディスクからの復元を伴う）
                if file_thumbnail_cache.get(key) is not None:
                    self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
                    continue
                # --- ファイルの処理 (変更なし) ---
                suffix = path.suffix.lower()
                if suffix in self.media_extensions:
                    self._ensure_thumbnail(path, suffix)
            
            elif file_info.isDir() and self._thumbnail_edge > 64:
                if folder_preview_cache.get(key) is not None:
                    self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
                    continue
                # --- フォルダの処理をここに追加 ---
                self._ensure_folder_thumbnail(path)

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
   
        # ★ 追加: すでにキャッシュにあればジョブを投げない（ディスク→メモリ復元もここで済む）
        if file_thumbnail_cache.get(norm_key) is not None:
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
        started = self._provider.request_thumbnail(path, self._thumbnail_edge)
        if started:
            # print(f"[MediaFileSystemModel] job started for {norm_key}", flush=True)
            self._pending.add(norm_key)
        else:
            print(f"[MediaFileSystemModel] job not started for {norm_key}", flush=True)

    def _handle_thumbnail_ready(self, path: str, icon: QIcon | None) -> None:
        # print(f"[MediaFileSystemModel] thumbnail ready for {path} icon={'Y' if icon else 'N'}", flush=True)
        key = self._normalise_key(path)

        # ★ 追加: フォルダのプレビューがキャッシュにあれば生成しない
        if folder_preview_cache.get(key) is not None:
            idx = self.index(key)
            if idx.isValid():
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
            return

        self._pending.discard(key)
        if icon is None or icon.isNull():
            # print(f"[MediaFileSystemModel] thumbnail failed for {key}", flush=True)
            self._failed.add(key)
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
                return # もしインデックスが無効なら、処理を中断

            # 2. 取得したQModelIndexを使って、ファイル情報を取得する
            folder_info = self.fileInfo(folder_index)
            base_icon = self._icon_provider.icon(folder_info)
            base_pixmap = base_icon.pixmap(QSize(self._thumbnail_edge, self._thumbnail_edge))
            
            # 2. サムネイル画像を取得
            thumb_pixmap = icon.pixmap(QSize(self._thumbnail_edge, self._thumbnail_edge))

            # 3. Painterを使ってアイコンを合成
            painter = QPainter(base_pixmap)
            # 中央に描画
            target_size = int(self._thumbnail_edge * 1.2) # 少し大きめに
            scaled_thumb = thumb_pixmap.scaled(
                target_size, target_size, 
                Qt.AspectRatioMode.KeepAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
            x = (base_pixmap.width() - scaled_thumb.width()) // 2
            y = (base_pixmap.height() - scaled_thumb.height()) // 2 - int(self._thumbnail_edge * 0.05) # 少し上に
            
            painter.drawPixmap(x, y, scaled_thumb)
            painter.end()
            
            # 4. 合成したPixmapから新しいQIconを作成してキャッシュ
            final_icon = QIcon(base_pixmap)
            # print(f"  final_icon valid={'Y' if not final_icon.isNull() else 'N'}", flush=True)
            folder_preview_cache.put(key, final_icon, base_pixmap)
            # print(f"[MediaFileSystemModel] folder thumbnail created for {key}", flush=True)
        else:
            # --- 通常のファイルの処理 (変更なし) ---
            # QIconから元になったPixmapを取得して渡す
            pixmap = icon.pixmap(QSize(self._thumbnail_edge, self._thumbnail_edge))
            file_thumbnail_cache.put(key, icon, pixmap)
        
        index = self.index(key)
        if index.isValid():
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
            self.headerDataChanged.emit(Qt.Orientation.Vertical, index.row(), index.row())

    def _handle_folder_scan_finished(self, dir_path: str, image_path: str | None) -> None:
        """バックグラウンドスキャンの結果を受け取り、必要ならサムネイル生成を依頼する"""
        key = self._normalise_key(dir_path)
        self._scanning.discard(key)

        if image_path is None:
            self._pending.discard(key)
            self._failed.add(key)
            return

        started = self._provider.request_thumbnail(
            Path(image_path),
            self._thumbnail_edge,
            result_key=key,
        )
        if not started:
            self._pending.discard(key)
            self._failed.add(key)

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
        self,
        data: QMimeData,
        action: Qt.DropAction,
        row: int,
        column: int,
        parent: QModelIndex
    ) -> bool:
        print
        """ドラッグ＆ドロップによるファイル移動を処理"""
        if action == Qt.DropAction.IgnoreAction:
            print(f"[MediaFileSystemModel] drop ignored")
            return True

        if not data.hasUrls():
            print(f"[MediaFileSystemModel] drop has no URLs")
            return False

        if not parent.isValid() or not self.isDir(parent):
            print(f"[MediaFileSystemModel] drop target is not a valid directory")
            return False

        dest_dir = Path(self.filePath(parent))
        if not dest_dir.exists() or not dest_dir.is_dir():
            print(f"[MediaFileSystemModel] drop target directory does not exist: {dest_dir}")
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
            except Exception as e:
                print(f"[MediaFileSystemModel] move failed: {e}")

        return moved


class _FolderScanSignals(QObject):
    """フォルダスキャンの完了を通知するためのシグナル保持クラス"""

    finished = pyqtSignal(str, object)  # dir_path, image_path | None


class _FolderScanJob(QRunnable):
    """フォルダ内の最初のメディアファイルをバックグラウンドで探索するジョブ"""

    def __init__(self, dir_path: Path, media_extensions: set[str]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._dir_path = dir_path
        self._media_extensions = {ext.lower() for ext in media_extensions}
        self.signals = _FolderScanSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable contract
        image_path = self._find_first_image_in_dir(self._dir_path)
        self.signals.finished.emit(str(self._dir_path), str(image_path) if image_path else None)

    def _find_first_image_in_dir(self, dir_path: Path) -> Path | None:
        """指定されたディレクトリ内で、名前順で最初の画像ファイルを探す"""
        try:
            sorted_entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
            for entry in sorted_entries:
                if entry.is_file() and entry.suffix.lower() in self._media_extensions:
                    return entry  # 最初の画像ファイルが見つかったら即座に返す
        except OSError:
            return None
        return None
