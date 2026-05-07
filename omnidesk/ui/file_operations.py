"""Filesystem operations used by the file browser."""

from __future__ import annotations

import logging
import shutil
from itertools import count
from pathlib import Path

logger = logging.getLogger(__name__)


def delete_paths(paths: list[Path]) -> list[str]:
    """Delete files or directories and return user-facing error messages."""
    errors: list[str] = []
    for path in paths:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except Exception as exc:  # pragma: no cover - filesystem dependent
            logger.exception("Failed to delete path: %s", path)
            errors.append(f"{path}: {exc}")
    return errors


def perform_copy_or_move(sources: list[Path], dest_dir: Path, *, move: bool) -> list[str]:
    """Copy or move paths into a destination directory."""
    errors: list[str] = []
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        logger.exception("Failed to create destination directory: %s", dest_dir)
        return [f"{dest_dir}: {exc}"]

    for src in sources:
        if not src.exists():
            logger.warning("Source path does not exist: %s", src)
            errors.append(f"Missing: {src}")
            continue
        try:
            if move and src.parent.resolve() == dest_dir.resolve():
                continue
        except Exception:  # pragma: no cover - resolution failure on some systems
            logger.debug(
                "Could not resolve source/destination for same-directory check", exc_info=True
            )
        try:
            target = resolve_destination(dest_dir, src.name, move)
        except ValueError as exc:
            logger.warning("Could not resolve destination for %s into %s: %s", src, dest_dir, exc)
            errors.append(str(exc))
            continue
        try:
            if move:
                shutil.move(str(src), str(target))
            elif src.is_dir():
                shutil.copytree(src, target)
            else:
                shutil.copy2(src, target)
        except Exception as exc:  # pragma: no cover - filesystem dependent
            logger.exception("Failed to copy/move %s to %s", src, target)
            errors.append(f"{src} -> {target}: {exc}")
    return errors


def resolve_destination(dest_dir: Path, name: str, move: bool) -> Path:
    """Return a non-conflicting destination path."""
    target = dest_dir / name
    if not target.exists():
        return target
    if move:
        raise ValueError(f"Destination already has {name}")
    stem = target.stem
    suffix = target.suffix
    for n in count(1):
        candidate = dest_dir / f"{stem} - Copy {n}{suffix}"
        if not candidate.exists():
            return candidate
    raise ValueError("Unable to resolve destination")


def rename_path(original: Path, new_name: str) -> tuple[Path | None, str | None]:
    """Rename a path within its parent directory."""
    if not new_name or new_name == original.name:
        return None, None
    target = original.with_name(new_name)
    if target.exists():
        return None, f"{target} already exists."
    try:
        original.rename(target)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        logger.exception("Failed to rename %s to %s", original, target)
        return None, str(exc)
    return target, None


def create_file(dest_dir: Path, name: str) -> tuple[Path | None, str | None]:
    """Create a new file with conflict-safe naming."""
    target = resolve_destination(dest_dir, name, move=False)
    try:
        target.touch(exist_ok=False)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        logger.exception("Failed to create file: %s", target)
        return None, str(exc)
    return target, None


def create_folder(dest_dir: Path, name: str) -> tuple[Path | None, str | None]:
    """Create a new folder with conflict-safe naming."""
    target = resolve_destination(dest_dir, name, move=False)
    try:
        target.mkdir(parents=True, exist_ok=False)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        logger.exception("Failed to create folder: %s", target)
        return None, str(exc)
    return target, None
