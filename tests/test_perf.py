from __future__ import annotations

import logging

from omnidesk.utils.perf import env_flag, log_perf, perf_debug_enabled, perf_start


def test_env_flag_accepts_known_truthy_values() -> None:
    assert env_flag("FEATURE", {"FEATURE": "1"})
    assert env_flag("FEATURE", {"FEATURE": "true"})
    assert env_flag("FEATURE", {"FEATURE": "YES"})
    assert not env_flag("FEATURE", {"FEATURE": "0"})
    assert not env_flag("FEATURE", {})


def test_perf_debug_enabled_uses_omnidesk_flag() -> None:
    assert perf_debug_enabled({"OMNIDESK_PERF_DEBUG": "on"})
    assert not perf_debug_enabled({"OMNIDESK_PERF_DEBUG": "off"})


def test_log_perf_is_opt_in(caplog) -> None:
    logger = logging.getLogger("omnidesk.test.perf")
    start = perf_start()

    with caplog.at_level(logging.INFO, logger="omnidesk.test.perf"):
        log_perf(logger, "disabled", start, enabled=False, path="unused")
        log_perf(logger, "enabled", start, enabled=True, path="target")

    assert "disabled" not in caplog.text
    assert "[perf] enabled elapsed_ms=" in caplog.text
    assert "path=target" in caplog.text
