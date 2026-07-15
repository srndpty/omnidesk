from __future__ import annotations

import logging
from pathlib import Path

from freezegun import freeze_time

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


def test_log_dir_prefers_local_app_data(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert logging_config.log_dir() == tmp_path / "OmniDesk" / "logs"


@freeze_time("2030-01-02 03:04:05")
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
    log_text = log_file.read_text(encoding="utf-8")
    assert "2030-01-02" in log_text
    assert "hello log" in log_text


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


def test_rotating_handler_continues_when_another_process_locks_log(
    monkeypatch,
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "omnidesk.log"
    handler = logging_config._WindowsSafeRotatingFileHandler(
        log_file,
        maxBytes=1,
        backupCount=1,
        encoding="utf-8",
    )
    rotate_attempts = 0

    def deny_rotation(_source: str, _destination: str) -> None:
        nonlocal rotate_attempts
        rotate_attempts += 1
        raise PermissionError()

    monkeypatch.setattr(handler, "rotate", deny_rotation)
    record = logging.LogRecord("omnidesk.test", logging.INFO, __file__, 1, "locked", (), None)

    handler.emit(record)
    handler.emit(record)
    handler.emit(record)
    handler.close()

    assert log_file.read_text(encoding="utf-8").count("locked") == 3
    assert rotate_attempts == 1


def test_rotating_handler_retries_after_cooldown_and_resets_deadline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "omnidesk.log"
    handler = logging_config._WindowsSafeRotatingFileHandler(
        log_file,
        maxBytes=1,
        backupCount=1,
        encoding="utf-8",
    )
    now = 100.0
    failed_attempts = 0
    successful_attempts = 0
    original_rotate = handler.rotate

    monkeypatch.setattr(logging_config.time, "monotonic", lambda: now)

    def deny_rotation(_source: str, _destination: str) -> None:
        nonlocal failed_attempts
        failed_attempts += 1
        raise PermissionError()

    def allow_rotation(source: str, destination: str) -> None:
        nonlocal successful_attempts
        successful_attempts += 1
        original_rotate(source, destination)

    monkeypatch.setattr(handler, "rotate", deny_rotation)
    record = logging.LogRecord("omnidesk.test", logging.INFO, __file__, 1, "retry", (), None)

    handler.emit(record)
    now = 110.0
    handler.emit(record)
    monkeypatch.setattr(handler, "rotate", allow_rotation)
    now = 130.0
    handler.emit(record)

    assert failed_attempts == 1
    assert successful_attempts == 1
    assert handler._rollover_retry_at == 0.0

    handler.emit(record)
    handler.close()

    assert successful_attempts == 2
