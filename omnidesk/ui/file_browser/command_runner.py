"""Address-bar command execution for the file browser tab."""

# pyright: reportAttributeAccessIssue=false, reportCallIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false
from __future__ import annotations

import logging
import os
import shlex

from PyQt6.QtCore import QProcess
from PyQt6.QtWidgets import QMessageBox

from ..file_browser_helpers import resolve_windows_program
from ..file_browser_navigation import resolve_address_path

logger = logging.getLogger(__name__)


class FileBrowserCommandRunnerMixin:
    def _handle_path_entered(self) -> None:
        text = self._path_edit.text().strip()
        if not text:
            return

        candidate = resolve_address_path(text, self._current_path)

        if candidate.exists():
            if candidate.is_file():
                self._open_file(candidate)
            else:
                self.navigate_to(candidate)
            return

        # ここまで来たら「コマンド」と見なす
        self._execute_address_command(text)

    def _execute_address_command(self, cmdline: str) -> None:
        # 例: 'zapall -f' / 'cmd' / 'powershell -NoExit'
        try:
            parts = shlex.split(cmdline, posix=False)
        except ValueError:
            logger.exception("Cannot parse address bar command: %s", cmdline)
            QMessageBox.warning(self, "Command", f"Cannot parse command line:\n{cmdline}")
            return
        if not parts:
            return

        program, *args = parts
        logger.debug("Executing address bar command program=%s args=%s", program, args)

        # 特例: 'cmd' 単体なら現在のフォルダで起動
        if program.lower() in ("cmd", "cmd.exe"):
            comspec = os.environ.get("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
            if not QProcess.startDetached(comspec, [], str(self._current_path)):
                logger.error("Failed to start cmd from %s", self._current_path)
                QMessageBox.warning(self, "Command", f"Failed to start:\n{cmdline}")
            return

        # 実行ファイルの解決
        resolved, is_batch = self._resolve_program_for_windows(program)
        if not resolved:
            logger.warning("Address bar command was not found: %s", program)
            QMessageBox.warning(
                self, "Command not found", f"'{program}' is not found in current folder or PATH."
            )
            return

        if is_batch:
            # .bat/.cmd はシェル経由で
            comspec = os.environ.get("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
            if not QProcess.startDetached(
                comspec, ["/C", resolved, *args], str(self._current_path)
            ):
                logger.error("Failed to start batch command: %s args=%s", resolved, args)
                QMessageBox.warning(self, "Command", f"Failed to start:\n{cmdline}")
        else:
            if not QProcess.startDetached(resolved, args, str(self._current_path)):
                logger.error("Failed to start command: %s args=%s", resolved, args)
                QMessageBox.warning(self, "Command", f"Failed to start:\n{cmdline}")

    def _resolve_program_for_windows(self, program: str) -> tuple[str | None, bool]:
        """
        実行ファイルのフルパスを返す。見つからなければ (None, False)。
        返り値の第2要素は .bat / .cmd かどうか。
        """
        return resolve_windows_program(program, self._current_path)
