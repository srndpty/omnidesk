"""Filesystem related helper utilities."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def get_default_start_path() -> Path:
    """Return the initial directory to use when the app boots."""
    candidates = [Path.cwd(), Path.home() / "Desktop", Path.home()]
    for candidate in candidates:
        if candidate.exists():
            try:
                return candidate.resolve()
            except OSError:
                # Gracefully handle network drives or missing permissions
                return candidate
    return Path.home()


def resolve_for_navigation(value: str | Path) -> Path:
    """Normalise any user provided input into a navigable Path."""
    path = Path(value).expanduser()
    try:
        return path.resolve()
    except OSError:
        return path


def iter_available_roots() -> Iterable[Path]:
    """Yield Windows logical drives that are currently available."""
    roots: list[Path] = []
    for drive_letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{drive_letter}:/")
        if root.exists():
            roots.append(root)
    return roots
