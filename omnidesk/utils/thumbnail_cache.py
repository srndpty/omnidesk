# utils/thumbnail_cache.py
"""Disk-backed thumbnail cache with in-memory LRU."""

from __future__ import annotations

import hashlib
import logging
import os
from collections import OrderedDict
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Generic, TypeVar

from PyQt6.QtCore import QSaveFile, QStandardPaths
from PyQt6.QtGui import QIcon, QPixmap

Key = TypeVar("Key")
logger = logging.getLogger(__name__)


def _app_cache_root() -> Path:
    # OSごとの「キャッシュフォルダ」（Windows: %LOCALAPPDATA%、mac: ~/Library/Caches、Linux: ~/.cache）
    loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    if not loc:
        loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    base = Path(loc) / "OmniDesk" / "thumbs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _stable_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()


class ThumbnailCache(Generic[Key]):
    """Lightweight in-memory LRU storing QIcon instances keyed by path (for backward compat)."""

    def __init__(self, capacity: int = 256) -> None:
        self._capacity = capacity
        self._store: OrderedDict[Key, tuple[QIcon, QPixmap]] = OrderedDict()

    def get(self, key: Key) -> QIcon | None:
        item = self._store.get(key)
        if item is not None:
            self._store.move_to_end(key)
            return item[0]  # icon
        return None

    def get_sized(self, key: Key, min_edge: int | None = None) -> QIcon | None:
        item = self._store.get(key)
        if item is None:
            return None
        icon, pixmap = item
        if pixmap.isNull():
            return None
        if min_edge is not None and max(pixmap.width(), pixmap.height()) < min_edge:
            return None
        self._store.move_to_end(key)
        return icon

    def put_memory(self, key: Key, icon: QIcon, pixmap: QPixmap) -> None:
        """Store an icon in memory without touching disk."""
        self.put(key, icon, pixmap)

    def put(self, key: Key, icon: QIcon, pixmap: QPixmap) -> None:
        self._store[key] = (icon, pixmap)
        self._store.move_to_end(key)
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def get_or_create(self, key: Key, factory: Callable[[], QIcon | None]) -> QIcon | None:
        icon = self.get(key)
        if icon is not None:
            return icon
        icon = factory()
        if icon is not None:
            # 旧インタフェース互換のため、Pixmap は作れないことがある
            # 呼び出し元の put が正しく呼ばれる経路があるのでここでは put しない
            pass
        return icon


