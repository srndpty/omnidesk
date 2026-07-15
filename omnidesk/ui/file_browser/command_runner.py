"""アドレスバーコントローラへ委譲する互換Mixin。"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from PyQt6.QtCore import QProcess as QProcess
from PyQt6.QtWidgets import QMessageBox, QWidget

from .address_bar_controller import AddressBarController, parse_address_command

if TYPE_CHECKING:
    from PyQt6.QtWidgets import QLineEdit

__all__ = ["FileBrowserCommandRunnerMixin", "parse_address_command"]


class FileBrowserCommandRunnerMixin:
    if TYPE_CHECKING:
        _address_bar_controller: AddressBarController
        _path_edit: QLineEdit

    def _handle_path_entered(self) -> None:
        self._address_bar_controller.handle_text(self._path_edit.text())

    def _execute_address_command(self, cmdline: str) -> None:
        self._address_bar_controller.execute_command(cmdline)

    def _resolve_program_for_windows(self, program: str) -> tuple[str | None, bool]:
        return self._address_bar_controller.resolve_program(program)

    def _show_address_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(cast(QWidget, self), title, message)
