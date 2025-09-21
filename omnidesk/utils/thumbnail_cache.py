"""Simple in-memory thumbnail cache with LRU eviction."""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Generic, Optional, TypeVar

from PyQt6.QtGui import QIcon

Key = TypeVar("Key")


class ThumbnailCache(Generic[Key]):
    """Lightweight LRU cache storing QIcon instances keyed by path."""

    def __init__(self, capacity: int = 256) -> None:
        self._capacity = capacity
        self._store: OrderedDict[Key, QIcon] = OrderedDict()

    def get(self, key: Key) -> Optional[QIcon]:
        icon = self._store.get(key)
        if icon is not None:
            self._store.move_to_end(key)
        return icon

    def put(self, key: Key, icon: QIcon) -> None:
        self._store[key] = icon
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


thumbnail_cache = ThumbnailCache[str]()
