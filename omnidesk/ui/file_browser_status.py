"""Status summary helpers for file browser views."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserStatus:
    total_count: int = 0
    folder_count: int = 0
    file_count: int = 0
    selected_count: int = 0
    selected_file_size: int = 0


def directory_item_counts(path: Path) -> tuple[int, int]:
    """Return direct child folder and file counts for a directory."""
    folder_count = 0
    file_count = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0, 0
    for child in children:
        try:
            if child.is_dir():
                folder_count += 1
            elif child.is_file():
                file_count += 1
        except OSError:
            continue
    return folder_count, file_count


def selection_file_size(paths: list[Path]) -> int:
    """Return the total size for selected files, ignoring directories."""
    total = 0
    for path in paths:
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def browser_status_for(path: Path, selected_paths: list[Path] | None = None) -> BrowserStatus:
    selected = selected_paths or []
    folder_count, file_count = directory_item_counts(path)
    return browser_status_from_counts(folder_count, file_count, selected)


def browser_status_from_counts(
    folder_count: int,
    file_count: int,
    selected_paths: list[Path] | None = None,
) -> BrowserStatus:
    selected = selected_paths or []
    return BrowserStatus(
        total_count=folder_count + file_count,
        folder_count=folder_count,
        file_count=file_count,
        selected_count=len(selected),
        selected_file_size=selection_file_size(selected),
    )


def format_size(size: int) -> str:
    """Format a byte count for compact status-bar display."""
    if size < 1024:
        return f"{size} B"
    units = ("KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        value /= 1024
        if value < 1024 or unit == units[-1]:
            if value >= 100:
                return f"{value:.0f} {unit}"
            if value >= 10:
                return f"{value:.1f} {unit}"
            return f"{value:.2f} {unit}"
    return f"{size} B"


def format_browser_details(status: BrowserStatus) -> str:
    item_summary = (
        f"{status.total_count}個の項目"
        f"（フォルダ{status.folder_count}個/ファイル{status.file_count}個）"
    )
    parts: list[str] = []
    if status.selected_count:
        parts.append(
            f"{status.selected_count}個選択中"
            f"（ファイル合計 {format_size(status.selected_file_size)}）"
        )
    parts.append(item_summary)
    return " | ".join(parts)


def format_browser_status(path: Path, status: BrowserStatus) -> str:
    parts = [str(path), format_browser_details(status)]
    return " | ".join(parts)
