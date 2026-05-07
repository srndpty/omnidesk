from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from omnidesk import app as app_module
from omnidesk.theme import DARK_STYLESHEET, apply_dark_theme
from omnidesk.ui.icons import application_icon
from omnidesk.ui.media_file_system_model import MediaFileSystemModel


def test_apply_dark_theme_sets_fusion_style_and_stylesheet(qapp: QApplication) -> None:
    apply_dark_theme(qapp)

    assert qapp.style() is not None
    assert qapp.styleSheet() == DARK_STYLESHEET


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
    assert model._normalise_key(target).endswith("target.txt")
    assert model.supportedDropActions() == Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    assert model.supportedDragActions() == Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
