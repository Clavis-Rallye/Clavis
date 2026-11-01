"""The production compose file passes every setting an operator should set.

``deployment/docker-compose.prod.yml`` is the only way an operator reaches the
API's settings. A setting the file does not pass cannot be set, whatever the
documentation says. These tests hold three things together:

- every setting of ``app/config.py`` appears in the compose file, or stands in
  ``NOT_FOR_OPERATORS`` with a reason,
- every variable of the compose file has a default, except the few that only
  the operator can know (``REQUIRED_FROM_THE_OPERATOR``). A stack Env that holds
  only those still starts the API. That is the update rule: a stack Env written
  for an older version must start the new one,
- every variable of the compose file is documented in ``deployment/.env.example``.
"""

import ipaddress
import os
import re
from pathlib import Path

import pytest
import yaml
from pydantic_settings import BaseSettings

from app.config import Settings, startup_warnings

APP_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = APP_ROOT / "deployment" / "docker-compose.prod.yml"
ENV_EXAMPLE = APP_ROOT / "deployment" / ".env.example"

# Settings that exist for development and tests, never for an operator.
NOT_FOR_OPERATORS = {
    "SQL_ECHO": "prints every SQL statement into the log, a debugging aid",
    "ALLOW_TEAM_JOIN_BY_ID": (
        "join by team id without the code — a test aid for the suite, must stay off in production"
    ),
}

# Variables without a default: only the operator knows them. A new entry here
# breaks every existing stack Env at the update, so it needs a release step.
REQUIRED_FROM_THE_OPERATOR = {
    "DATABASE_URL": "postgresql+asyncpg://clavis:clavis@db:5432/clavis",
    "S3_ACCESS_KEY": "GK000000000000000000000000",
    "S3_SECRET_KEY": "0" * 64,
    "JWT_SECRET_KEY": "j" * 48,
    "HMAC_AUDIT_KEY": "h" * 48,
    "CORS_ORIGINS": "https://rally.example.org",
    "WEBAUTHN_RP_ID": "rally.example.org",
    "WEBAUTHN_ORIGIN": "https://rally.example.org",
}

_SUBSTITUTION = re.compile(r"^\$\{(?P<name>\w+)(?:(?P<op>:-|:\?|-|\?)(?P<rest>.*))?\}$")


def _api_environment() -> dict[str, str]:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    environment = compose["services"]["api"]["environment"]
    assert isinstance(environment, dict)
    return {str(key): str(value) for key, value in environment.items()}


def _substitutions() -> dict[str, str | None]:
    """Each ``${VAR...}`` of the api environment: its default, or None when it has none."""
    found: dict[str, str | None] = {}
    for value in _api_environment().values():
        match = _SUBSTITUTION.match(value)
        if match is None:
            continue
        op, rest = match.group("op"), match.group("rest")
        found[match.group("name")] = rest if op in (":-", "-") else None
    return found


def _settings_env_names() -> set[str]:
    """The environment variable of every field of ``Settings`` and its sections."""
    names: set[str] = set()
    for field_name, field in Settings.model_fields.items():
        section = field.annotation
        if isinstance(section, type) and issubclass(section, BaseSettings):
            prefix = str(section.model_config.get("env_prefix", ""))
            names.update(f"{prefix}{name}".upper() for name in section.model_fields)
        else:
            names.add(field_name.upper())
    return names


def _stack_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """The environment a stack Env with only the operator's values produces."""
    for name in _settings_env_names() | set(_substitutions()):
        monkeypatch.delenv(name, raising=False)
    for name, value in _api_environment().items():
        match = _SUBSTITUTION.match(value)
        if match is None:
            monkeypatch.setenv(name, value)
        elif match.group("name") in REQUIRED_FROM_THE_OPERATOR:
            monkeypatch.setenv(name, REQUIRED_FROM_THE_OPERATOR[match.group("name")])
        else:
            monkeypatch.setenv(name, _substitutions()[match.group("name")] or "")
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)


def test_every_setting_reaches_the_api_container():
    passed = set(_api_environment())
    missing = sorted(_settings_env_names() - passed - set(NOT_FOR_OPERATORS))
    assert missing == [], f"app/config.py settings the production compose file does not pass: {missing}"


def test_only_the_operators_own_values_have_no_default():
    without_default = sorted(name for name, default in _substitutions().items() if default is None)
    assert without_default == sorted(REQUIRED_FROM_THE_OPERATOR)


