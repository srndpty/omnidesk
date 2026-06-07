from __future__ import annotations

import os
from pathlib import Path

import pytest
from PyQt6.QtGui import QIcon, QPixmap

from omnidesk.utils import thumbnail_cache as cache_module
from omnidesk.utils.thumbnail_cache import PersistentThumbnailCache, ThumbnailCache, _stable_hash

pytestmark = pytest.mark.usefixtures("qapp")


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


def test_thumbnail_cache_discards_memory_item() -> None:
    cache = ThumbnailCache[str]()
    pixmap = _pixmap()
    icon = QIcon(pixmap)

    cache.put("stale", icon, pixmap)
    cache.discard_memory("stale")

    assert cache.get("stale") is None


def test_persistent_cache_stores_and_loads_from_disk(tmp_path: Path) -> None:
    cache = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    key_path = tmp_path / "source.png"
    key_path.write_text("source", encoding="utf-8")
    pixmap = _pixmap()
    icon = QIcon(pixmap)

    cache.put(str(key_path), icon, pixmap, hint_edge=16)
    disk_path = cache.disk_path(str(key_path), hint_edge=16)

    assert disk_path.exists()
    assert cache.get_memory(str(key_path)) is icon

    reloaded = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    loaded_icon = reloaded.get(str(key_path), hint_edge=16)

    assert loaded_icon is not None
    assert not loaded_icon.isNull()


def test_persistent_cache_disk_path_includes_edge(tmp_path: Path) -> None:
    cache = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    key_path = tmp_path / "source.png"
    key_path.write_text("source", encoding="utf-8")

    small_path = cache.disk_path(str(key_path), hint_edge=96)
    large_path = cache.disk_path(str(key_path), hint_edge=160)

    assert small_path != large_path


def test_persistent_cache_memory_miss_when_item_is_smaller_than_requested(
    tmp_path: Path,
) -> None:
    cache = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    key = "image-key"
    pixmap = _pixmap(96)
    icon = QIcon(pixmap)

    cache.put_memory(key, icon, pixmap)

    assert cache.get_memory(key, min_edge=96) is icon
    assert cache.get_memory(key, min_edge=160) is None


def test_persistent_cache_get_respects_hint_edge_for_memory(tmp_path: Path) -> None:
    cache = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    key = "image-key"
    pixmap = _pixmap(96)
    icon = QIcon(pixmap)

    cache.put_memory(key, icon, pixmap)

    assert cache.get(key, hint_edge=96) is icon
    assert cache.get(key, hint_edge=160) is None


def test_persistent_cache_put_uses_explicit_hint_edge_for_disk_key(tmp_path: Path) -> None:
    cache = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    key_path = tmp_path / "source.png"
    key_path.write_text("source", encoding="utf-8")
    pixmap = _pixmap(96)
    icon = QIcon(pixmap)

    cache.put(str(key_path), icon, pixmap, hint_edge=160)

    assert cache.disk_path(str(key_path), hint_edge=160).exists()
    assert not cache.disk_path(str(key_path), hint_edge=96).exists()


def test_persistent_cache_put_reports_qsavefile_open_failure(
    monkeypatch,
    caplog,
    tmp_path: Path,
) -> None:
    class FailingSaveFile:
        class OpenModeFlag:
            WriteOnly = object()

        def __init__(self, _path: str) -> None:
            pass

        def open(self, _mode: object) -> bool:
            return False

    cache = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    pixmap = _pixmap()
    icon = QIcon(pixmap)
    monkeypatch.setattr(cache_module, "QSaveFile", FailingSaveFile)

    with caplog.at_level("WARNING"):
        cache.put("key", icon, pixmap, hint_edge=16)

    assert "Failed to open thumbnail cache file for writing" in caplog.text


def test_persistent_cache_discards_single_disk_entry(tmp_path: Path) -> None:
    cache = PersistentThumbnailCache[str](capacity=1, namespace="files", root=tmp_path)
    key_path = tmp_path / "source.png"
    key_path.write_text("source", encoding="utf-8")
    pixmap = _pixmap(96)
    icon = QIcon(pixmap)

    cache.put(str(key_path), icon, pixmap, hint_edge=160)
    cache.discard_disk(str(key_path), hint_edge=160)

    assert not cache.disk_path(str(key_path), hint_edge=160).exists()


def test_persistent_cache_discards_all_known_and_hint_edge_entries(tmp_path: Path) -> None:
    cache = PersistentThumbnailCache[str](capacity=1, namespace="folders", root=tmp_path)
    key_path = tmp_path / "folder"
    key_path.mkdir()
    pixmap = _pixmap(96)
    icon = QIcon(pixmap)

    cache.put(str(key_path), icon, pixmap, hint_edge=96)
    cache.put(str(key_path), icon, pixmap, hint_edge=160)
    extra_path = cache.disk_path(str(key_path), hint_edge=192)
    extra_path.write_bytes(b"extra")

    cache.discard_disk_all_sizes(str(key_path), hint_edges={96, 160})

    assert not cache.disk_path(str(key_path), hint_edge=96).exists()
    assert not cache.disk_path(str(key_path), hint_edge=160).exists()
    assert not extra_path.exists()


def test_thumbnail_cache_get_sized_ignores_null_pixmap() -> None:
    cache = ThumbnailCache[str]()
    icon = QIcon()

    cache.put("null", icon, QPixmap())

    assert cache.get_sized("null") is None


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
