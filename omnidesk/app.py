"""QApplication factory and runnable entry point."""

from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import QApplication

from .theme import apply_dark_theme
from .ui.icons import application_icon
from .ui.main_window import MainWindow
from .utils.crash_diagnostics import crash_stream, install_crash_diagnostics
from .utils.event_loop_watchdog import EventLoopWatchdog
from .utils.logging_config import configure_logging

logger = logging.getLogger(__name__)

# ウォッチドッグはアプリ全体で1つだけ動かす。GCで止まらないよう参照を保持する。
_watchdog: EventLoopWatchdog | None = None


def create_app(argv: list[str] | None = None) -> QApplication:
    """Create a QApplication instance and apply the shared theme."""
    global _watchdog
    if argv is None:
        argv = sys.argv
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(argv)
    log_path = configure_logging()
    install_crash_diagnostics(log_path.parent)
    logger.info("アプリケーション初期化を開始しました: argv_count=%d", len(argv))
    app.setApplicationName("OmniDesk")
    app.setOrganizationName("OmniDesk")
    apply_dark_theme(app)
    app.setWindowIcon(application_icon())
    if _watchdog is None:
        # フリーズは落ちないぶん手掛かりが残らない。GUIスレッドが止まったら
        # 全スレッドのスタックをクラッシュログへ出しておく。
        watchdog = EventLoopWatchdog(crash_stream(), parent=app)
        watchdog.start()
        app.aboutToQuit.connect(watchdog.stop)
        _watchdog = watchdog
    logger.info("QApplicationの初期化が完了しました")
    return app


def run(argv: list[str] | None = None) -> int:
    """Boot the application and start the main event loop."""
    app = create_app(argv)
    logger.info("メインウィンドウを構築します")
    window = MainWindow()
    logger.info("メインウィンドウの構築が完了しました")
    window.show()
    logger.info("Qtイベントループを開始します")
    exit_code = app.exec()
    logger.info("Qtイベントループが終了しました: exit_code=%d", exit_code)
    return exit_code


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--thumbnail-worker":
        from .video_thumbnail_worker import run as run_thumbnail_worker

        raise SystemExit(run_thumbnail_worker(sys.argv[2:]))
    raise SystemExit(run())


if __name__ == "__main__":
    main()
