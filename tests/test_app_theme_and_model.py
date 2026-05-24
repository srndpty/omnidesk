from __future__ import annotations

from pathlib import Path
from typing import cast

from PyQt6.QtCore import QMimeData, QSize, Qt, QUrl
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import QApplication

import omnidesk.ui.media_file_system_model as model_module
from omnidesk import app as app_module
from omnidesk.theme import DARK_STYLESHEET, apply_dark_theme
from omnidesk.ui.icons import application_icon
from omnidesk.ui.media_file_system_model import (
    MediaFileSystemModel,
    file_thumbnail_cache,
    folder_base_pixmap,
    folder_preview_cache,
    folder_thumbnail_rect,
)


def test_apply_dark_theme_sets_fusion_style_and_stylesheet(qapp: QApplication) -> None:
    apply_dark_theme(qapp)

    assert qapp.style() is not None
    assert qapp.styleSheet() == DARK_STYLESHEET


def test_dark_theme_styles_disabled_menu_items() -> None:
    assert "QMenu::item:disabled" in DARK_STYLESHEET
    assert "color: #6f7680;" in DARK_STYLESHEET


def test_dark_theme_scrollbar_handles_have_minimum_extent() -> None:
    assert "QScrollBar::handle:vertical" in DARK_STYLESHEET
    assert "min-height: 36px;" in DARK_STYLESHEET
    assert "QScrollBar::handle:horizontal" in DARK_STYLESHEET
    assert "min-width: 36px;" in DARK_STYLESHEET


def test_create_app_reuses_existing_application(monkeypatch, qapp: QApplication) -> None:
    monkeypatch.setattr(app_module, "application_icon", lambda: QIcon())

    created = app_module.create_app(["omnidesk-test"])

    assert created is qapp
    assert created.applicationName() == "OmniDesk"
    assert created.organizationName() == "OmniDesk"


def test_application_icon_returns_icon_when_candidate_exists(monkeypatch, tmp_path: Path) -> None:
    icon_file = tmp_path / "icon.png"
    icon_file.write_bytes(
        bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
            "0000000A49444154789C63600000020001E221BC330000000049454E44AE426082"
        )
    )
    application_icon.cache_clear()
    monkeypatch.setattr("omnidesk.ui.icons.application_icon_candidates", lambda: (icon_file,))

    try:
        assert not application_icon().isNull()
    finally:
        application_icon.cache_clear()


def test_media_file_system_model_small_helpers(tmp_path: Path) -> None:
    model = MediaFileSystemModel()

    model.set_thumbnail_edge(4)
    assert model._thumbnail_edge == 16

    target = tmp_path / "target.txt"
    supported_actions = (
        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.TargetMoveAction
    )
    assert model._normalise_key(target).endswith("target.txt")
    assert model.supportedDropActions() == supported_actions
    assert model.supportedDragActions() == supported_actions


def test_media_file_system_model_token_and_cancel_state(monkeypatch) -> None:
    model = MediaFileSystemModel()
    cancelled: list[str] = []
    monkeypatch.setattr(model._provider, "cancel_thumbnail", lambda key: cancelled.append(key))

    token = model._new_token("key")
    model._visible_keys.add("key")
    model._pending.add("key")
    model._folder_scans["key"] = cast(model_module.FolderScanJob, object())
    model._cache_jobs["key"] = cast(model_module.CacheLoadJob, object())

    assert model._is_current_request("key", token.generation)
    assert model._request_edges["key"] == model._thumbnail_edge

    model.clear_visible_thumbnail_targets()

    assert token.cancelled
    assert cancelled == ["key"]
    assert model._visible_keys == set()
    assert "key" not in model._pending
    assert "key" not in model._folder_scans
    assert "key" not in model._cache_jobs
    assert "key" not in model._request_edges


def test_media_file_system_model_cache_for_info_and_rejections(tmp_path: Path) -> None:
    model = MediaFileSystemModel()
    model._thumbnail_edge = 32

    assert model._cache_for_info(True) is folder_preview_cache
    assert model._cache_for_info(False) is file_thumbnail_cache
    assert not model._request_visible_key(str(tmp_path), tmp_path, is_dir=True)

    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("text", encoding="utf-8")
    assert not model._request_visible_key(str(unsupported), unsupported, is_dir=False)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")
    model._provider._video_support = False
    model._ensure_thumbnail(video, ".mp4", str(video))

    assert str(video) in model._failed


