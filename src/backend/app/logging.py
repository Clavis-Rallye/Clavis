"""
Structured logging configuration using structlog.

Provides 4 separate log categories (audit, app, error, access) with JSON rendering,
HMAC integrity chain for audit logs, sensitive field filtering, and development-friendly
console output.
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import structlog
from structlog.types import EventDict, WrappedLogger

from app.audit_handler import AuditLogHandler, register_handler
from app.config import get_settings

# A log key that contains one of these names, in any case, has its value redacted.
SENSITIVE_FIELDS = {
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "totp_secret",
    "jwt_secret_key",
    "hmac_audit_key",
    "access_key",
    "secret_key",
}


def filter_sensitive_fields(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Structlog processor: replace the value of a sensitive key with "[REDACTED]".

    A key is sensitive when it contains a name of ``SENSITIVE_FIELDS``, case-insensitive.
    """
    for key in list(event_dict.keys()):
        if any(sensitive in key.lower() for sensitive in SENSITIVE_FIELDS):
            event_dict[key] = "[REDACTED]"
    return event_dict


def add_service_identifier(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Structlog processor: add the ``service`` field to every log entry."""
    event_dict["service"] = "clavis-backend"
    return event_dict


def add_log_category(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Structlog processor: add the ``category`` field, derived from the logger name.

    The names ``audit``, ``error`` and ``access`` map to themselves, any other name to ``app``.
    """
    logger_name = event_dict.get("logger", "app")

    if logger_name == "audit":
        category = "audit"
    elif logger_name == "error":
        category = "error"
    elif logger_name == "access":
        category = "access"
    else:
        category = "app"

    event_dict["category"] = category
    return event_dict


def configure_logging(environment: str = "development", log_dir: str | None = None) -> None:
    """Configure structlog with four log categories, each with its own file.

    - Audit log: security-relevant events with the HMAC integrity chain
    - Application log: general application events
    - Error log: error-level events and exceptions
    - Access log: HTTP request and response lines

    The files are JSON. The console gets colored output in development and JSON elsewhere.
    ``log_dir`` defaults to ``settings.logging.log_dir``.
    """
    settings = get_settings()

    if log_dir is None:
        log_dir = settings.logging.log_dir

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    shared_processors: list[structlog.types.Processor] = [
        # Context variables such as request_id.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        # Must come before add_log_category, which reads the logger name.
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_service_identifier,
        add_log_category,
        filter_sensitive_fields,
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    root_logger.handlers.clear()

    json_renderer = structlog.processors.JSONRenderer()

    # Per category: file path and retention in days (= backupCount for daily
    # rotation). The operator sets the days with AUDIT_, APP_, ERROR_ and
    # ACCESS_LOG_RETENTION_DAYS. The handler deletes
    # the oldest plain rotated file beyond the count. A day without an entry
    # leaves no file, so a quiet stream keeps its files longer than the count
    # in days. log-cleanup.sh deletes by age, with the same variables.
    retention = settings.logging
    log_categories: dict[str, tuple[Path, int]] = {
        "audit": (log_path / "audit.log", retention.audit_log_retention_days),
        "app": (log_path / "application.log", retention.app_log_retention_days),
        "error": (log_path / "error.log", retention.error_log_retention_days),
        "access": (log_path / "access.log", retention.access_log_retention_days),
    }

    # The audit category gets the chain-aware AuditLogHandler, so the write to
    # disk and the integrity-hash update happen under the same lock. Every
    # other category uses the stdlib rotating handler.
    for category, (file_path, backup_count) in log_categories.items():
        handler: logging.Handler
        if category == "audit":
            audit_handler = AuditLogHandler(
                str(file_path),
                when="midnight",
                interval=1,
                backupCount=backup_count,
                encoding="utf-8",
                utc=True,
            )
            register_handler(audit_handler)
            handler = audit_handler
        else:
            handler = TimedRotatingFileHandler(
                str(file_path),
                when="midnight",
                interval=1,
                backupCount=backup_count,
                encoding="utf-8",
                utc=True,
            )
        handler.setLevel(logging.INFO if category != "error" else logging.ERROR)

        handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=json_renderer,
                foreign_pre_chain=shared_processors,
            )
        )

        category_logger = logging.getLogger(category)
        category_logger.setLevel(logging.INFO if category != "error" else logging.ERROR)
        category_logger.addHandler(handler)
        category_logger.propagate = False

    # Route SQLAlchemy engine logs through structlog (a single rendered line via
    # the root pipeline) instead of SQLAlchemy's own echo StreamHandler, which
    # would emit a second, plain-formatted line. Off by default — including in
    # dev, so the every-10s health-check `SELECT 1` probe doesn't spam the
    # logs. Opt in with SQL_ECHO=1.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO if settings.sql_echo else logging.WARNING)

    # Console output on stdout, so `docker logs clavis-api` works in every
    # environment. Without it the category loggers (audit/app/error/access,
    # propagate=False) would write to files only and `docker logs` would be
    # near-silent in production.
    console_handler = logging.StreamHandler(sys.stdout)
    console_processor: structlog.types.Processor
    if environment == "development":
        console_handler.setLevel(logging.DEBUG)
        console_processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        console_handler.setLevel(logging.INFO)
        console_processor = json_renderer

    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=console_processor,
            foreign_pre_chain=shared_processors,
        )
    )

    # Add to root (third-party / propagating loggers) and to each category
    # logger (which has propagate=False, so it needs its own console handler).
    root_logger.addHandler(console_handler)
    for category in log_categories:
        logging.getLogger(category).addHandler(console_handler)


def get_logger(name: str = "app") -> structlog.stdlib.BoundLogger:
    """Return a structlog logger. ``name`` is one of "audit", "app", "error" and "access".

    The name decides the category, and so the log file, of its entries.
    """
    return structlog.get_logger(name)  # type: ignore[no-any-return]  # structlog returns Any


# One logger per category.
audit_logger = get_logger("audit")
app_logger = get_logger("app")
error_logger = get_logger("error")
access_logger = get_logger("access")


# Set by ensure_logging_configured.
_logging_configured = False


def ensure_logging_configured() -> None:
    """Configure logging from the settings on the first call. Later calls do nothing."""
    global _logging_configured
    if not _logging_configured:
        settings = get_settings()
        configure_logging(
            environment=settings.environment,
            log_dir=settings.logging.log_dir,
        )
        _logging_configured = True
