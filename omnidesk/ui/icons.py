"""Icon helpers used across OmniDesk UI components."""

from __future__ import annotations

from functools import lru_cache

from PyQt6.QtGui import QIcon

from ..utils.resources import application_icon_candidates


@lru_cache(maxsize=1)
def application_icon() -> QIcon:
    """Return a cached QIcon for the main application icon."""
    icon = QIcon()
    added = False
    for path in application_icon_candidates():
        if path.exists():
            icon.addFile(str(path))
            added = True
    return icon if added else QIcon()