def test_every_compose_variable_is_documented_in_the_env_example():
    documented = set(re.findall(r"^#?\s*(\w+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), flags=re.MULTILINE))
    undocumented = sorted(set(_substitutions()) - documented)
    assert undocumented == [], f"variables missing from deployment/.env.example: {undocumented}"


def test_a_stack_env_with_only_the_operators_values_starts(monkeypatch: pytest.MonkeyPatch):
    """The compose defaults alone give valid settings: empty LK_* means the built-in period."""
    _stack_env(monkeypatch)
    settings = Settings()

    assert settings.environment == "production"
    assert settings.retention.lk_a_days is None
    assert settings.retention.lk_h_days is None
    assert settings.retention.backup_rotation_days == 30
    assert settings.retention.result_link_days == 90
    assert (settings.uploads.max_file_size_mb, settings.uploads.max_video_file_size_mb) == (10, 100)
    assert settings.auth.enforce_org_admin_totp is True
    assert settings.auth.allow_unlimited_org_registrations is False
    assert settings.qa_feedback.enabled is False
    # No mail address of anybody: mail is off until the operator names a sender.
    assert settings.mail.mail_from_address == ""
    assert settings.mail.mail_reply_to == ""
    assert settings.mail.enabled is False
    # The links fall back to the one public origin.
    assert settings.mail.public_base_url == "https://rally.example.org"
    assert settings.public_base_url_is_fallback is True


def test_an_operator_value_wins_over_the_default(monkeypatch: pytest.MonkeyPatch):
    _stack_env(
        monkeypatch,
        LK_H_DAYS="730",
        BACKUP_ROTATION_DAYS="45",
        MAX_VIDEO_FILE_SIZE_MB="250",
        PUBLIC_BASE_URL="https://links.example.org/",
    )
    settings = Settings()

    assert settings.retention.lk_h_days == 730
    assert settings.retention.backup_rotation_days == 45
    assert settings.uploads.max_video_file_size_mb == 250
    assert settings.mail.public_base_url == "https://links.example.org"
    assert settings.public_base_url_is_fallback is False


ALL_OPERATOR_VALUES = {
    "SMTP_HOST": "mail.example.org",
    "MAIL_FROM_ADDRESS": "noreply@example.org",
    "MAIL_REPLY_TO": "team@example.org",
    "PUBLIC_BASE_URL": "https://rally.example.org",
}


def _warnings_with_forwarded_allow_ips(
    monkeypatch: pytest.MonkeyPatch, forwarded_allow_ips: str | None, **overrides: str
) -> list[str]:
    """The start warnings with FORWARDED_ALLOW_IPS set by the test, or deleted when it is None."""
    _stack_env(monkeypatch, **overrides)
    if forwarded_allow_ips is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", forwarded_allow_ips)
    return startup_warnings(Settings())


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, ["PUBLIC_BASE_URL is not set", "FORWARDED_ALLOW_IPS is *"]),
        (
            {"SMTP_HOST": "mail.example.org"},
            ["MAIL_FROM_ADDRESS is empty", "PUBLIC_BASE_URL is not set", "FORWARDED_ALLOW_IPS is *"],
        ),
        (
            {"SMTP_HOST": "mail.example.org", "MAIL_FROM_ADDRESS": "noreply@example.org"},
            ["MAIL_REPLY_TO is empty", "PUBLIC_BASE_URL is not set", "FORWARDED_ALLOW_IPS is *"],
        ),
        (ALL_OPERATOR_VALUES, ["FORWARDED_ALLOW_IPS is *"]),
    ],
)
def test_startup_names_what_the_operator_forgot(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], expected: list[str]
):
    warnings = _warnings_with_forwarded_allow_ips(monkeypatch, "*", **overrides)

    assert len(warnings) == len(expected)
    for warning, fragment in zip(warnings, expected, strict=True):
        assert fragment in warning


def test_startup_does_not_warn_about_a_named_proxy_network(monkeypatch: pytest.MonkeyPatch):
    assert _warnings_with_forwarded_allow_ips(monkeypatch, "172.18.0.0/16", **ALL_OPERATOR_VALUES) == []


def test_startup_does_not_warn_about_an_unset_forwarded_allow_ips(monkeypatch: pytest.MonkeyPatch):
    """The App's behaviour when the variable is unset: uvicorn then trusts only 127.0.0.1, and the App does not warn."""
    assert _warnings_with_forwarded_allow_ips(monkeypatch, None, **ALL_OPERATOR_VALUES) == []


def test_the_default_of_the_production_stack_file_is_a_named_network(monkeypatch: pytest.MonkeyPatch):
    """Reads the file's default on purpose: the shipped default must stay a named network, never ``*``."""
    default = _substitutions()["FORWARDED_ALLOW_IPS"]
    assert default is not None
    entries = [entry.strip() for entry in default.split(",")]
    assert entries != [""]
    for entry in entries:
        ipaddress.ip_network(entry, strict=False)

    # _stack_env sets the variable to this default and the call does not override it.
    _stack_env(monkeypatch, **ALL_OPERATOR_VALUES)
    assert os.environ["FORWARDED_ALLOW_IPS"] == default
    assert startup_warnings(Settings()) == []
