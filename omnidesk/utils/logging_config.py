"""Application logging setup."""

from __future__ import annotations

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_ENV_VAR = "OMNIDESK_LOG_LEVEL"
LOG_FILE_NAME = "omnidesk.log"
DEFAULT_LOG_LEVEL = logging.INFO


def _default_log_dir() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "OmniDesk" / "logs"
    return Path.home() / ".omnidesk" / "logs"


def log_dir() -> Path:
    """Return the directory used for persistent application logs."""
    return _default_log_dir()


def log_file_path() -> Path:
    """Return the active application log file path."""
    return log_dir() / LOG_FILE_NAME


def log_level_from_environment(environ: dict[str, str] | None = None) -> int:
    """Resolve the configured log level from OMNIDESK_LOG_LEVEL."""
    env = environ if environ is not None else os.environ
    raw_level = env.get(LOG_ENV_VAR, "").strip().upper()
    if not raw_level:
        return DEFAULT_LOG_LEVEL
    level = getattr(logging, raw_level, None)
    return level if isinstance(level, int) else DEFAULT_LOG_LEVEL


def configure_logging(
    *,
    level: int | None = None,
    path: Path | None = None,
    force: bool = False,
) -> Path:
    """Configure file logging and return the log file path."""
    target = path or log_file_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        fallback_dir = Path(tempfile.gettempdir()) / "OmniDesk" / "logs"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        target = fallback_dir / LOG_FILE_NAME
    root_logger = logging.getLogger()

    if force:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
    elif any(getattr(handler, "_omnidesk_handler", False) for handler in root_logger.handlers):
        root_logger.setLevel(level if level is not None else log_level_from_environment())
        return target

    resolved_level = level if level is not None else log_level_from_environment()
    try:
        handler = _rotating_file_handler(target)
    except OSError:
        fallback_dir = Path(tempfile.gettempdir()) / "OmniDesk" / "logs"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        target = fallback_dir / LOG_FILE_NAME
        handler = _rotating_file_handler(target)
    handler._omnidesk_handler = True  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    root_logger.addHandler(handler)
    root_logger.setLevel(resolved_level)
    logging.getLogger(__name__).debug("Logging configured at %s", target)
    return target


def _rotating_file_handler(path: Path) -> RotatingFileHandler:
    return RotatingFileHandler(
        path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
