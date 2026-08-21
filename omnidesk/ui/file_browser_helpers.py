"""Non-UI helpers for file browser behavior."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from .file_browser_navigation import navigation_key
from .file_operations import (
    delete_paths as delete_paths,
)
from .file_operations import (
    perform_copy_or_move as perform_copy_or_move,
)
from .file_operations import (
    resolve_destination as resolve_destination,
)

logger = logging.getLogger(__name__)


def file_action_states(
    selected_count: int,
    *,
    clipboard_has_paths: bool,
    current_path_exists: bool,
) -> dict[str, bool]:
    """ファイル操作アクションの有効状態を返す。"""
    has_selection = selected_count > 0
    return {
        "copy": has_selection,
        "cut": has_selection,
        "delete": has_selection,
        "rename": selected_count == 1,
        "paste": clipboard_has_paths and current_path_exists,
        "new_file": current_path_exists,
        "new_folder": current_path_exists,
    }


def deletion_replacement_path(
    path_at: Callable[[int], Path | None],
    row_count: int,
    selected_rows: set[int],
    deleted_paths: Iterable[Path],
) -> Path | None:
    """Return the item to select after deleting rows from an ordered directory.

    ``path_at`` は行番号からパスを取り出すコールバック。以前は全行ぶんの
    ``Path`` を事前に作って渡していたが、削除確認ダイアログを出す**前**に
    7,500件ぶんのモデル往復と ``Path`` 生成が走り、体感できる待ちになっていた。
    探索順（選択行の直前から前方へ、見つからなければ直後から後方へ）は変えず、
    実際に調べる行だけを遅延で取り出す。

    突き合わせは実I/Oを伴わない :func:`navigation_key` で行う。
    """
    if not selected_rows:
        return None

    deleted_keys = {navigation_key(path) for path in deleted_paths}

    def candidate_at(row: int) -> Path | None:
        if row < 0 or row >= row_count:
            return None
        candidate = path_at(row)
        if candidate is None:
            return None
        if navigation_key(candidate) in deleted_keys:
            return None
        return candidate

    for row in range(min(selected_rows) - 1, -1, -1):
        candidate = candidate_at(row)
        if candidate is not None:
            return candidate

    for row in range(max(selected_rows) + 1, row_count):
        candidate = candidate_at(row)
        if candidate is not None:
            return candidate

    return None


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
            logger.debug("実行ファイル候補の確認に失敗しました: %s", path, exc_info=True)
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
