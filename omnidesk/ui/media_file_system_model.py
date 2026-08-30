"""走査ベースのディレクトリモデルに、非同期のメディアサムネイルを載せた版。

土台は :class:`omnidesk.ui.directory_model.DirectoryModel`。``QFileSystemModel``
から置き換えた理由（大量ファイルのフォルダでGUIスレッドが秒単位で止まる）は
そちらのモジュールdocstringに書いてある。
"""

from __future__ import annotations

import logging
import os
from contextlib import suppress
from pathlib import Path
from threading import Lock

from PyQt6.QtCore import QFileInfo, QMimeData, QModelIndex, QSize, Qt
from PyQt6.QtGui import QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QFileIconProvider

from ..utils.thumbnail_cache import file_thumbnail_cache, folder_preview_cache
from .directory_model import DirectoryEntry, DirectoryModel
from .media_icon_provider import MediaThumbnailProvider
from .qt_lifetime import own_by_application
from .thumbnail_jobs import (
    CacheLoadJob,
    CacheSaveJob,
    CancellationToken,
    FolderScanJob,
    ThumbnailJobSignals,
)

logger = logging.getLogger(__name__)
FOLDER_PREVIEW_DISK_EDGES = frozenset({96, 160})


def folder_thumbnail_rect(base_size: QSize, thumb_size: QSize, edge: int) -> tuple[int, int]:
    """Return the top-left point for a folder preview thumbnail overlay."""
    x = (base_size.width() - thumb_size.width()) // 2
    y = (base_size.height() - thumb_size.height()) // 2 - int(edge * 0.05)
    return x, y


def folder_thumbnail_preview_edge(edge: int) -> int:
    """Return the largest preview edge that fits with the folder overlay offset."""
    return max(1, edge - int(edge * 0.1))


