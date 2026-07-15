"""Navigation helpers for file browser tabs."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DirectoryFingerprint = tuple[int, int]
RetrySelectionAction = Literal["inactive", "selected", "retry", "exhausted"]
SettledScrollAction = Literal["inactive", "path_missing", "select", "blocked"]


@dataclass(frozen=True)
class NavigationHistoryStep:
    """Result of moving through a file browser tab history stack."""

    target: Path
    back_history: list[Path]
    forward_history: list[Path]


def navigation_target(path: Path) -> Path:
    """Return the directory that should become the current browser root."""
    return path if path.is_dir() else path.parent


def should_record_history(current: Path, destination: Path, *, from_history: bool) -> bool:
    """Return whether navigating to destination should append current to history."""
    if from_history:
        return False
    return not same_navigation_path(current, destination)


def same_navigation_path(left: Path, right: Path) -> bool:
    """Return whether two paths refer to the same navigation target."""
    try:
        return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
            str(right.resolve(strict=False))
        )
    except OSError:
        return os.path.normcase(str(left.absolute())) == os.path.normcase(str(right.absolute()))


def directory_fingerprint(path: Path) -> DirectoryFingerprint | None:
    """Return a lightweight best-effort directory change fingerprint."""
    try:
        stat_result = path.stat()
    except OSError:
        return None
    mtime = getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1e9))
    return mtime, stat_result.st_size


def directory_fingerprint_changed(path: Path, previous: DirectoryFingerprint | None) -> bool:
    """Return whether a directory fingerprint changed since it was captured."""
    if previous is None:
        return False
    current = directory_fingerprint(path)
    return current is not None and current != previous


def navigation_history_step(
    back_history: Sequence[Path],
    forward_history: Sequence[Path],
    current: Path,
    *,
    direction: Literal["back", "forward"],
) -> NavigationHistoryStep | None:
    """Return the next history state for a back/forward navigation request."""
    if direction == "back":
        if not back_history:
            return None
        return NavigationHistoryStep(
            target=back_history[-1],
            back_history=list(back_history[:-1]),
            forward_history=[current, *forward_history],
        )

    if not forward_history:
        return None
    return NavigationHistoryStep(
        target=forward_history[0],
        back_history=[*back_history, current],
        forward_history=list(forward_history[1:]),
    )


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


def refresh_sort_action(
    *,
    active: bool,
    retries_before_attempt: int,
    restore_succeeded: bool,
) -> RetrySelectionAction:
    """リフレッシュ後ソートの継続可否を返す。"""
    if not active or retries_before_attempt <= 0:
        return "inactive"
    if restore_succeeded:
        return "selected"
    if retries_before_attempt - 1 <= 0:
        return "exhausted"
    return "retry"


def settled_scroll_action(
    *,
    pending_path: Path | None,
    retries_before_attempt: int,
    path_exists: bool,
    can_apply: bool,
) -> SettledScrollAction:
    """遅延スクロールを適用・中断・継続するか返す。"""
    if pending_path is None or retries_before_attempt <= 0:
        return "inactive"
    if not path_exists:
        return "path_missing"
    if not can_apply:
        return "blocked"
    return "select"
