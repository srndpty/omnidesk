"""Navigation helpers for file browser tabs."""

from __future__ import annotations

import os
from pathlib import Path


def navigation_target(path: Path) -> Path:
    """Return the directory that should become the current browser root."""
    return path if path.is_dir() else path.parent


def should_record_history(current: Path, destination: Path, *, from_history: bool) -> bool:
    """Return whether navigating to destination should append current to history."""
    if from_history:
        return False
    try:
        return current.resolve() != destination.resolve()
    except OSError:
        return current != destination


def path_to_focus_after_go_up(current_path: Path) -> tuple[Path, Path] | None:
    """Return parent and child-to-focus when going up, or None at filesystem root."""
    parent = current_path.parent
    if parent == current_path:
        return None
    return parent, current_path


def resolve_address_path(text: str, current_path: Path) -> Path:
    """Expand environment variables and resolve relative address-bar text."""
    expanded = os.path.expandvars(text.strip())
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = current_path / candidate
    return candidate
