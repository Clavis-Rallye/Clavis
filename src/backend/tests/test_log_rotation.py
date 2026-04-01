"""
The daily rotation and retention of the four log streams, and the stdout handler of production.

The tests need no database or running service. tmp_path holds the log directory.
"""

import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from unittest.mock import patch

import pytest


def _configure_for_tmp(tmp_path: Path, **retention_env: str) -> None:
    """Run configure_logging pointed at tmp_path and reset afterwards.

    ``retention_env`` sets retention variables such as
    ``AUDIT_LOG_RETENTION_DAYS="30"``. The periods come from the real
    ``LoggingSettings``, so its defaults and validation apply.
    """
    # Patch get_settings so configure_logging doesn't need real env vars
    from unittest.mock import MagicMock

    from app.config import LoggingSettings

    names = (
        "AUDIT_LOG_RETENTION_DAYS",
        "APP_LOG_RETENTION_DAYS",
        "ERROR_LOG_RETENTION_DAYS",
        "ACCESS_LOG_RETENTION_DAYS",
    )
    with pytest.MonkeyPatch.context() as env:
        for name in names:
            env.delenv(name, raising=False)
        for name, value in retention_env.items():
            env.setenv(name, value)
        logging_settings = LoggingSettings(hmac_audit_key="k" * 32, log_dir=str(tmp_path))

    mock_settings = MagicMock()
    mock_settings.logging = logging_settings
    mock_settings.environment = "production"

    with patch("app.logging.get_settings", return_value=mock_settings):
        from app.logging import configure_logging

        configure_logging(environment="production", log_dir=str(tmp_path))


def _handlers_for(log_dir: Path) -> dict[str, TimedRotatingFileHandler]:
    """Return the TimedRotatingFileHandler for each category logger."""
    categories = ("audit", "app", "error", "access")
    result: dict[str, TimedRotatingFileHandler] = {}
    for name in categories:
        logger = logging.getLogger(name)
        for h in logger.handlers:
            if isinstance(h, TimedRotatingFileHandler):
                result[name] = h
                break
    return result


@pytest.fixture(autouse=True)
def _reset_logging():
    """Remove handlers added by configure_logging after each test."""
    yield
    for name in ("audit", "app", "error", "access", "root"):
        lg = logging.getLogger(name if name != "root" else "")
        for h in list(lg.handlers):
            h.close()
        lg.handlers.clear()
    # Reset the module-level flag so configure_logging can be called again
    import app.logging as log_module

    log_module._logging_configured = False


def test_timed_rotating_handlers_installed(tmp_path: Path) -> None:
    """configure_logging installs TimedRotatingFileHandler for all four streams."""
    _configure_for_tmp(tmp_path)
    handlers = _handlers_for(tmp_path)

    assert set(handlers.keys()) == {"audit", "app", "error", "access"}, (
        f"Missing handlers. Found: {set(handlers.keys())}"
    )
    for name, h in handlers.items():
        assert isinstance(h, TimedRotatingFileHandler), (
            f"{name} handler is {type(h)}, expected TimedRotatingFileHandler"
        )


def _stdout_stream_handler(logger_name: str) -> logging.StreamHandler | None:
    """The plain stdout StreamHandler on a logger. File handlers subclass StreamHandler and are excluded."""
    logger = logging.getLogger(logger_name if logger_name != "root" else "")
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            return h
    return None


def test_production_adds_stdout_handler_to_categories(tmp_path: Path) -> None:
    """Production attaches a stdout StreamHandler to root and every category logger, so `docker logs` keeps working."""
    _configure_for_tmp(tmp_path)

    # Only the presence of a non-file StreamHandler is asserted. Pytest's capture swaps sys.stdout,
    # so the handler binds the real stream while the test sees the capture object.
    # test_production_stdout_is_structured_json covers that the output reaches stdout.
    for name in ("root", "audit", "app", "error", "access"):
        handler = _stdout_stream_handler(name)
        assert handler is not None, f"{name} logger has no stdout StreamHandler in production"


def test_production_stdout_is_structured_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """In production the stdout handler renders JSON, not the dev console format, so container logs are parseable."""
    _configure_for_tmp(tmp_path)

    logging.getLogger("app").info("retention_sweep_completed")

    out = capsys.readouterr().out.strip().splitlines()
    assert out, "expected a line on stdout in production mode"
    record = json.loads(out[-1])  # raises if not valid JSON
    assert record["event"] == "retention_sweep_completed"


def test_rotation_schedule_and_utc(tmp_path: Path) -> None:
    """All handlers rotate at midnight UTC with interval=1."""
    _configure_for_tmp(tmp_path)
    handlers = _handlers_for(tmp_path)

    for name, h in handlers.items():
        assert h.when == "MIDNIGHT", f"{name}: when={h.when!r}, expected 'MIDNIGHT'"
        assert h.interval == 86400, f"{name}: interval={h.interval}, expected 86400"
        assert h.utc is True, f"{name}: utc={h.utc}, expected True"


def test_backup_counts(tmp_path: Path) -> None:
    """audit stream keeps 365 rotations; app/error/access keep 90."""
    _configure_for_tmp(tmp_path)
    handlers = _handlers_for(tmp_path)

    assert handlers["audit"].backupCount == 365, f"audit backupCount={handlers['audit'].backupCount}, expected 365"
    for name in ("app", "error", "access"):
        assert handlers[name].backupCount == 90, f"{name} backupCount={handlers[name].backupCount}, expected 90"


def test_backup_counts_follow_the_retention_variables(tmp_path: Path) -> None:
    """The four retention variables set the number of daily files kept."""
    _configure_for_tmp(
        tmp_path,
        AUDIT_LOG_RETENTION_DAYS="30",
        APP_LOG_RETENTION_DAYS="14",
        ERROR_LOG_RETENTION_DAYS="21",
        ACCESS_LOG_RETENTION_DAYS="7",
    )
    handlers = _handlers_for(tmp_path)

    assert {name: h.backupCount for name, h in handlers.items()} == {"audit": 30, "app": 14, "error": 21, "access": 7}
