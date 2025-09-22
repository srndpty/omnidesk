"""Simple in-memory thumbnail cache with LRU eviction."""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Generic, Optional, TypeVar

from PyQt6.QtGui import QIcon, QPixmap

Key = TypeVar("Key")


class ThumbnailCache(Generic[Key]):
    """Lightweight LRU cache storing QIcon instances keyed by path."""

    def __init__(self, capacity: int = 256) -> None:
        self._capacity = capacity
        self._store: OrderedDict[Key, tuple[QIcon, QPixmap]] = OrderedDict()

    def get(self, key: Key) -> Optional[QIcon]:
        item = self._store.get(key)
        print(f"[ThumbnailCache] get key={key} -> found={'Y' if item else 'N'}", flush=True)
        if item is not None:
            self._store.move_to_end(key)
            return item[0]  # icon
        return None

    def put(self, key: Key, icon: QIcon, pixmap: QPixmap) -> None:
        print(f"[ThumbnailCache] put key={key} icon={'Y' if icon else 'N'}", flush=True)
        self._store[key] = (icon, pixmap) # タプルを保存
        self._store.move_to_end(key)
        while len(self._store) > self._capacity:
            self._store.popitem(last=False)

    def get_or_create(self, key: Key, factory: Callable[[], Optional[QIcon]]) -> Optional[QIcon]:
        icon = self.get(key)
        if icon is not None:
            return icon
        icon = factory()
        if icon is not None:
            self.put(key, icon)
        return icon

folder_preview_cache = ThumbnailCache[str](capacity=512)
file_thumbnail_cache = ThumbnailCache[str](capacity=512)