def test_media_file_system_model_folder_scan_result_branches(monkeypatch, tmp_path: Path) -> None:
    model = MediaFileSystemModel()
    key = str(tmp_path)
    image_path = tmp_path / "image.jpg"
    image_path.write_text("fake", encoding="utf-8")
    started: list[tuple[str, Path, int]] = []
    monkeypatch.setattr(
        model._provider,
        "request_thumbnail",
        lambda path, edge, result_key=None, token=None: started.append(
            (cast(str, result_key), path, edge)
        )
        or True,
    )

    model._handle_folder_scan_result(key, generation=99, image_path=image_path)
    assert started == []

    token = model._new_token(key)
    model.set_thumbnail_edge(160)
    model._visible_keys.add(key)
    model._pending.add(key)
    model._handle_folder_scan_result(key, token.generation, image_path)
    assert started == [(key, image_path, 96)]

    empty_key = str(tmp_path / "empty")
    token = model._new_token(empty_key)
    model._visible_keys.add(empty_key)
    model._pending.add(empty_key)
    model._handle_folder_scan_result(empty_key, token.generation, None)
    assert empty_key in model._failed
    assert empty_key not in model._pending
    assert empty_key not in model._request_edges


def test_media_file_system_model_cache_loaded_branches(monkeypatch, tmp_path: Path) -> None:
    model = MediaFileSystemModel()
    key = str(tmp_path / "image.png")
    started: list[str] = []
    monkeypatch.setattr(
        model, "_ensure_thumbnail", lambda path, suffix, key=None: started.append(str(path))
    )
    monkeypatch.setattr(
        model, "_emit_thumbnail_changed", lambda changed_key: started.append(f"emit:{changed_key}")
    )

    model._handle_cache_loaded(key, generation=99, image=None, is_dir=False)
    assert started == []

    token = model._new_token(key)
    model._visible_keys.add(key)
    model._pending.add(key)
    model._tokens[key] = token
    model._handle_cache_loaded(key, token.generation, QImage(), is_dir=False)
    assert started == [key]
    assert key not in model._pending

    image = QImage(10, 10, QImage.Format.Format_RGB32)
    image.fill(0xFF00FF)
    token = model._new_token(key)
    model._visible_keys.add(key)
    model._pending.add(key)
    model._tokens[key] = token
    model._handle_cache_loaded(key, token.generation, image, is_dir=False)
    assert f"emit:{key}" in started


def test_media_file_system_model_thumbnail_ready_failure_and_file_success(
    monkeypatch, tmp_path: Path
) -> None:
    model = MediaFileSystemModel()
    key = str(tmp_path / "image.png")
    emitted: list[str] = []
    monkeypatch.setattr(model, "_save_cache_async", lambda cache, key, pixmap, **kwargs: None)
    monkeypatch.setattr(
        model, "_emit_thumbnail_changed", lambda changed_key: emitted.append(changed_key)
    )

    token = model._new_token(key)
    model._visible_keys.add(key)
    model._pending.add(key)
    model._handle_thumbnail_ready(key, None, token.generation)
    assert key in model._failed

    pixmap = QPixmap(16, 16)
    pixmap.fill()
    icon = QIcon(pixmap)
    token = model._new_token(key)
    model._visible_keys.add(key)
    model._pending.add(key)
    model._failed.add(key)
    model._handle_thumbnail_ready(key, icon, token.generation)

    assert key not in model._failed
    assert emitted == [key]


def test_media_file_system_model_thumbnail_ready_saves_with_request_edge(
    monkeypatch, tmp_path: Path
) -> None:
    model = MediaFileSystemModel()
    key = str(tmp_path / "image.png")
    saved_edges: list[int | None] = []
    requested_edges: list[int] = []
    monkeypatch.setattr(
        model,
        "_save_cache_async",
        lambda cache, key, pixmap, **kwargs: saved_edges.append(kwargs.get("hint_edge")),
    )
    monkeypatch.setattr(
        model._provider,
        "request_thumbnail",
        lambda path, edge, result_key=None, token=None: requested_edges.append(edge) or True,
    )
    monkeypatch.setattr(model, "_emit_thumbnail_changed", lambda changed_key: None)

    pixmap = QPixmap(96, 96)
    pixmap.fill()
    icon = QIcon(pixmap)
    token = model._new_token(key)
    model.set_thumbnail_edge(160)
    model._visible_keys.add(key)
    model._pending.add(key)

    model._handle_thumbnail_ready(key, icon, token.generation)

    assert saved_edges == [96]
    assert requested_edges == [160]
    assert model._request_edges[key] == 160
    assert key in model._pending


