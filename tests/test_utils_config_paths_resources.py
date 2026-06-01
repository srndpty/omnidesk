from __future__ import annotations

import sys
from pathlib import Path

from omnidesk.utils import config, paths, resources


def test_load_settings_returns_empty_when_file_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(config, "LEGACY_CONFIG_FILE", tmp_path / "legacy-missing.json")

    assert config.load_settings() == {}


def test_load_settings_reads_valid_json(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"window": {"width": 1200}}', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)

    assert config.load_settings() == {"window": {"width": 1200}}


def test_load_settings_falls_back_to_legacy_file(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    legacy_file = tmp_path / "legacy.json"
    legacy_file.write_text('{"legacy": true}', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    monkeypatch.setattr(config, "LEGACY_CONFIG_FILE", legacy_file)

    assert config.load_settings() == {"legacy": True}


def test_load_settings_returns_empty_for_invalid_json(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)

    assert config.load_settings() == {}


def test_load_settings_returns_empty_for_read_error(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    monkeypatch.setattr(
        Path, "read_text", lambda self, encoding=None: (_ for _ in ()).throw(OSError())
    )

    assert config.load_settings() == {}


def test_save_settings_creates_config_file(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)

    config.save_settings({"tabs": ["C:/tmp"]})

    assert settings_file.read_text(encoding="utf-8") == '{\n  "tabs": [\n    "C:/tmp"\n  ]\n}'
    assert not (tmp_path / "settings.json.tmp").exists()


def test_save_settings_keeps_existing_file_when_temp_write_fails(
    monkeypatch, tmp_path: Path
) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"existing": true}', encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)

    original_write_text = Path.write_text

    def fail_temp_write(self: Path, *args, **kwargs):
        if self.name.endswith(".tmp"):
            raise OSError()
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_temp_write)

    config.save_settings({"ignored": True})

    assert settings_file.read_text(encoding="utf-8") == '{"existing": true}'


def test_save_settings_ignores_os_errors(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    monkeypatch.setattr(
        Path, "write_text", lambda self, *args, **kwargs: (_ for _ in ()).throw(OSError())
    )

    config.save_settings({"ignored": True})

    assert not settings_file.exists()


def test_app_settings_typed_wrapper_reads_and_updates_values() -> None:
    settings = config.AppSettings.from_raw(
        {
            "file_browser": {"name_column_width": 320},
            "session": {
                "tabs": ["C:/one", 123, "C:/two"],
                "pinned_tabs": [True, "bad", False],
                "view_mode": "columns",
            },
        }
    )

    assert settings.name_column_width() == 320
    assert settings.session_tabs() == ["C:/one", "C:/two"]
    assert settings.session_pinned_tabs() == [True, False]
    assert settings.session_tab_states() == [
        {"path": "C:/one", "pinned": True},
        {"path": "C:/two", "pinned": False},
    ]
    assert settings.view_mode() == "columns"
    assert settings.set_name_column_width(640)
    assert not settings.set_name_column_width(640)

    settings.set_session(tabs=["C:/three"], pinned_tabs=[True], view_mode="tabs")

    assert settings.as_dict()["file_browser"]["name_column_width"] == 640
    assert settings.as_dict()["session"] == {
        "tabs": ["C:/three"],
        "pinned_tabs": [True],
        "view_mode": "tabs",
    }


def test_app_settings_ignores_invalid_raw_values() -> None:
    settings = config.AppSettings.from_raw(
        {
            "file_browser": {"name_column_width": -1},
            "session": {"tabs": "not-a-list", "view_mode": 1},
        }
    )

    assert settings.name_column_width() is None
    assert settings.session_tabs() == []
    assert settings.session_pinned_tabs() == []
    assert settings.view_mode() is None


def test_app_settings_preserves_pin_positions_when_tabs_contain_invalid_items() -> None:
    settings = config.AppSettings.from_raw(
        {
            "session": {
                "tabs": ["C:/one", 123, "C:/two"],
                "pinned_tabs": [False, True, False],
            },
        }
    )

    assert settings.session_tab_states() == [
        {"path": "C:/one", "pinned": False},
        {"path": "C:/two", "pinned": False},
    ]
    assert settings.session_tabs() == ["C:/one", "C:/two"]
    assert settings.session_pinned_tabs() == [False, False]


def test_app_settings_video_thumbnail_timeout_ms_boundaries() -> None:
    def _settings(value: object) -> config.AppSettings:
        return config.AppSettings.from_raw({"thumbnails": {"video_timeout_ms": value}})

    assert _settings(None).video_thumbnail_timeout_ms() == 5000
    assert config.AppSettings().video_thumbnail_timeout_ms() == 5000
    assert _settings(999).video_thumbnail_timeout_ms() == 5000
    assert _settings(1000).video_thumbnail_timeout_ms() == 1000
    assert _settings(5000).video_thumbnail_timeout_ms() == 5000
    assert _settings(30000).video_thumbnail_timeout_ms() == 30000
    assert _settings(30001).video_thumbnail_timeout_ms() == 5000
    assert _settings("5000").video_thumbnail_timeout_ms() == 5000


def test_resolve_for_navigation_resolves_existing_path(tmp_path: Path) -> None:
    target = tmp_path / "folder"
    target.mkdir()

    assert paths.resolve_for_navigation(target) == target.resolve()


def test_resolve_for_navigation_returns_unresolved_path_on_os_error(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "unavailable"
    monkeypatch.setattr(Path, "resolve", lambda self: (_ for _ in ()).throw(OSError()))

    assert paths.resolve_for_navigation(target) == target


def test_get_default_start_path_prefers_current_directory(monkeypatch, tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.setattr(Path, "cwd", lambda: cwd)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    assert paths.get_default_start_path() == cwd.resolve()


def test_iter_available_roots_uses_existing_drive_letters(monkeypatch) -> None:
    def exists(self: Path) -> bool:
        return str(self).replace("\\", "/") in {"C:/", "Z:/"}

    monkeypatch.setattr(Path, "exists", exists)

    assert list(paths.iter_available_roots()) == [Path("C:/"), Path("Z:/")]


def test_resource_path_accepts_varargs_and_iterable() -> None:
    resources._resource_root.cache_clear()

    assert resources.resource_path("icons", "app_icon.png").name == "app_icon.png"
    assert resources.resource_path(["icons", "app_icon.ico"]).name == "app_icon.ico"


def test_resource_root_uses_meipass_resources(monkeypatch, tmp_path: Path) -> None:
    bundle_resources = tmp_path / "resources"
    bundle_resources.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    resources._resource_root.cache_clear()

    try:
        assert resources._resource_root() == bundle_resources
    finally:
        resources._resource_root.cache_clear()
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)


def test_resource_root_uses_nested_meipass_resources(monkeypatch, tmp_path: Path) -> None:
    nested_resources = tmp_path / "omnidesk" / "resources"
    nested_resources.mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    resources._resource_root.cache_clear()

    try:
        assert resources._resource_root() == nested_resources
    finally:
        resources._resource_root.cache_clear()
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)


def test_application_icon_candidates_prefer_platform_extension(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(resources, "_resource_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")

    candidates = resources.application_icon_candidates()

    assert candidates[0].suffix == ".png"
    assert candidates[1].suffix == ".ico"


def test_application_icon_path_falls_back_to_png(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(resources, "_resource_root", lambda: tmp_path)

    assert resources.application_icon_path() == tmp_path / "icons" / "app_icon.png"
