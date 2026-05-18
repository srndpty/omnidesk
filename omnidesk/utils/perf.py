"""Small opt-in performance logging helpers."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from time import perf_counter

_TRUE_VALUES = {"1", "true", "yes", "on"}


def env_flag(name: str, environ: Mapping[str, str] | None = None) -> bool:
    """Return whether an environment flag is enabled."""
    source = os.environ if environ is None else environ
    return source.get(name, "").lower() in _TRUE_VALUES


def perf_debug_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether opt-in performance logging is enabled."""
    return env_flag("OMNIDESK_PERF_DEBUG", environ)


def perf_start() -> float:
    """Return a high-resolution timestamp for performance measurements."""
    return perf_counter()


def log_perf(
    logger: logging.Logger,
    event: str,
    start: float,
    *,
    enabled: bool,
    **fields: object,
) -> None:
    """Log an elapsed performance event when enabled."""
    if not enabled:
        return
    elapsed_ms = (perf_counter() - start) * 1000
    details = " ".join(f"{key}={value}" for key, value in fields.items())
    if details:
        logger.info("[perf] %s elapsed_ms=%.1f %s", event, elapsed_ms, details)
    else:
        logger.info("[perf] %s elapsed_ms=%.1f", event, elapsed_ms)