def test_media_file_system_model_stale_folder_edge_respects_scroll_throttle(
    monkeypatch, tmp_path: Path
) -> None:
    model = MediaFileSystemModel()
    key = model._normalise_key(tmp_path)
    requested: list[tuple[str, Path, bool]] = []
    monkeypatch.setattr(
        model,
        "_request_visible_key",
        lambda key, path, is_dir: requested.append((key, path, is_dir)) or True,
    )

    model._visible_keys.add(key)
    model.set_thumbnail_edge(160)
    model._allow_folder_preview_for_visible_targets = False

    model._request_current_edge_if_needed(key, tmp_path, is_dir=True, completed_edge=96)

    assert requested == []


def test_media_file_system_model_drop_mime_data_rejects_invalid_inputs(tmp_path: Path) -> None:
    model = MediaFileSystemModel()
    data = QMimeData()

    assert model.dropMimeData(data, Qt.DropAction.IgnoreAction, 0, 0, model.index("")) is True
    assert model.dropMimeData(data, Qt.DropAction.MoveAction, 0, 0, model.index("")) is False

    data.setUrls([QUrl.fromLocalFile(str(tmp_path / "source.txt"))])
    assert model.dropMimeData(data, Qt.DropAction.MoveAction, 0, 0, model.index("")) is False


def test_media_file_system_model_drop_mime_data_moves_file(monkeypatch, tmp_path: Path) -> None:
    model = MediaFileSystemModel()
    source = tmp_path / "source.txt"
    dest = tmp_path / "dest"
    source.write_text("move", encoding="utf-8")
    dest.mkdir()
    parent = model.index(str(dest))
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(source))])

    monkeypatch.setattr(model, "isDir", lambda index: True)
    monkeypatch.setattr(model, "filePath", lambda index: str(dest))

    assert model.dropMimeData(data, Qt.DropAction.MoveAction, 0, 0, parent)
    assert not source.exists()
    assert (dest / "source.txt").read_text(encoding="utf-8") == "move"


def test_media_file_system_model_can_drop_urls_on_directory(monkeypatch, tmp_path: Path) -> None:
    model = MediaFileSystemModel()
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    parent = model.index(str(dest))
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(str(source))])

    monkeypatch.setattr(model, "isDir", lambda index: True)

    assert model.canDropMimeData(data, Qt.DropAction.MoveAction, 0, 0, parent)
    assert model.canDropMimeData(data, Qt.DropAction.TargetMoveAction, 0, 0, parent)


def test_folder_thumbnail_rect_centers_and_offsets_preview() -> None:
    assert folder_thumbnail_rect(QSize(160, 160), QSize(80, 60), 160) == (40, 42)


def test_folder_base_pixmap_normalizes_small_theme_icon_to_requested_edge() -> None:
    source = QPixmap(32, 32)
    source.fill()
    icon = QIcon(source)

    pixmap = folder_base_pixmap(icon, 160)

    assert pixmap.size() == QSize(160, 160)
    assert not pixmap.isNull()


def test_media_file_system_model_flags_for_invalid_and_directory(monkeypatch) -> None:
    model = MediaFileSystemModel()
    invalid_flags = model.flags(model.index(""))

    monkeypatch.setattr(model, "isDir", lambda index: True)
    directory_flags = model.flags(model.index("C:/"))

    assert directory_flags & Qt.ItemFlag.ItemIsDragEnabled
    assert directory_flags & Qt.ItemFlag.ItemIsDropEnabled
    assert invalid_flags is not None


class _FakeCache:
    def __init__(self, disk_path: Path, memory_icon: QIcon | None = None) -> None:
        self._disk_path = disk_path
        self._memory_icon = memory_icon
        self.memory_puts: list[tuple[str, QIcon, QPixmap]] = []

    def get_memory(self, _key: str, *, min_edge: int | None = None) -> QIcon | None:
        return self._memory_icon

    def disk_path(self, _key: str, *, hint_edge: int | None = None) -> Path:
        return self._disk_path

    def put_memory(self, key: str, icon: QIcon, pixmap: QPixmap) -> None:
        self.memory_puts.append((key, icon, pixmap))


