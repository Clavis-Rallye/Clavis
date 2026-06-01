"""SQL_ECHO, the switch of SQLAlchemy's SQL logging.

It is off by default, so the health-check probe does not log `SELECT 1` every 10 s.
When on, it sets the level of the `sqlalchemy.engine` logger, so the lines run through structlog.
SQLAlchemy's own echo handler would emit a second, plain-formatted line.
"""

import logging

import pytest

from app.config import get_settings
from app.logging import configure_logging


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    # Each test sets SQL_ECHO in the environment. The cache is cleared before and after,
    # so neither this nor a neighbouring test sees a stale Settings.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_sql_echo_off_by_default_silences_engine_logger(monkeypatch, tmp_path):
    monkeypatch.delenv("SQL_ECHO", raising=False)
    get_settings.cache_clear()
    configure_logging(environment="production", log_dir=str(tmp_path))
    assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING


def test_sql_echo_on_enables_info_through_structlog(monkeypatch, tmp_path):
    monkeypatch.setenv("SQL_ECHO", "1")
    get_settings.cache_clear()
    configure_logging(environment="production", log_dir=str(tmp_path))
    assert logging.getLogger("sqlalchemy.engine").level == logging.INFO
