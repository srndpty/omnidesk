from __future__ import annotations

import logging
from pathlib import Path

from omnidesk.utils import logging_config


def test_log_level_from_environment_defaults_and_accepts_known_level() -> None:
    assert logging_config.log_level_from_environment({}) == logging.INFO
    assert (
        logging_config.log_level_from_environment({"OMNIDESK_LOG_LEVEL": "debug"}) == logging.DEBUG
    )
    assert (
        logging_config.log_level_from_environment({"OMNIDESK_LOG_LEVEL": "not-a-level"})
        == logging.INFO
    )


def test_configure_logging_writes_rotating_log_file(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "omnidesk.log"

    configured_path = logging_config.configure_logging(
        level=logging.DEBUG,
        path=log_file,
        force=True,
    )
    logging.getLogger("omnidesk.test").debug("hello log")

    for handler in logging.getLogger().handlers:
        handler.flush()

    assert configured_path == log_file
    assert "hello log" in log_file.read_text(encoding="utf-8")


def test_configure_logging_does_not_duplicate_handler(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "omnidesk.log"

    logging_config.configure_logging(path=log_file, force=True)
    before = len(
        [h for h in logging.getLogger().handlers if getattr(h, "_omnidesk_handler", False)]
    )
    logging_config.configure_logging(path=log_file)
    after = len([h for h in logging.getLogger().handlers if getattr(h, "_omnidesk_handler", False)])

    assert before == after == 1


def test_configure_logging_falls_back_when_target_directory_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "blocked" / "omnidesk.log"
    fallback_root = tmp_path / "temp"
    original_mkdir = Path.mkdir

    monkeypatch.setattr(logging_config.tempfile, "gettempdir", lambda: str(fallback_root))

    def mkdir_with_blocked_target(self: Path, *args, **kwargs) -> None:
        if self == log_file.parent:
            raise OSError()
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir_with_blocked_target)

    configured = logging_config.configure_logging(path=log_file, force=True)

    assert configured == fallback_root / "OmniDesk" / "logs" / "omnidesk.log"
