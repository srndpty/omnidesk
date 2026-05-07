"""Helpers for loading and saving OmniDesk configuration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

DEFAULT_CONFIG_DIR = Path.home() / ".omnidesk"
CONFIG_FILE = DEFAULT_CONFIG_DIR / "settings.json"

logger = logging.getLogger(__name__)


class SessionSettings(TypedDict, total=False):
    tabs: list[str]
    view_mode: str


class FileBrowserSettings(TypedDict, total=False):
    name_column_width: int


class SettingsData(TypedDict, total=False):
    session: SessionSettings
    file_browser: FileBrowserSettings


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
        session = self.data.get("session", {})
        tabs = session.get("tabs") if isinstance(session, dict) else None
        return [item for item in tabs if isinstance(item, str)] if isinstance(tabs, list) else []

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

    def set_session(self, *, tabs: list[str], view_mode: str) -> None:
        self.data["session"] = {"tabs": tabs, "view_mode": view_mode}


def load_settings() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load settings from %s", CONFIG_FILE)
        return {}


def save_settings(data: dict[str, Any]) -> None:
    try:
        DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        logger.exception("Failed to save settings to %s", CONFIG_FILE)
