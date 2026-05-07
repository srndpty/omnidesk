from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtGui import QIcon, QPixmap

from omnidesk.utils.thumbnail_cache import PersistentThumbnailCache, ThumbnailCache, _stable_hash


def _pixmap(size: int = 16) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill()
    return pixmap


def test_stable_hash_is_deterministic() -> None:
    assert _stable_hash("same") == _stable_hash("same")
    assert _stable_hash("same") != _stable_hash("different")


def test_thumbnail_cache_lru_eviction() -> None:
    cache = ThumbnailCache[str](capacity=2)
    pixmap = _pixmap()
    icon = QIcon(pixmap)

    cache.put("one", icon, pixmap)
    cache.put("two", icon, pixmap)
    assert cache.get("one") is icon

    cache.put("three", icon, pixmap)

    assert cache.get("two") is None
    assert cache.get("one") is icon
    assert cache.get("three") is icon


def test_get_or_create_returns_factory_icon_without_storing() -> None:
    cache = ThumbnailCache[str]()
    pixmap = _pixmap()
    icon = QIcon(pixmap)

    assert cache.get_or_create("missing", lambda: icon) is icon
    assert cache.get("missing") is None


def test_persistent_cache_stores_and_loads_from_disk(tmp_path: Path) -> None:
    cache = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    key_path = tmp_path / "source.png"
    key_path.write_text("source", encoding="utf-8")
    pixmap = _pixmap()
    icon = QIcon(pixmap)

    cache.put(str(key_path), icon, pixmap)
    disk_path = cache.disk_path(str(key_path))

    assert disk_path.exists()
    assert cache.get_memory(str(key_path)) is icon

    reloaded = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    loaded_icon = reloaded.get(str(key_path))

    assert loaded_icon is not None
    assert not loaded_icon.isNull()


def test_persistent_cache_enforces_disk_item_budget(tmp_path: Path) -> None:
    cache = PersistentThumbnailCache[str](
        capacity=1,
        namespace="budget",
        root=tmp_path,
        disk_max_items=1,
        disk_max_bytes=1_000_000,
    )
    root = tmp_path / "budget"
    old_file = root / "old.png"
    new_file = root / "new.png"
    old_file.write_bytes(b"old")
    new_file.write_bytes(b"new")
    os.utime(old_file, (1, 1))
    os.utime(new_file, (2, 2))

    cache.enforce_disk_budget()

    assert not old_file.exists()
    assert new_file.exists()


def test_clear_disk_removes_png_files_only(tmp_path: Path) -> None:
    cache = PersistentThumbnailCache[str](capacity=1, namespace="clear", root=tmp_path)
    root = tmp_path / "clear"
    png = root / "cached.png"
    txt = root / "keep.txt"
    png.write_bytes(b"png")
    txt.write_text("keep", encoding="utf-8")

    cache.clear_disk()

    assert not png.exists()
    assert txt.exists()
