"""Filesystem operations used by the file browser."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Literal

from omnidesk.utils.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

FileOperationMode = Literal["copy", "move", "delete"]


@dataclass(frozen=True)
class FileOperationResult:
    errors: list[str]
    changed_dirs: list[Path]
    cancelled: bool = False


@dataclass(frozen=True)
class FileOperationRequest:
    sources: list[Path]
    destination: Path | None
    mode: FileOperationMode


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
    return delete_paths_with_result(paths).errors


def delete_paths_with_result(
    paths: list[Path],
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> FileOperationResult:
    """Delete files or directories and return errors plus directories that changed."""
    errors: list[str] = []
    changed_dirs: list[Path] = []
    logger.info("Deleting %d path(s)", len(paths))
    for path in paths:
        if is_cancelled is not None and is_cancelled():
            return FileOperationResult(errors, changed_dirs, cancelled=True)
        if is_dangerous_operation_path(path):
            logger.error("Refusing to delete dangerous path: %s", path)
            errors.append(f"Refusing to delete dangerous path: {path}")
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            changed_dirs.append(path.parent)
        except Exception as exc:  # pragma: no cover - filesystem dependent
            logger.exception("Failed to delete path: %s", path)
            errors.append(f"{path}: {exc}")
    return FileOperationResult(errors, changed_dirs)


def perform_copy_or_move(sources: list[Path], dest_dir: Path, *, move: bool) -> list[str]:
    """Copy or move paths into a destination directory."""
    return perform_copy_or_move_with_result(sources, dest_dir, move=move).errors


def perform_copy_or_move_with_result(
    sources: list[Path],
    dest_dir: Path,
    *,
    move: bool,
    is_cancelled: Callable[[], bool] | None = None,
) -> FileOperationResult:
    """Copy or move paths and return errors plus directories that changed."""
    errors: list[str] = []
    changed_dirs: list[Path] = []
    logger.info(
        "%s %d path(s) into %s",
        "Moving" if move else "Copying",
        len(sources),
        dest_dir,
    )
    if is_cancelled is not None and is_cancelled():
        return FileOperationResult(errors, changed_dirs, cancelled=True)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - filesystem dependent
        logger.exception("Failed to create destination directory: %s", dest_dir)
        return FileOperationResult([f"{dest_dir}: {exc}"], [])

    for src in sources:
        if is_cancelled is not None and is_cancelled():
            return FileOperationResult(errors, changed_dirs, cancelled=True)
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
            guard_error = validate_copy_or_move(src, dest_dir, move=move)
            if guard_error is not None:
                logger.warning("Refusing file operation: %s", guard_error)
                errors.append(guard_error)
                continue
            target = resolve_destination(dest_dir, src.name, move)
        except ValueError as exc:
            logger.warning("Could not resolve destination for %s into %s: %s", src, dest_dir, exc)
            errors.append(str(exc))
            continue
        try:
            if move:
                shutil.move(str(src), str(target))
                changed_dirs.extend([dest_dir, src.parent])
            elif src.is_dir():
                shutil.copytree(src, target)
                changed_dirs.append(dest_dir)
            else:
                shutil.copy2(src, target)
                changed_dirs.append(dest_dir)
        except Exception as exc:  # pragma: no cover - filesystem dependent
            logger.exception("Failed to copy/move %s to %s", src, target)
            errors.append(f"{src} -> {target}: {exc}")
    return FileOperationResult(errors, changed_dirs)


def execute_file_operation(
    request: FileOperationRequest,
    *,
    is_cancelled: Callable[[], bool] | None = None,
) -> FileOperationResult:
    """Execute a file operation request."""
    if request.mode == "delete":
        return delete_paths_with_result(request.sources, is_cancelled=is_cancelled)
    if request.mode not in ("copy", "move"):
        return FileOperationResult([f"Unsupported file operation mode: {request.mode}"], [])
    if request.destination is None:
        return FileOperationResult(["Destination is required."], [])
    return perform_copy_or_move_with_result(
        request.sources,
        request.destination,
        move=request.mode == "move",
        is_cancelled=is_cancelled,
    )


def validate_copy_or_move(src: Path, dest_dir: Path, *, move: bool) -> str | None:
    """Return a user-facing error when a copy/move request is unsafe."""
    try:
        src_resolved = src.resolve(strict=False)
        dest_resolved = dest_dir.resolve(strict=False)
        target_resolved = (dest_dir / src.name).resolve(strict=False)
    except OSError as exc:
        logger.debug("Could not resolve copy/move safety paths", exc_info=True)
        return f"{src}: {exc}"

    if _same_path(src_resolved, target_resolved):
        return f"Source and destination are the same: {src}"

    if src.is_dir() and _is_relative_to_path(dest_resolved, src_resolved):
        return f"Refusing to {'move' if move else 'copy'} a folder into itself: {src}"

    return None


def _same_path(left: Path, right: Path) -> bool:
    return _normalise_path_text(left) == _normalise_path_text(right)


def _is_relative_to_path(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        path_text = _normalise_path_text(path)
        parent_text = _normalise_path_text(parent)
        return path_text == parent_text or path_text.startswith(
            parent_text + _path_text_separator()
        )


def _normalise_path_text(path: Path) -> str:
    separator = _path_text_separator()
    return (
        os.path.normcase(str(path)).replace("/", separator).replace("\\", separator).rstrip("\\/")
    )


def _path_text_separator() -> str:
    return "\\" if os.name == "nt" else os.sep


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
