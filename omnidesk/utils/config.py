"""Helpers for loading and saving OmniDesk configuration."""

from __future__ import annotations

import json
import logging
import os
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

LEGACY_CONFIG_DIR = Path.home() / ".omnidesk"


def _default_config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "OmniDesk"
    return LEGACY_CONFIG_DIR


DEFAULT_CONFIG_DIR = _default_config_dir()
CONFIG_FILE = DEFAULT_CONFIG_DIR / "settings.json"
LEGACY_CONFIG_FILE = LEGACY_CONFIG_DIR / "settings.json"

logger = logging.getLogger(__name__)


class SessionSettings(TypedDict, total=False):
    tabs: list[str]
    pinned_tabs: list[bool]
    view_mode: str


class FileBrowserSettings(TypedDict, total=False):
    name_column_width: int


class SettingsData(TypedDict, total=False):
    session: SessionSettings
    file_browser: FileBrowserSettings


class SessionTabState(TypedDict):
    path: str
    pinned: bool


@dataclass
class AppSettings:
    """Typed wrapper around persisted settings data."""

    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: object) -> AppSettings:
        return cls(raw if isinstance(raw, dict) else {})

    def as_dict(self) -> dict[str, Any]:
        return self.data

    def session_tabs(self) -> list[str]:
        return [item["path"] for item in self.session_tab_states()]

    def session_pinned_tabs(self) -> list[bool]:
        return [item["pinned"] for item in self.session_tab_states()]

    def session_tab_states(self) -> list[SessionTabState]:
        session = self.data.get("session", {})
        tabs = session.get("tabs") if isinstance(session, dict) else None
        pinned = session.get("pinned_tabs") if isinstance(session, dict) else None
        if not isinstance(tabs, list):
            return []
        states: list[SessionTabState] = []
        for index, path in enumerate(tabs):
            if not isinstance(path, str):
                continue
            pinned_value = (
                pinned[index] if isinstance(pinned, list) and index < len(pinned) else False
            )
            states.append(
                {"path": path, "pinned": pinned_value if isinstance(pinned_value, bool) else False}
            )
        return states

    def view_mode(self) -> str | None:
        session = self.data.get("session", {})
        value = session.get("view_mode") if isinstance(session, dict) else None
        return value if isinstance(value, str) else None

    def name_column_width(self) -> int | None:
        file_browser = self.data.get("file_browser", {})
        value = file_browser.get("name_column_width") if isinstance(file_browser, dict) else None
        return value if isinstance(value, int) and value > 0 else None

    def set_name_column_width(self, width: int) -> bool:
        if width <= 0:
            return False
        file_browser = self.data.setdefault("file_browser", {})
        if not isinstance(file_browser, dict):
            file_browser = {}
            self.data["file_browser"] = file_browser
        if file_browser.get("name_column_width") == width:
            return False
        file_browser["name_column_width"] = width
        return True

    def video_thumbnail_timeout_ms(self) -> int:
        thumbnails = self.data.get("thumbnails", {})
        value = thumbnails.get("video_timeout_ms") if isinstance(thumbnails, dict) else None
        return value if isinstance(value, int) and 1000 <= value <= 30000 else 5000

    def set_session(
        self,
        *,
        tabs: list[str],
        pinned_tabs: list[bool] | None = None,
        view_mode: str,
    ) -> None:
        self.data["session"] = {
            "tabs": tabs,
            "pinned_tabs": pinned_tabs if pinned_tabs is not None else [False for _ in tabs],
            "view_mode": view_mode,
        }


def load_settings() -> dict[str, Any]:
    temp_file = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.tmp")
    if temp_file.exists():
        logger.warning("Removing stale settings temp file: %s", temp_file)
        with suppress(OSError):
            temp_file.unlink(missing_ok=True)
    source = CONFIG_FILE
    if not source.exists():
        if LEGACY_CONFIG_FILE != CONFIG_FILE and LEGACY_CONFIG_FILE.exists():
            source = LEGACY_CONFIG_FILE
        else:
            return {}
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load settings from %s", source)
        return {}


def save_settings(data: dict[str, Any]) -> None:
    temp_file = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.tmp")
    try:
        DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_file.replace(CONFIG_FILE)
    except OSError:
        logger.exception("Failed to save settings to %s", CONFIG_FILE)
        try:
            temp_file.unlink(missing_ok=True)
        except OSError:
            logger.debug("Failed to remove temporary settings file: %s", temp_file, exc_info=True)
