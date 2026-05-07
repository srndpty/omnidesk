"""Non-UI helpers for file browser behavior."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from itertools import count
from pathlib import Path


def deletion_replacement_path(
    ordered_paths: list[Path],
    selected_rows: set[int],
    deleted_paths: set[Path],
) -> Path | None:
    """Return the item to select after deleting rows from an ordered directory."""
    if not selected_rows:
        return None

    def candidate_at(row: int) -> Path | None:
        if row < 0 or row >= len(ordered_paths):
            return None
        candidate = ordered_paths[row]
        try:
            if candidate.resolve() in deleted_paths:
                return None
        except Exception:
            return None
        return candidate

    for row in range(min(selected_rows) - 1, -1, -1):
        candidate = candidate_at(row)
        if candidate is not None:
            return candidate

    for row in range(max(selected_rows) + 1, len(ordered_paths)):
        candidate = candidate_at(row)
        if candidate is not None:
            return candidate

    return None


def delete_paths(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except Exception as exc:  # pragma: no cover - filesystem dependent
            errors.append(f"{path}: {exc}")
    return errors


def perform_copy_or_move(sources: list[Path], dest_dir: Path, *, move: bool) -> list[str]:
    errors: list[str] = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in sources:
        if not src.exists():
            errors.append(f"Missing: {src}")
            continue
        try:
            if move and src.parent.resolve() == dest_dir.resolve():
                continue
        except Exception:  # pragma: no cover - resolution failure on some systems
            pass
        try:
            target = resolve_destination(dest_dir, src.name, move)
        except ValueError as exc:
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
            errors.append(f"{src} -> {target}: {exc}")
    return errors


def resolve_destination(dest_dir: Path, name: str, move: bool) -> Path:
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


def is_within(path: Path, potential_parent: Path) -> bool:
    try:
        path.relative_to(potential_parent)
        return True
    except ValueError:
        return False


def resolve_windows_program(
    program: str,
    current_path: Path,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str | None, bool]:
    """
    Return an executable path and whether it should run through cmd.exe.

    The lookup mirrors Explorer-like behavior: explicit or relative paths first,
    then the current directory, then PATH.
    """

    def exists_file(path: Path) -> bool:
        try:
            return path.exists() and path.is_file()
        except Exception:
            return False

    env = environ or os.environ
    pathexts = [ext.lower() for ext in env.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if ext]

    if any(sep in program for sep in ("/", "\\")) or program.startswith("."):
        path = Path(program)
        if not path.is_absolute():
            path = current_path / path
        resolved = _resolve_program_candidate(path, pathexts, exists_file)
        return resolved

    base = current_path / program
    resolved = _resolve_program_candidate(base, pathexts, exists_file)
    if resolved[0] is not None:
        return resolved

    found = which(program)
    if found:
        ext = Path(found).suffix.lower()
        return found, ext in (".bat", ".cmd")

    return None, False


def _resolve_program_candidate(
    path: Path,
    pathexts: list[str],
    exists_file: Callable[[Path], bool],
) -> tuple[str | None, bool]:
    if path.suffix:
        if exists_file(path):
            ext = path.suffix.lower()
            return str(path), ext in (".bat", ".cmd")
        return None, False

    for ext in pathexts:
        candidate = path.with_suffix(ext)
        if exists_file(candidate):
            return str(candidate), ext in (".bat", ".cmd")
    return None, False
