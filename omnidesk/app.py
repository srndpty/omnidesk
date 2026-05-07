"""QApplication factory and runnable entry point."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from .theme import apply_dark_theme
from .ui.icons import application_icon
from .ui.main_window import MainWindow
from .utils.logging_config import configure_logging


def create_app(argv: list[str] | None = None) -> QApplication:
    """Create a QApplication instance and apply the shared theme."""
    if argv is None:
        argv = sys.argv
    existing = QApplication.instance()
    app = existing if existing is not None else QApplication(argv)
    configure_logging()
    app.setApplicationName("OmniDesk")
    app.setOrganizationName("OmniDesk")
    apply_dark_theme(app)
    app.setWindowIcon(application_icon())
    return app


def run(argv: list[str] | None = None) -> int:
    """Boot the application and start the main event loop."""
    app = create_app(argv)
    window = MainWindow()
    window.show()
    return app.exec()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
