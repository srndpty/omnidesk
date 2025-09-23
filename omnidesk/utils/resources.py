"""Utilities for resolving resource file paths."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Iterable


@lru_cache(maxsize=1)
def _resource_root() -> Path:
    """Return the directory that contains bundled resources."""
    if hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        for candidate in (base / "resources", base / "omnidesk" / "resources"):
            if candidate.exists():
                return candidate
        return base / "resources"
    return Path(__file__).resolve().parents[2] / "resources"


def resource_path(*parts: str | Path | Iterable[str | Path]) -> Path:
    """Return a path under the resources directory."""
    if len(parts) == 1 and isinstance(parts[0], Iterable) and not isinstance(parts[0], (str, bytes, Path)):
        sequence = parts[0]
    else:
        sequence = parts
    return _resource_root() / Path(*sequence)


def application_icon_candidates() -> tuple[Path, ...]:
    """Return candidate icon paths ordered by preference for the platform."""
    ico = resource_path("icons", "app_icon.ico")
    png = resource_path("icons", "app_icon.png")
    if sys.platform.startswith("win"):
        return (ico, png)
    return (png, ico)


def application_icon_path() -> Path:
    """Return the highest priority application icon path that exists."""
    for candidate in application_icon_candidates():
        if candidate.exists():
            return candidate
    return resource_path("icons", "app_icon.png")
