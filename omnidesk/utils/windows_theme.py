"""Windows-only helper to force the dark title bar."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PyQt6.QtWidgets import QWidget

_DARK_MODE_ATTRIBUTES: tuple[int, ...] = (20, 19)


def apply_dark_title_bar(widget: QWidget) -> None:
    """Force the Windows title bar into dark mode when available."""
    if sys.platform != "win32":
        return

    hwnd = widget.winId()
    if hwnd is None:
        return

    _set_dark_mode_for_hwnd(int(hwnd))


def _set_dark_mode_for_hwnd(hwnd: int) -> None:
    """Try DWM attributes that switch the title bar to dark mode."""
    value = ctypes.c_int(1)
    dwmapi = getattr(ctypes.windll, "dwmapi", None)
    if dwmapi is None:
        return

    for attribute in _DARK_MODE_ATTRIBUTES:
        result = dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(attribute),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        if result == 0:
            return
