"""アドレスバーのパス移動とコマンド実行を管理するコントローラ。"""

from __future__ import annotations

import logging
import os
import shlex
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QWidget

from ..file_browser_helpers import resolve_windows_program
from ..file_browser_navigation import resolve_address_path

logger = logging.getLogger(__name__)


def parse_address_command(cmdline: str) -> list[str]:
    """アドレスバーのコマンド文字列を引数列へ分解する。"""
    return [_strip_surrounding_quotes(part) for part in shlex.split(cmdline, posix=False)]


def _strip_surrounding_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _start_detached(program: str, args: list[str], working_directory: Path) -> bool:
    """QtのPID付き戻り値からプロセス開始の成否だけを返す。"""
    started, _pid = QProcess.startDetached(program, args, str(working_directory))
    return started


class AddressBarController:
    """アドレス入力の解釈とWindowsプロセス起動を担当する。"""

    def __init__(
        self,
        parent: QWidget,
        *,
        current_path: Callable[[], Path],
        open_file: Callable[[Path], None],
        navigate_to: Callable[[Path], bool],
        show_warning: Callable[[str, str], None],
    ) -> None:
        self._parent = parent
        self._current_path = current_path
        self._open_file = open_file
        self._navigate_to = navigate_to
        self._show_warning = show_warning

    def handle_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        candidate = resolve_address_path(text, self._current_path())
        if candidate.exists():
            if candidate.is_file():
                self._open_file(candidate)
            else:
                self._navigate_to(candidate)
            return
        self.execute_command(text)

    def execute_command(self, cmdline: str) -> None:
        try:
            parts = parse_address_command(cmdline)
        except ValueError:
            logger.exception("アドレスバーのコマンドを解析できません: %s", cmdline)
            self._show_warning("Command", f"Cannot parse command line:\n{cmdline}")
            return
        if not parts:
            return
        program, *args = parts
        logger.debug("アドレスバーからコマンドを実行します: program=%s args=%s", program, args)
        current_path = self._current_path()
        if program.lower() in ("cmd", "cmd.exe"):
            comspec = os.environ.get("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
            if not _start_detached(comspec, [], current_path):
                self._show_start_failure(cmdline, current_path)
            return
        resolved, is_batch = self.resolve_program(program)
        if not resolved:
            logger.warning("アドレスバーのコマンドが見つかりません: %s", program)
            self._show_warning(
                "Command not found",
                f"'{program}' is not found in current folder or PATH.",
            )
            return
        if is_batch:
            comspec = os.environ.get("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
            started = _start_detached(comspec, ["/C", resolved, *args], current_path)
        else:
            started = _start_detached(resolved, args, current_path)
        if not started:
            self._show_start_failure(cmdline, current_path)

    def resolve_program(self, program: str) -> tuple[str | None, bool]:
        return resolve_windows_program(program, self._current_path())

    def _show_start_failure(self, cmdline: str, current_path: Path) -> None:
        logger.error("コマンドを開始できません: command=%s cwd=%s", cmdline, current_path)
        self._show_warning("Command", f"Failed to start:\n{cmdline}")