def folder_base_pixmap(base_icon: QIcon, edge: int) -> QPixmap:
    """Return a folder base pixmap normalized to the requested thumbnail edge."""
    target = QSize(edge, edge)
    base = base_icon.pixmap(target)
    if base.size() == target:
        return base

    canvas = QPixmap(target)
    canvas.fill(Qt.GlobalColor.transparent)
    if base.isNull():
        return canvas

    scaled = base.scaled(
        edge,
        edge,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = (edge - scaled.width()) // 2
    y = (edge - scaled.height()) // 2
    painter = QPainter(canvas)
    painter.drawPixmap(x, y, scaled)
    painter.end()
    return canvas


def icon_with_enlarged_pixmap(base_icon: QIcon, edge: int) -> QIcon:
    """``edge`` に届く絵を持たないアイコンに、拡大した絵を足したアイコンを返す。

    シェルのフォルダアイコンは 48px 程度までしか持たないことがあり、``QIcon`` は
    持っている絵より大きくは描かない。タイル表示ではサムネイルだけが 160px で
    描かれ、プレビューを作れないフォルダのアイコンだけが小さく見えていた。

    元の絵は残したまま拡大版を足すので、小さいサイズを要求する経路
    （ツリー表示の 32px など）では、これまでどおり実寸の絵が使われる。
    """
    if edge <= 0:
        return base_icon
    sizes = base_icon.availableSizes()
    if not sizes:
        return base_icon
    largest = max(sizes, key=lambda size: size.width() * size.height())
    if largest.width() >= edge or largest.height() >= edge:
        return base_icon
    source = base_icon.pixmap(largest)
    if source.isNull():
        return base_icon
    enlarged = QIcon(base_icon)
    enlarged.addPixmap(
        source.scaled(
            edge,
            edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    return enlarged


def cache_pixmap_for_edge(pixmap: QPixmap, edge: int) -> QPixmap:
    """Return a cache pixmap whose outer edge matches the requested cache edge."""
    target_size = QSize(edge, edge)
    if pixmap.isNull() or edge <= 0:
        return pixmap

    if pixmap.width() > edge or pixmap.height() > edge:
        pixmap = pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    if pixmap.size() == target_size:
        return pixmap

    canvas = QPixmap(target_size)
    canvas.fill(Qt.GlobalColor.transparent)
    x = (edge - pixmap.width()) // 2
    y = (edge - pixmap.height()) // 2
    painter = QPainter(canvas)
    painter.drawPixmap(x, y, pixmap)
    painter.end()
    return canvas


# アイコンがファイルごとに違い得る拡張子。実行ファイルは埋め込みアイコンを持ち、
# ショートカットはリンク先のアイコンを指し、アイコンファイル自体は中身がアイコン。
# 拡張子単位でキャッシュすると、別のファイルの絵を使い回してしまう。
PER_FILE_ICON_SUFFIXES = frozenset(
    {"exe", "lnk", "ico", "cur", "ani", "scr", "cpl", "msc", "url", "dll", "msi"}
)

# 拡張子と衝突しないキャッシュキー（拡張子は常に小文字・ドット無し）。
_FOLDER_ICON_KEY = "<folder>"
_GENERIC_FILE_ICON_KEY = "<file>"


def _make_icon_provider() -> QFileIconProvider:
    """フォルダ個別アイコンを引かないアイコンプロバイダを作る。

    ``DontUseCustomDirectoryIcons`` は、フォルダごとの独自アイコン
    （Windowsでは ``desktop.ini`` の参照）を止めるQtの公式オプション。Qtの
    ドキュメントも「ネットワークやリムーバブルドライブで大きな性能影響がある」と
    明記している。

    土台が ``QFileSystemModel`` だった頃は、プロバイダをエントリごとに
    バックグラウンドの ``QFileInfoGatherer`` スレッドから呼ばれていたため、
    Python側で作ったインスタンスを渡すと破棄と競合してプロセスごと落ちていた。
    現在の土台（``DirectoryModel``）はアイコンを一切扱わず、ここでの参照は
    ``data()`` からGUIスレッドでしか起きないので、自前で持って問題ない。
    """
    provider = QFileIconProvider()
    provider.setOptions(QFileIconProvider.Option.DontUseCustomDirectoryIcons)
    return provider


class MediaFileSystemModel(DirectoryModel):
    """``DirectoryModel`` に、キャッシュ付きのメディアサムネイルを足したモデル。"""

    # thumbnailUpdated = pyqtSignal(QModelIndex)

    # パス正規化キャッシュの上限。フォルダ数件ぶんを保持できれば十分で、
    # 超えたらまとめて捨てる（LRUにするほどの効果はない）。
    KEY_CACHE_LIMIT = 20_000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._key_cache: dict[str, str] = {}
        self._thumbnail_edge = 96
        self._provider = MediaThumbnailProvider(self)
        self._provider.thumbnailReady.connect(self._handle_thumbnail_ready)
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        self._visible_keys: set[str] = set()
        self._tokens: dict[str, CancellationToken] = {}
        self._generations: dict[str, int] = {}
        self._request_edges: dict[str, int] = {}
        self._allow_folder_preview_for_visible_targets = True
        self._icon_provider = _make_icon_provider()
        # 拡張子ごとのアイコン。エントリごとにシェルへ問い合わせない。
        self._type_icons: dict[str, QIcon] = {}
        # フォルダプレビューの土台画像はエッジごとに1枚あれば足りる（下記参照）。
        self._folder_base_pixmaps: dict[int, QPixmap] = {}
        # プレビューが無いフォルダ用のアイコン。表示サイズ（エッジ）ごとに1つ。
        self._folder_icons: dict[int, QIcon] = {}
        # ジョブはワーカースレッドで破棄されるため、シグナル用QObjectはジョブに
        # 持たせず1つだけ用意して共有する。寿命はモデルではなく QApplication に
        # 預ける（サムネイル生成中にタブを閉じても壊れないようにするため）。
        self._job_signals = own_by_application(ThumbnailJobSignals())
        self._job_signals.folder_scanned.connect(self._handle_folder_scan_result)
        self._job_signals.cache_loaded.connect(self._handle_cache_loaded)
        self._folder_scans: dict[str, FolderScanJob] = {}
        self._cache_jobs: dict[str, CacheLoadJob] = {}
        self._cache_save_generations: dict[tuple[int, str, int], int] = {}
        self._cache_save_lock = Lock()
        self._debug_thumbnails = os.environ.get("OMNIDESK_THUMB_DEBUG", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    # ------------------------------------------------------------------
    @property
    def media_extensions(self) -> set[str]:
        return self._provider.media_extensions

    def setRootPath(self, path: str) -> QModelIndex:  # noqa: N802 - Qt-style API
        # シンボリックリンクの張り替えなどで正規化結果が古くなり得るため、
        # ディレクトリを移動するたびにキャッシュを捨てる。
        self._key_cache.clear()
        return super().setRootPath(path)

    def set_thumbnail_edge(self, edge: int) -> None:
        self._thumbnail_edge = max(16, edge)

    def _debug(self, event: str, key: str, detail: object = "") -> None:
        if self._debug_thumbnails:
            logger.debug("[thumb:%s] %s %s", event, key, detail)

    # ------------------------------------------------------------------
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """描画で使う値を返す。

        描画経路なので、**行ごとの同期I/Oは行わない**。表示に要る情報はすべて
        走査時に確定した :class:`DirectoryEntry` から取る。唯一の例外は
        :meth:`_type_icon` で、拡張子ごとの初回だけシェルへ問い合わせる
        （件数ではなく、フォルダ内の異なる拡張子の数にしか比例しない）。
        """
        if role != Qt.ItemDataRole.DecorationRole or index.column() != 0:
            return super().data(index, role)

        entry = self.entry(index)
        if entry is None:
            return None

        # サムネイルはメモリキャッシュにあるときだけ返す。生成のリクエストは
        # set_visible_thumbnail_targets が担当する。
        cache = self._cache_for_info(entry.is_dir)
        cached = cache.get_memory(entry.key, min_edge=self._thumbnail_edge)
        if cached is not None:
            return cached
        return self._type_icon(entry)

    def _type_icon(self, entry: DirectoryEntry) -> QIcon:
        """アイコンを拡張子ごとに1回だけ引いて使い回す。

        エントリごとにシェルへ問い合わせると、大量ファイルのフォルダで描画が
        止まる。ほとんどの拡張子はアイコンが拡張子の関連付けだけで決まるので、
        拡張子単位のキャッシュで正しい絵が出せる。

        ただし :data:`PER_FILE_ICON_SUFFIXES` の拡張子は**ファイルごとに絵が違う**。
        1つ目に見た ``a.exe`` のアイコンを ``b.exe`` にも使ってしまうと、
        まったく別のアプリのアイコンを表示することになる。これらは種別の汎用
        アイコンに寄せる（具体性は落ちるが、間違った絵は出さない）。

        なお、拡張子ごとの初回だけはここでシェルへ問い合わせるため、GUIスレッドで
        の同期I/Oが発生する。フォルダ内の**異なる拡張子の数**だけで、件数には
        比例しない。
        """
        if entry.is_dir:
            return self._folder_type_icon()
        if entry.suffix in PER_FILE_ICON_SUFFIXES:
            return self._cached_type_icon(_GENERIC_FILE_ICON_KEY, QFileIconProvider.IconType.File)

        cached = self._type_icons.get(entry.suffix)
        if cached is None:
            cached = self._icon_provider.icon(QFileInfo(entry.path))
            if cached.isNull():
                cached = self._icon_provider.icon(QFileIconProvider.IconType.File)
            self._type_icons[entry.suffix] = cached
        return cached

    def _folder_type_icon(self) -> QIcon:
        """プレビューが無いフォルダのアイコンを、サムネイルと同じ大きさで返す。"""
        edge = self._thumbnail_edge
        cached = self._folder_icons.get(edge)
        if cached is None:
            base = self._cached_type_icon(_FOLDER_ICON_KEY, QFileIconProvider.IconType.Folder)
            cached = icon_with_enlarged_pixmap(base, edge)
            self._folder_icons[edge] = cached
        return cached

    def _cached_type_icon(self, cache_key: str, icon_type: QFileIconProvider.IconType) -> QIcon:
        cached = self._type_icons.get(cache_key)
        if cached is None:
            cached = self._icon_provider.icon(icon_type)
            self._type_icons[cache_key] = cached
        return cached

    def _new_token(self, key: str) -> CancellationToken:
        generation = self._generations.get(key, 0) + 1
        self._generations[key] = generation
        token = CancellationToken(generation)
        self._tokens[key] = token
        self._request_edges[key] = self._thumbnail_edge
        return token

    def _bump_thumbnail_generation(self, key: str) -> None:
        self._generations[key] = self._generations.get(key, 0) + 1

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
        self._request_edges.pop(key, None)
        self._provider.cancel_thumbnail(key)

    def clear_visible_thumbnail_targets(self) -> None:
        for key in list(self._visible_keys):
            self._cancel_thumbnail_key(key)
        self._visible_keys.clear()

    def forget_failed_thumbnails(self) -> None:
        """記録済みのサムネイル失敗を消し、可視アイテムを再試行できるようにする。

        一過性の読み取りエラー（ロック中・書き込み途中のファイルなど）は、成功時しか
        ``_failed`` を解除せず、ファイル単位の無効化経路も無いため、モデルの寿命の間
        ずっと残ってしまう。明示的な refresh が、それらのファイルを再試行する自然な
        起点となる。
        """
        self._failed.clear()

    def cancel_background_work(self) -> None:
        """非表示中に不要なサムネイル処理をキャンセルする。"""
        self.clear_visible_thumbnail_targets()
        for key in list(self._tokens):
            self._cancel_thumbnail_key(key)
        self._folder_scans.clear()
        self._cache_jobs.clear()

    def shutdown_background_work(self) -> None:
        """破棄前にバックグラウンド処理を停止し、専用スレッドの終了を待つ。"""
        self.cancel_background_work()
        self._provider.shutdown_video_jobs()

    def _cache_for_info(self, is_dir: bool):
        return folder_preview_cache if is_dir else file_thumbnail_cache

    def set_visible_thumbnail_targets(
        self,
        indexes: list[QModelIndex],
        *,
        request_limit: int | None = None,
        allow_folder_preview: bool = True,
    ) -> int:
        """Request thumbnails only for currently visible model indexes."""
        self._allow_folder_preview_for_visible_targets = allow_folder_preview
        ordered: list[tuple[str, Path, bool]] = []
        seen: set[str] = set()
        for index in indexes:
            # 走査時に確定した情報だけを使う。QFileInfo を作ると可視アイテム数ぶんの
            # 実I/Oが描画のたびにGUIスレッドで走る。
            entry = self.entry(index)
            if entry is None:
                continue
            if entry.key in seen:
                continue
            seen.add(entry.key)
            ordered.append((entry.key, Path(entry.path), entry.is_dir))

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
        for key, path, is_dir in ordered:
            if request_limit is not None and requested >= request_limit:
                break
            # スクロール中はフォルダプレビューを新規に起こさない。以前はここで
            # ディスクキャッシュの有無も見ていたが、disk_path() は stat + is_dir、
            # exists() でさらに1回と、可視アイテム数ぶんの同期I/OがGUIスレッドで
            # 走っていた。スクロール中はメモリキャッシュだけで判定し、ディスクに
            # だけあるものはスクロール停止後（allow_folder_preview=True）に拾う。
            if (
                is_dir
                and not allow_folder_preview
                and self._cache_for_info(is_dir).get_memory(key, min_edge=self._thumbnail_edge)
                is None
            ):
                continue
            if self._request_visible_key(key, path, is_dir):
                requested += 1
        return requested

    def _request_visible_key(self, key: str, path: Path, is_dir: bool) -> bool:
        if key in self._pending or key in self._failed:
            self._debug("skip", key, "pending" if key in self._pending else "failed")
            return False
        cache = self._cache_for_info(is_dir)
        if cache.get_memory(key, min_edge=self._thumbnail_edge) is not None:
            self._debug("memory-hit", key)
            index = self.index_for_path(key)
            if index.isValid():
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])
            return False

        # そもそもサムネイルを持ち得ないエントリは、I/Oせずここで落とす。
        # （request_limit の消費対象にもしない）
        if not self._can_have_thumbnail(path, is_dir):
            return False

        # ディスクキャッシュの有無は CacheLoadJob が worker で確かめる。
        # 以前はここで disk_path()（stat + is_dir）と exists() を呼んでおり、
        # 可視アイテム数 × タイマー発火回数ぶんの同期I/OがGUIスレッドで走っていた。
        # キャッシュが無ければ _handle_cache_loaded が生成経路へフォールバックする。
        disk_path = cache.disk_path(key, hint_edge=self._thumbnail_edge)
        self._debug("disk-load", key, disk_path)
        token = self._new_token(key)
        job = CacheLoadJob(key, disk_path, token, self._job_signals, is_dir=is_dir)
        self._cache_jobs[key] = job
        self._pending.add(key)
        self._scan_pool.start(job)
        return True

    def _can_have_thumbnail(self, path: Path, is_dir: bool) -> bool:
        """サムネイル生成の対象になり得るエントリかを、I/Oせずに判定する。"""
        if is_dir:
            # 小さいアイコンではフォルダプレビューを作らない。
            return self._thumbnail_edge > 64
        return path.suffix.lower() in self.media_extensions

    def _ensure_folder_thumbnail(self, path: Path) -> None:
        """フォルダのプレビューサムネイル生成をリクエストする"""
        key = self._normalise_key(path)
        if key in self._folder_scans:
            return

        token = self._new_token(key)
        self._pending.add(key)

        job = FolderScanJob(key, path, self.media_extensions, token, self._job_signals)
        self._folder_scans[key] = job
        self._scan_pool.start(job)

    def invalidate_folder_thumbnail_preview(self, path: Path) -> None:
        """Drop cached folder preview so the next visible request re-scans it."""
        key = self._normalise_key(path)
        self._cancel_thumbnail_key(key)
        self._bump_thumbnail_generation(key)
        self._failed.discard(key)
        self._invalidate_cache_saves(folder_preview_cache, key)
        folder_preview_cache.discard_memory(key)
        folder_preview_cache.discard_disk_all_sizes(
            key,
            hint_edges=FOLDER_PREVIEW_DISK_EDGES | {self._thumbnail_edge},
        )
        self._emit_thumbnail_changed(key)

    def _handle_folder_scan_result(
        self, key: str, generation: int, image_path: Path | None
    ) -> None:
        self._folder_scans.pop(key, None)
        if not self._is_current_request(key, generation):
            self._debug("folder-stale", key, generation)
            return

        if image_path:
            self._debug("folder-found", key, image_path)
            request_edge = self._request_edges.get(key, self._thumbnail_edge)
            started = self._provider.request_thumbnail(
                image_path,
                request_edge,
                result_key=key,
                token=self._tokens.get(key),
            )
            if not started:
                self._pending.discard(key)
                self._request_edges.pop(key, None)
                self._failed.add(key)
                self._debug("folder-image-not-started", key, image_path)
            return

        self._pending.discard(key)
        self._request_edges.pop(key, None)
        self._failed.add(key)
        self._debug("folder-none", key)

    def prioritize_thumbnail_requests(self, indexes: list[QModelIndex]) -> None:
        """Given a list of visible indexes, request thumbnails for them."""
        self.set_visible_thumbnail_targets(indexes)

    def get_path_list(self, indexes: list[QModelIndex]) -> list[str]:
        """Given a list of indexes, return their absolute file paths."""
        paths = []
        for index in indexes:
            entry = self.entry(index)
            if entry is not None:
                paths.append(entry.path)
        return paths

    # ------------------------------------------------------------------
    def _ensure_thumbnail(self, path: Path, suffix: str, key: str | None = None) -> None:
        norm_key = key or self._normalise_key(path)

        if file_thumbnail_cache.get_memory(norm_key, min_edge=self._thumbnail_edge) is not None:
            idx = self.index_for_path(norm_key)
            if idx.isValid():
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
            return

        if norm_key in self._pending or norm_key in self._failed:
            return
        if suffix in self._provider.VIDEO_EXTENSIONS and not self._provider.video_supported:
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
        request_edge = self._request_edges.pop(key, self._thumbnail_edge)
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
        expected_size = QSize(request_edge, request_edge)
        if pixmap.size() != expected_size:
            self._debug("cache-wrong-size", key, f"{pixmap.size()}!={expected_size}")
            with suppress(OSError):
                self._cache_for_info(is_dir).disk_path(key, hint_edge=request_edge).unlink()
            path = Path(key)
            if is_dir:
                self._ensure_folder_thumbnail(path)
            else:
                self._ensure_thumbnail(path, path.suffix.lower(), key)
            return
        icon = QIcon(pixmap)
        self._cache_for_info(is_dir).put_memory(key, icon, pixmap)
        self._debug("cache-ready", key)
        self._request_current_edge_if_needed(key, Path(key), is_dir, request_edge)
        self._emit_thumbnail_changed(key)

    def _handle_thumbnail_ready(self, path: str, icon: QIcon | None, generation: int) -> None:
        key = self._normalise_key(path)

        if not self._is_current_request(key, generation):
            self._debug("stale", key, generation)
            return

        self._pending.discard(key)
        self._tokens.pop(key, None)
        request_edge = self._request_edges.pop(key, self._thumbnail_edge)
        if icon is None or icon.isNull():
            self._failed.add(key)
            self._debug("failed", key)
            return
        self._failed.discard(key)

        target_path = Path(key)
        folder_index = self.index_for_path(key)
        # 種別は走査時に確定した情報から決める。以前は Path.is_dir() を（しかも
        # 同じ関数内で2回）呼んでおり、サムネイルが1件完成するたびにGUIスレッドで
        # 実I/Oが走っていた。7,500件のフォルダでは無視できない。
        entry = self.entry(folder_index)
        is_dir = entry.is_dir if entry is not None else target_path.is_dir()

        if is_dir:
            if not folder_index.isValid():
                return

            base_pixmap = self._folder_base_pixmap_for_edge(request_edge)
            thumb_pixmap = icon.pixmap(QSize(request_edge, request_edge))

            painter = QPainter(base_pixmap)
            target_size = folder_thumbnail_preview_edge(request_edge)
            scaled_thumb = thumb_pixmap.scaled(
                target_size,
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x, y = folder_thumbnail_rect(base_pixmap.size(), scaled_thumb.size(), request_edge)

            painter.drawPixmap(x, y, scaled_thumb)
            painter.end()

            final_icon = QIcon(base_pixmap)
            folder_preview_cache.put_memory(key, final_icon, base_pixmap)
            self._save_cache_async(folder_preview_cache, key, base_pixmap, hint_edge=request_edge)
        else:
            pixmap = icon.pixmap(QSize(request_edge, request_edge))
            pixmap = cache_pixmap_for_edge(pixmap, request_edge)
            icon = QIcon(pixmap)
            file_thumbnail_cache.put_memory(key, icon, pixmap)
            self._save_cache_async(file_thumbnail_cache, key, pixmap, hint_edge=request_edge)

        self._request_current_edge_if_needed(key, target_path, is_dir, request_edge)
        self._debug("ready", key)
        self._emit_thumbnail_changed(key)

    def _folder_base_pixmap_for_edge(self, edge: int) -> QPixmap:
        """フォルダプレビューの土台画像を、エッジごとに1枚だけ作って使い回す。

        :class:`LightweightIconProvider` によりフォルダの基底アイコンは全フォルダで
        同一になったため、エッジが同じなら土台も同じになる。以前はフォルダ1件ごとに
        ``QIcon`` からの取り出しと平滑スケーリングをやり直していた。

        呼び出し側はこの上にサムネイルを描き込むので、必ずコピーを返す。
        """
        cached = self._folder_base_pixmaps.get(edge)
        if cached is None:
            provider = self._icon_provider
            base_icon = (
                provider.icon(QFileIconProvider.IconType.Folder)
                if provider is not None
                else QIcon()
            )
            cached = folder_base_pixmap(base_icon, edge)
            self._folder_base_pixmaps[edge] = cached
        return cached.copy()

    def _request_current_edge_if_needed(
        self, key: str, path: Path, is_dir: bool, completed_edge: int
    ) -> None:
        if completed_edge >= self._thumbnail_edge:
            return
        if key not in self._visible_keys:
            return
        if is_dir and not self._allow_folder_preview_for_visible_targets:
            # set_visible_thumbnail_targets と同じ理由で、ここでも exists() は見ない。
            cache = self._cache_for_info(is_dir)
            if cache.get_memory(key, min_edge=self._thumbnail_edge) is None:
                self._debug("edge-stale-throttled", key, completed_edge)
                return
        self._debug("edge-stale", key, f"{completed_edge}->{self._thumbnail_edge}")
        self._request_visible_key(key, path, is_dir)

    def _save_cache_async(
        self, cache, key: str, pixmap: QPixmap, *, hint_edge: int | None = None
    ) -> None:
        if pixmap.isNull():
            return
        edge = hint_edge if hint_edge is not None else self._thumbnail_edge
        if edge <= 0:
            return
        cache_pixmap = cache_pixmap_for_edge(pixmap, edge)
        if cache_pixmap.isNull():
            return
        save_key = self._cache_save_key(cache, key, edge)
        generation = self._next_cache_save_generation(save_key)
        self._scan_pool.start(
            CacheSaveJob(
                cache.disk_path(key, hint_edge=edge),
                cache_pixmap.toImage(),
                # 保存1件ごとの全走査を避けるため、間引き版を渡す。
                # サムネイルは1フォルダで数千件保存され得る。
                cache.maybe_enforce_disk_budget,
                lambda temp_path, cache_path: self._commit_cache_save(
                    save_key,
                    generation,
                    temp_path,
                    cache_path,
                ),
            )
        )

    @staticmethod
    def _cache_save_key(cache, key: str, edge: int) -> tuple[int, str, int]:
        return id(cache), key, edge

    def _next_cache_save_generation(self, save_key: tuple[int, str, int]) -> int:
        with self._cache_save_lock:
            generation = self._cache_save_generations.get(save_key, 0) + 1
            self._cache_save_generations[save_key] = generation
            return generation

    def _invalidate_cache_saves(self, cache, key: str) -> None:
        cache_id = id(cache)
        with self._cache_save_lock:
            matching_keys = [
                save_key
                for save_key in self._cache_save_generations
                if save_key[0] == cache_id and save_key[1] == key
            ]
            for save_key in matching_keys:
                self._cache_save_generations[save_key] += 1

    def _commit_cache_save(
        self,
        save_key: tuple[int, str, int],
        generation: int,
        temp_path: Path,
        cache_path: Path,
    ) -> bool:
        with self._cache_save_lock:
            if self._cache_save_generations.get(save_key) != generation:
                return False
            # Keep replace under the generation lock so invalidation cannot
            # interleave between the final generation check and disk commit.
            temp_path.replace(cache_path)
            return True

    def _emit_thumbnail_changed(self, key: str) -> None:
        index = self.index_for_path(key)
        if index.isValid():
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def supportedDropActions(self) -> Qt.DropAction:
        """このモデルがサポートするドロップアクションを宣言します。"""
        # コピーと移動の両方をサポートすることをビューに伝える
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.TargetMoveAction

    def supportedDragActions(self) -> Qt.DropAction:
        """このモデルがサポートするドロップアクションを宣言します。"""
        # コピーと移動の両方をサポートすることをビューに伝える
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.TargetMoveAction

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """各アイテムの振る舞いを定義するフラグを返します。"""
        # まず、ベースクラスのデフォルトフラグを取得する
        default_flags = super().flags(index)

        if not index.isValid():
            return default_flags

        # すべてのアイテムをドラッグ可能にする
        default_flags |= Qt.ItemFlag.ItemIsDragEnabled

        # 名前列はインプレースリネーム（F2）のため編集可能にする。
        # モデルは読み取り専用のままで、実際のリネームはデリゲート経由で
        # タブ側のロジックが行う。
        if index.column() == 0:
            default_flags |= Qt.ItemFlag.ItemIsEditable

        # もしアイテムがディレクトリであれば、ドロップ先として有効にする
        if self.isDir(index):
            default_flags |= Qt.ItemFlag.ItemIsDropEnabled

        return default_flags

    # ------------------------------------------------------------------
    def _normalise_key(self, path: Path | str) -> str:
        """サムネイルキー用にパスを正規化する（結果をキャッシュする）。

        ``Path.resolve()`` は Windows では実ファイルシステムアクセスを伴う。
        このメソッドは ``data()`` の描画経路からも呼ばれるため、キャッシュしないと
        スクロールのたびに可視アイテム数ぶんの同期I/OがGUIスレッドで走る。
        ネットワークドライブや低速メディアでは体感できるフリーズになる。
        """
        raw = str(path)
        cached = self._key_cache.get(raw)
        if cached is not None:
            return cached
        key = self._resolve_key(path)
        if len(self._key_cache) >= self.KEY_CACHE_LIMIT:
            self._key_cache.clear()
        self._key_cache[raw] = key
        return key

    @staticmethod
    def _resolve_key(path: Path | str) -> str:
        """パスを、ファイルシステムへ問い合わせずに正規化する。

        以前は ``Path.resolve()`` を使っていたが、これはシンボリックリンクを
        辿るため実I/Oを伴い、低速ドライブでは1回でも描画スレッドを止める。
        実際にウォッチドッグが ``paint`` → ``data`` → ``resolve`` の経路で
        12秒のGUI停止を記録している。

        ここでのキー契約は「同じ**字句的絶対パス**が同じ文字列になること」。
        シンボリックリンク経由と実体パスは別キーになるが、これは実I/Oを避ける
        ための意図的な割り切りで、キャッシュが二重に載る以外の実害はない。

        ``abspath`` はパス文字列の正規化とカレントディレクトリの解決だけを行い、
        ディスクへ触らない。``normcase`` はWindowsで大文字小文字と区切り文字を
        揃えるため、``F:\\A.PNG`` と ``f:\\a.png`` が同じキーになる。
        """
        try:
            return os.path.normcase(os.path.abspath(str(path)))
        except (OSError, ValueError):
            logger.debug("パスを正規化できません: %s", path, exc_info=True)
            return str(path)

    def dropMimeData(
        self, data: QMimeData, action: Qt.DropAction, row: int, column: int, parent: QModelIndex
    ) -> bool:
        """Reject model-level drops so view/controller code owns file operations."""
        if action == Qt.DropAction.IgnoreAction:
            logger.debug("drop ignored")
            return True
        logger.info("Ignoring model-level drop; file browser views handle URL drops")
        return False

    def canDropMimeData(
        self, data: QMimeData, action: Qt.DropAction, row: int, column: int, parent: QModelIndex
    ) -> bool:
        """Reject model-level URL drops; views handle file operations."""
        return action == Qt.DropAction.IgnoreAction
