"""Python例外とQtネイティブ障害の診断情報を永続化する。"""

from __future__ import annotations

import faulthandler
import logging
import os
import platform
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import TextIO

from PyQt6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

logger = logging.getLogger(__name__)

CRASH_LOG_FILE_NAME = "omnidesk-crash.log"

_crash_stream: TextIO | None = None
_installed = False


def install_crash_diagnostics(log_directory: Path) -> Path:
    """未処理例外、Qtメッセージ、ネイティブ障害の記録を有効にする。"""
    global _crash_stream, _installed

    crash_path = log_directory / CRASH_LOG_FILE_NAME
    if _installed:
        return crash_path

    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        _crash_stream = crash_path.open("a", encoding="utf-8", buffering=1)
        _write_session_header(_crash_stream)
        faulthandler.enable(file=_crash_stream, all_threads=True)
    except OSError:
        logger.exception("クラッシュ診断ログを開始できません: %s", crash_path)
    else:
        sys.excepthook = _log_unhandled_exception
        threading.excepthook = _log_thread_exception
        qInstallMessageHandler(_log_qt_message)
        _installed = True
        logger.info("クラッシュ診断を有効化しました: %s", crash_path)
    return crash_path


def crash_stream() -> TextIO | None:
    """クラッシュ診断ログの出力先を返す（未初期化なら ``None``）。

    ウォッチドッグなど、同じログへ追記したい仕組みが使う。
    """
    return _crash_stream


def _write_session_header(stream: TextIO) -> None:
    stream.write(
        "\n=== OmniDesk session "
        f"{datetime.now().astimezone().isoformat()} "
        f"pid={os.getpid()} frozen={getattr(sys, 'frozen', False)} "
        f"python={platform.python_version()} ===\n"
    )


def _log_unhandled_exception(
    exception_type: type[BaseException],
    exception: BaseException,
    traceback: TracebackType | None,
) -> None:
    logger.critical(
        "未処理のPython例外",
        exc_info=(exception_type, exception, traceback),
    )


def _log_thread_exception(args: threading.ExceptHookArgs) -> None:
    exception = args.exc_value
    if exception is None:
        logger.critical(
            "バックグラウンドスレッドの例外値を取得できません: thread=%s type=%s",
            args.thread.name if args.thread is not None else "unknown",
            args.exc_type.__name__,
        )
        return
    logger.critical(
        "バックグラウンドスレッドの未処理例外: thread=%s",
        args.thread.name if args.thread is not None else "unknown",
        exc_info=(args.exc_type, exception, args.exc_traceback),
    )


def _log_qt_message(
    message_type: QtMsgType,
    context: QMessageLogContext,
    message: str,
) -> None:
    level = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }.get(message_type, logging.WARNING)
    location = f"{context.file or '?'}:{context.line}" if context.file else "?"
    logging.getLogger("qt").log(level, "%s (%s)", message, location)