def test_media_file_system_model_request_visible_loads_disk_cache(
    monkeypatch, tmp_path: Path
) -> None:
    model = MediaFileSystemModel()
    disk_cache = tmp_path / "cache.png"
    disk_cache.write_bytes(b"cache")
    fake_cache = _FakeCache(disk_cache)
    started: list[object] = []
    monkeypatch.setattr(model, "_cache_for_info", lambda is_dir: fake_cache)
    monkeypatch.setattr(model._scan_pool, "start", started.append)

    assert model._request_visible_key("cache-key", tmp_path / "image.png", is_dir=False)

    assert "cache-key" in model._pending
    assert "cache-key" in model._cache_jobs
    assert len(started) == 1


def test_media_file_system_model_request_visible_skips_small_folder_preview(tmp_path: Path) -> None:
    model = MediaFileSystemModel()
    model.set_thumbnail_edge(64)

    assert not model._request_visible_key(str(tmp_path), tmp_path, is_dir=True)
    assert str(tmp_path) not in model._pending


def test_media_file_system_model_request_visible_starts_folder_scan(
    monkeypatch, tmp_path: Path
) -> None:
    model = MediaFileSystemModel()
    started: list[Path] = []
    monkeypatch.setattr(model, "_ensure_folder_thumbnail", started.append)

    assert model._request_visible_key(str(tmp_path), tmp_path, is_dir=True)

    assert started == [tmp_path]


def test_media_file_system_model_ensure_folder_thumbnail_ignores_duplicate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    model = MediaFileSystemModel()
    started: list[object] = []
    monkeypatch.setattr(model._scan_pool, "start", started.append)

    model._ensure_folder_thumbnail(tmp_path)
    model._ensure_folder_thumbnail(tmp_path)

    key = model._normalise_key(tmp_path)
    assert key in model._pending
    assert key in model._folder_scans
    assert len(started) == 1


def test_media_file_system_model_ensure_thumbnail_skips_memory_pending_failed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    model = MediaFileSystemModel()
    key = str(tmp_path / "image.png")
    pixmap = QPixmap(16, 16)
    icon = QIcon(pixmap)
    fake_cache = _FakeCache(tmp_path / "unused.png", memory_icon=icon)
    requested: list[str] = []
    monkeypatch.setattr(model_module, "file_thumbnail_cache", fake_cache)
    monkeypatch.setattr(
        model._provider,
        "request_thumbnail",
        lambda path, edge, result_key=None, token=None: requested.append(cast(str, result_key))
        or True,
    )

    model._ensure_thumbnail(Path(key), ".png", key)
    model._pending.add(key)
    model._ensure_thumbnail(Path(key), ".png", key)
    model._pending.discard(key)
    model._failed.add(key)
    model._ensure_thumbnail(Path(key), ".png", key)

    assert requested == []


def test_media_file_system_model_ensure_thumbnail_marks_not_started(
    monkeypatch,
    tmp_path: Path,
) -> None:
    model = MediaFileSystemModel()
    key = str(tmp_path / "image.png")
    monkeypatch.setattr(
        model._provider,
        "request_thumbnail",
        lambda path, edge, result_key=None, token=None: False,
    )

    model._ensure_thumbnail(Path(key), ".png", key)

    assert key not in model._pending
    assert key in model._tokens


def test_media_file_system_model_cache_loaded_dir_miss_restarts_folder_scan(
    monkeypatch,
    tmp_path: Path,
) -> None:
    model = MediaFileSystemModel()
    key = str(tmp_path)
    token = model._new_token(key)
    model._visible_keys.add(key)
    model._pending.add(key)
    restarted: list[Path] = []
    monkeypatch.setattr(model, "_ensure_folder_thumbnail", restarted.append)

    model._handle_cache_loaded(key, token.generation, QImage(), is_dir=True)

    assert restarted == [tmp_path]
    assert key not in model._pending


def test_media_file_system_model_save_cache_async_ignores_null_pixmap(monkeypatch) -> None:
    model = MediaFileSystemModel()
    started: list[object] = []
    monkeypatch.setattr(model._scan_pool, "start", started.append)

    model._save_cache_async(file_thumbnail_cache, "key", QPixmap())

    assert started == []