class PersistentThumbnailCache(ThumbnailCache[Key]):
    """
    2層キャッシュ: メモリLRU + ディスクLRU（PNG）
    - ディスクキーは [パス|mtime|size|edge] のハッシュで生成 → 変更に自動追従
    - edge は要求されたサムネイルサイズ（同一ファイルで別サイズも並存可）
    """

    VERSION = "v1"  # キャッシュ仕様を変えたら上げる

    def __init__(
        self,
        capacity: int = 512,
        *,
        namespace: str,
        disk_max_items: int = 10_000,
        disk_max_bytes: int = 1_000_000_000,  # 1GB
        root: Path | None = None,
    ) -> None:
        super().__init__(capacity=capacity)
        self._root = (root or _app_cache_root()) / namespace
        self._root.mkdir(parents=True, exist_ok=True)
        self._disk_max_items = max(1, disk_max_items)
        self._disk_max_bytes = max(1, disk_max_bytes)

    # ---------- ディスクキー生成 ----------
    def _disk_key(self, key: Key, *, hint_edge: int | None = None) -> Path:
        skey = str(key)
        try:
            p = Path(skey)
            st = p.stat()
            mtime = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
            size = st.st_size
            kind = "D" if p.is_dir() else "F"
        except Exception:
            # ファイルが一時的に無い場合など
            logger.debug("Could not stat thumbnail key %s", skey, exc_info=True)
            mtime = 0
            size = 0
            kind = "U"
        edge = max(0, hint_edge or 0)
        material = f"{self.VERSION}|{skey}|{mtime}|{size}|{kind}|{edge}"
        name = _stable_hash(material) + ".png"
        return self._root / name

    def disk_path(self, key: Key, *, hint_edge: int | None = None) -> Path:
        """Return the PNG cache path for this key without loading it."""
        return self._disk_key(key, hint_edge=hint_edge)

    def get_memory(self, key: Key, *, min_edge: int | None = None) -> QIcon | None:
        """Return only the in-memory item; never read from disk on the UI thread."""
        return ThumbnailCache.get_sized(self, key, min_edge)

    def put_memory(self, key: Key, icon: QIcon, pixmap: QPixmap) -> None:
        """Store an icon in memory without writing the disk cache."""
        ThumbnailCache.put(self, key, icon, pixmap)

    def enforce_disk_budget(self) -> None:
        self._enforce_disk_budget()

    # ---------- メモリ+ディスク: get ----------
    def get(self, key: Key, *, hint_edge: int | None = None) -> QIcon | None:
        icon = ThumbnailCache.get_sized(self, key, hint_edge)
        if icon is not None:
            return icon

        # ディスクから復元
        path = self._disk_key(key, hint_edge=hint_edge)
        if path.exists():
            # print(f"[ThumbnailCache] load from disk: {path}", flush=True)
            px = QPixmap()
            if px.load(str(path), "PNG"):
                ic = QIcon(px)
                # メモリにのみ格納（再保存はしない）
                self._store[key] = (ic, px)
                self._store.move_to_end(key)
                while len(self._store) > self._capacity:
                    self._store.popitem(last=False)
                # LRU更新として mtime を touch
                with suppress(Exception):
                    os.utime(path, None)
                return ic
            logger.warning("Failed to load thumbnail cache file: %s", path)
        return None

    # ---------- メモリ+ディスク: put ----------
    def put(
        self,
        key: Key,
        icon: QIcon,
        pixmap: QPixmap,
        *,
        hint_edge: int | None = None,
    ) -> None:
        super().put(key, icon, pixmap)

        # ディスクに保存（原子的に）
        edge = hint_edge if hint_edge is not None else max(pixmap.width(), pixmap.height())
        dst = self._disk_key(key, hint_edge=edge)
        try:
            saver = QSaveFile(str(dst))
            saver.open(QSaveFile.OpenModeFlag.WriteOnly)
            pixmap.save(saver, "PNG")
            saver.commit()
            # print(f"[ThumbnailCache] saved to disk: {dst}", flush=True)
            with suppress(Exception):
                os.utime(str(dst), None)  # LRU更新
        except Exception:
            # 保存失敗は無視（メモリにはある）
            logger.exception("Failed to save thumbnail cache file: %s", dst)
            return

        self._enforce_disk_budget()

    # ---------- ディスクLRU整理 ----------
    def _enforce_disk_budget(self) -> None:
        try:
            files = list(self._root.glob("*.png"))
            if not files:
                return

            # サイズ・件数がしきい値内なら何もしない
            total = 0
            stats = []
            for f in files:
                try:
                    st = f.stat()
                    total += st.st_size
                    stats.append((f, st.st_mtime, st.st_size))
                except Exception:
                    logger.debug("Could not stat thumbnail cache file: %s", f, exc_info=True)
                    continue

            if len(stats) <= self._disk_max_items and total <= self._disk_max_bytes:
                return

            # 古い順に削除
            stats.sort(key=lambda t: t[1])  # mtime 昇順
            while (len(stats) > self._disk_max_items) or (total > self._disk_max_bytes):
                f, _mt, sz = stats.pop(0)
                with suppress(Exception):
                    f.unlink(missing_ok=True)
                total -= sz
        except Exception:
            logger.exception("Failed to enforce thumbnail cache budget in %s", self._root)

    # 任意: クリアAPI
    def clear_disk(self) -> None:
        for f in self._root.glob("*.png"):
            with suppress(Exception):
                f.unlink(missing_ok=True)


# 既存呼び出し側はそのままで OK
folder_preview_cache = PersistentThumbnailCache[str](
    capacity=512, namespace="folders", disk_max_items=5000, disk_max_bytes=400_000_000
)
file_thumbnail_cache = PersistentThumbnailCache[str](
    capacity=2048, namespace="files", disk_max_items=15000, disk_max_bytes=1_000_000_000
)
