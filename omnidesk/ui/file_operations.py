"""Filesystem operations used by the file browser."""

from __future__ import annotations

import logging
import shutil
from itertools import count
from pathlib import Path

from omnidesk.utils.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)


def is_dangerous_operation_path(path: Path) -> bool:
    """Return whether a filesystem operation should refuse this path."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    if resolved == resolved.parent:
        return True
    try:
        if resolved == Path.home().resolve():
            return True
    except OSError:
        logger.debug("Could not resolve home directory for safety check", exc_info=True)
    try:
        if resolved == DEFAULT_CONFIG_DIR.resolve():
            return True
    except OSError:
        logger.debug("Could not resolve config directory for safety check", exc_info=True)
    return False


def is_plain_child_name(name: str) -> bool:
    """Return whether name can be used as a direct child name."""
    stripped = name.strip()
    return bool(stripped) and Path(stripped).name == stripped


def delete_paths(paths: list[Path]) -> list[str]:
    """Delete files or directories and return user-facing error messages."""
    errors: list[str] = []
    logger.info("Deleting %d path(s)", len(paths))
    for path in paths:
        if is_dangerous_operation_path(path):
            logger.error("Refusing to delete dangerous path: %s", path)
            errors.append(f"Refusing to delete dangerous path: {path}")
            continue
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
    logger.info(
        "%s %d path(s) into %s",
        "Moving" if move else "Copying",
        len(sources),
        dest_dir,
    )
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        logger.exception("Failed to create destination directory: %s", dest_dir)
        return [f"{dest_dir}: {exc}"]

    for src in sources:
        if is_dangerous_operation_path(src):
            logger.error("Refusing to operate on dangerous source path: %s", src)
            errors.append(f"Refusing to operate on dangerous path: {src}")
            continue
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
    if not is_plain_child_name(new_name):
        logger.warning("Rejected invalid rename target %r for %s", new_name, original)
        return None, "Name must not contain path separators."
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
    if not is_plain_child_name(name):
        logger.warning("Rejected invalid file name %r in %s", name, dest_dir)
        return None, "Name must not contain path separators."
    target = resolve_destination(dest_dir, name, move=False)
    try:
        target.touch(exist_ok=False)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        logger.exception("Failed to create file: %s", target)
        return None, str(exc)
    return target, None


def create_folder(dest_dir: Path, name: str) -> tuple[Path | None, str | None]:
    """Create a new folder with conflict-safe naming."""
    if not is_plain_child_name(name):
        logger.warning("Rejected invalid folder name %r in %s", name, dest_dir)
        return None, "Name must not contain path separators."
    target = resolve_destination(dest_dir, name, move=False)
    try:
        target.mkdir(parents=True, exist_ok=False)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        logger.exception("Failed to create folder: %s", target)
        return None, str(exc)
    return target, None
