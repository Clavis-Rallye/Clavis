"""
Application configuration using Pydantic Settings.

All configuration is loaded from environment variables with validation and fail-fast
behavior on missing required values or invalid configurations.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import Field, PrivateAttr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The .env next to the compose files is a developer convenience, never a source of truth
# for tests. Set CLAVIS_ENV_FILE=none to ignore it (the test suite does, see conftest.py),
# so a local ENFORCE_ORG_ADMIN_TOTP=false cannot turn a red test green or vice versa.
# Any other value names an alternative file. The default is App/.env.
_DEFAULT_ENV_FILE = str(Path(__file__).parent.parent.parent.parent / ".env")
_env_file_setting = os.getenv("CLAVIS_ENV_FILE")
ENV_FILE: str | None = (
    None if (_env_file_setting or "").strip().lower() == "none" else (_env_file_setting or _DEFAULT_ENV_FILE)
)


class DatabaseSettings(BaseSettings):
    """Database connection configuration."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        ...,
        description="PostgreSQL connection URL (postgresql+asyncpg://user:pass@host:port/db)",
    )
    pool_size: int = Field(
        default=10,
        ge=1,
        le=100,
        description="SQLAlchemy connection pool size",
    )
    pool_max_overflow: int = Field(
        default=20,
        ge=0,
        le=100,
        description="Maximum overflow connections beyond pool_size",
    )


class StorageSettings(BaseSettings):
    """S3-compatible object storage configuration."""

    model_config = SettingsConfigDict(
        env_prefix="S3_",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    endpoint: str = Field(
        ...,
        description="S3-compatible store endpoint (host:port)",
    )
    access_key: str = Field(
        ...,
        description="S3-compatible store access key",
    )
    secret_key: str = Field(
        ...,
        description="S3-compatible store secret key",
    )
    bucket: str = Field(
        ...,
        description="Default bucket name for application files",
    )
    secure: bool = Field(
        default=False,
        description="Use TLS for the S3-compatible store connection (true for production)",
    )
    region: str = Field(
        default="us-east-1",
        description=(
            "Region the S3-compatible store signs requests for. It must match the "
            "store's configured region. A mismatch fails with "
            "AuthorizationHeaderMalformed."
        ),
    )

    # Pydantic's own "Field required" error names the field (``endpoint``), not
    # the environment variable an operator has to set. This runs before field
    # validation and fails with the ``S3_*`` names, so a missing variable is
    # actionable from the startup error alone.
    _REQUIRED_ENV_VARS: ClassVar[dict[str, str]] = {
        "endpoint": "S3_ENDPOINT",
        "access_key": "S3_ACCESS_KEY",
        "secret_key": "S3_SECRET_KEY",
        "bucket": "S3_BUCKET",
    }

    @model_validator(mode="before")
    @classmethod
    def name_missing_required_env(cls, data: Any) -> Any:
        """Fail with the missing variable's S3_* name."""
        if isinstance(data, dict):
            missing = [env for field, env in cls._REQUIRED_ENV_VARS.items() if not str(data.get(field) or "").strip()]
            if missing:
                raise ValueError(f"Required environment variable(s) missing: {', '.join(missing)}")
        return data


class AuthSettings(BaseSettings):
    """Authentication and security configuration."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    jwt_secret_key: str = Field(
        ...,
        description="JWT secret key for signing tokens (min 32 chars)",
    )
    session_timeout_minutes: int = Field(
        default=60,
        ge=1,
        le=43200,  # Max 30 days
        description="Session timeout in minutes (inactivity logout)",
    )
    login_rate_limit: str = Field(
        default="10/minute",
        description=(
            "slowapi rate limit for POST /auth/login, per client IP. "
            "Dev/E2E stacks raise this via LOGIN_RATE_LIMIT — CI runs the whole "
            "browser suite from one IP and trips the production default."
        ),
    )
    register_rate_limit: str = Field(
        default="10/minute",
        description=(
            "slowapi rate limit for POST /auth/register, per client IP. Checked before the "
            "password is hashed, so a flood of registrations cannot buy Argon2 work. "
            "Dev/E2E stacks raise this via REGISTER_RATE_LIMIT — every browser spec "
            "registers its own organisation from one IP."
        ),
    )
    enforce_org_admin_totp: bool = Field(
        default=True,
        description="Force org_admin users to enroll TOTP before they can use the app. Disable in dev for easier testing.",
    )
    allow_unlimited_org_registrations: bool = Field(
        default=False,
        description=(
            "Allow the unauthenticated bootstrap registration (new organisation + its "
            "org_admin) to run even after an organisation already exists. Default off: "
            "on a normal deployment bootstrap is a one-time act, and leaving it open lets "
            "anyone mint themselves an org_admin account on a live instance. Turn "
            "on only for an instance that deliberately offers open organisation signup."
        ),
    )
    allow_team_join_by_id: bool = Field(
        default=False,
        description=(
            "Test aid only: keeps POST /teams/{team_id}/join (join by team id, no team "
            "code) reachable for the test suite, which uses it as a setup shortcut. "
            "Must stay off in production — joining a team requires the team's code."
        ),
    )
    password_min_length: int = Field(
        default=8,
        ge=8,
        le=128,
        description="Minimum password length",
    )
    password_criteria_count: int = Field(
        default=3,
        ge=1,
        le=4,
        description="Number of criteria (uppercase/lowercase/digit/special) that must be met",
    )
    max_login_attempts: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Maximum failed login attempts before account lockout",
    )
    lockout_duration_minutes: int = Field(
        default=15,
        ge=1,
        le=43200,  # Max 30 days
        description="Account lockout duration in minutes",
    )

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_length(cls, v: str) -> str:
        """Ensure JWT secret key is at least 32 characters long."""
        if len(v) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters long. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return v


class LoggingSettings(BaseSettings):
    """Logging and audit trail configuration."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    hmac_audit_key: str = Field(
        ...,
        description="HMAC key for audit log integrity chain",
    )
    log_dir: str = Field(
        default="/var/log/clavis",
        description="Directory for log files",
    )
    # The log retention periods. app/logging.py keeps this many daily files
    # per stream, and deployment/scripts/log-cleanup.sh deletes files older
    # than this many days. Both read the same variables.
    audit_log_retention_days: int = Field(
        default=365,
        ge=1,
        le=3650,  # Max 10 years
        description="Audit log retention period in days",
    )
    app_log_retention_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description="Application log retention period in days",
    )
    error_log_retention_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description="Error log retention period in days",
    )
    access_log_retention_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description="Access log retention period in days (the access log holds IP addresses)",
    )

    @field_validator("hmac_audit_key")
    @classmethod
    def validate_hmac_key_length(cls, v: str) -> str:
        """Ensure HMAC audit key is sufficiently long."""
        if len(v) < 32:
            raise ValueError(
                "HMAC_AUDIT_KEY must be at least 32 characters long. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return v


class WebAuthnSettings(BaseSettings):
    """The domain, the name and the public origin of the installation (WEBAUTHN_RP_ID, WEBAUTHN_RP_NAME, WEBAUTHN_ORIGIN)."""

    model_config = SettingsConfigDict(
        env_prefix="WEBAUTHN_",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    rp_id: str = Field(
        ...,
        description="Domain of the installation (e.g. 'clavis.example.de' or 'localhost'). No code reads it in this release.",
    )
    rp_name: str = Field(
        ...,
        description="Name of the installation that an authenticator app shows beside the TOTP code",
    )
    origin: str = Field(
        ...,
        description=(
            "Public origin of the installation (full URL including protocol). The links in mail and the "
            "result link use it when PUBLIC_BASE_URL is empty."
        ),
    )

    @field_validator("origin")
    @classmethod
    def validate_origin_format(cls, v: str) -> str:
        """Ensure origin is a valid URL."""
        if not v.startswith(("http://", "https://")):
            raise ValueError(f"WEBAUTHN_ORIGIN must start with http:// or https://, got: {v}")
        return v


class CORSSettings(BaseSettings):
    """CORS configuration."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000",
        description="Comma-separated list of allowed origins",
    )

    def get_origins_list(self) -> list[str]:
        """Parse CORS origins string into list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


class RetentionSettings(BaseSettings):
    """Data-retention configuration for the daily GDPR retention sweep.

    The LK_A/LK_B/LK_C retention periods themselves are single-sourced from
    ``DeletionClass.retention_days`` (``app/services/gdpr.py``); only knobs that
    have no class-period equivalent live here.

    An empty variable counts as not set. The production compose file passes
    ``LK_A_DAYS: ${LK_A_DAYS:-}``, and without ``env_ignore_empty`` the empty
    string would fail the integer check and stop the API at start.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    abandoned_run_days: int = Field(
        default=60,
        ge=1,
        le=3650,
        description=(
            "A run that is not ended and has had no activity since "
            "coalesce(started_at, created_at) for longer than this many days is "
            "treated as abandoned, so its LK_A guest accounts are swept even "
            "though the run was never explicitly ended. Default 60 — comfortably "
            "beyond any real rally (which lasts hours to days)."
        ),
    )

    # Instance-level overrides for the org-configurable LK_A/LK_B/LK_C periods
    # NULL falls back to the DeletionClass constant.
    # The effective period is resolved org-override → instance-setting → constant
    # in app/services/retention_config.py.
    lk_a_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="Instance default for LK_A (guest access to ended runs). NULL → DeletionClass constant (90).",
    )
    lk_b_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="Instance default for LK_B (chat + raw media). NULL → DeletionClass constant (90).",
    )
    lk_c_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="Instance default for LK_C (run anonymization). NULL → DeletionClass constant (365).",
    )

    lk_h_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description=(
            "Instance default for LK_H (answer text, quiz choices, score comments, "
            "feedback comments). NULL → DeletionClass constant (365)."
        ),
    )

    backup_rotation_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        description=(
            "How many days a backup can still hold data the app already deleted "
            "(default 30, the rotation of deployment/standalone/backup.sh). Two uses. "
            "First, the stated backup period, unless the operator saved one in the "
            "operator console (app/services/backup_retention.py). Second, the lower "
            "bound of an organization's LK_A/LK_B/LK_C/LK_H period "
            "(guardrail: a period may not be shorter than the backup rotation). "
            "The console value does not move "
            "that bound."
        ),
    )

    result_link_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description=(
            "How long a published run-result link stays resolvable, restarted on "
            "every re-publish. Not a Löschklasse, because the link is a capability "
            "and not personal data by itself. The daily retention sweep enforces it."
        ),
    )


class UploadSettings(BaseSettings):
    """Upload size limits for submission media (app/storage.py).

    Two caps, both MB-denominated:
    - ``max_file_size_mb``: the base cap, applied to every upload except
      ``video/*`` MIME types (photos, audio, documents, etc.).
    - ``max_video_file_size_mb``: raised cap for ``video/*`` uploads.

    The multipart parser's per-part cap (``storage.MAX_MULTIPART_PART_SIZE``)
    always tracks whichever of the two is larger, plus 1 MB of header/encoding
    slack.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    max_file_size_mb: int = Field(
        default=10,
        ge=1,
        le=1024,
        description="Maximum upload size in MB for non-video files (default: 10 MB)",
    )
    max_video_file_size_mb: int = Field(
        default=100,
        ge=1,
        le=1024,
        description="Maximum upload size in MB for video/* MIME uploads (default: 100 MB)",
    )


class MailSettings(BaseSettings):
    """Outbound transactional email.

    Mail is *optional infrastructure*: an unconfigured deployment must still
    boot and serve. Leave ``SMTP_HOST`` empty and the app runs with a logging
    transport that records every suppressed message instead of sending it —
    which is also the default for local dev and CI, so a test run can never
    reach a real mailbox.

    Sending needs two values: ``SMTP_HOST`` and ``MAIL_FROM_ADDRESS``. The
    sender address has no default, because only the operator knows which
    address the mail server lets the app use. With ``SMTP_HOST`` set and no
    sender address, the app does not send and says so at start
    (``startup_warnings``).
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    smtp_host: str = Field(
        default="",
        description="SMTP submission host. Empty disables sending (messages are logged instead).",
    )
    smtp_port: int = Field(
        default=465,
        ge=1,
        le=65535,
        description="SMTP submission port (465 implicit TLS, 587 STARTTLS)",
    )
    smtp_tls_mode: str = Field(
        default="ssl",
        description="Transport security: 'ssl' (implicit TLS, port 465), 'starttls', or 'none' (dev only)",
    )
    smtp_username: str = Field(default="", description="SMTP auth username")
    smtp_password: str = Field(default="", description="SMTP auth password — never logged, never returned by the API")
    smtp_timeout_seconds: int = Field(
        default=20,
        ge=1,
        le=120,
        description="Socket timeout for a single send attempt",
    )

    mail_from_address: str = Field(
        default="",
        description="Envelope + header From address. Empty: the app does not send mail",
    )
    mail_from_name: str = Field(
        default="Clavis",
        description="Display name in the From header",
    )
    mail_reply_to: str = Field(
        default="",
        description=(
            "Reply-To address. Set it to a mailbox a person reads: a no-reply sender that "
            "silently drops answers is hostile to the people we mail. Empty: no Reply-To header."
        ),
    )

    public_base_url: str = Field(
        default="",
        description=(
            "Absolute public origin of the frontend, used to build links in emails and the "
            "result link. Never derived from a request header — a Host-header injection would "
            "rewrite reset links. Empty: WEBAUTHN_ORIGIN, the one public origin of a "
            "single-origin installation (``Settings._public_base_url_from_webauthn_origin``)."
        ),
    )

    invitation_expiry_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="How long an invitation link stays valid",
    )

    @field_validator("smtp_tls_mode")
    @classmethod
    def validate_tls_mode(cls, v: str) -> str:
        """Restrict the transport-security mode to the three supported values."""
        allowed = {"ssl", "starttls", "none"}
        normalized = v.strip().lower()
        if normalized not in allowed:
            raise ValueError(f"SMTP_TLS_MODE must be one of {sorted(allowed)}, got: {v}")
        return normalized

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, v: str) -> str:
        """Ensure the base URL is absolute and carries no trailing slash. Empty means not set."""
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(f"PUBLIC_BASE_URL must start with http:// or https://, got: {v}")
        return v.rstrip("/")

    @field_validator("mail_from_address", "mail_reply_to")
    @classmethod
    def strip_address(cls, v: str) -> str:
        """A blank address counts as not set."""
        return v.strip()

    @property
    def enabled(self) -> bool:
        """Whether a real SMTP transport is configured: a host and a sender address."""
        return bool(self.smtp_host.strip()) and bool(self.mail_from_address)


class QaFeedbackSettings(BaseSettings):
    """The feedback module for test sessions.

    Off by default. While off, every ``/api/qa`` endpoint answers 404 and the
    frontend never loads the feedback bar. A stand or demo system switches it on
    with ``QA_FEEDBACK_ENABLED=true`` and gives the session kit a token.
    """

    model_config = SettingsConfigDict(
        env_prefix="QA_FEEDBACK_",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = Field(
        default=False,
        description="Switch the feedback bar and the /api/qa endpoints on",
    )
    token: str = Field(
        default="",
        description=(
            "The session kit's credential, sent as X-QA-Token. At least 32 characters. "
            "Empty leaves the operator session as the only reader."
        ),
    )
    retention_days: int = Field(
        default=30,
        ge=1,
        le=3650,
        description="Notes older than this are deleted by the daily retention sweep",
    )
    note_rate_limit: str = Field(
        default="120/minute",
        description="Anonymous note limit per client address (slowapi syntax)",
    )

    @field_validator("token")
    @classmethod
    def validate_token_length(cls, v: str) -> str:
        """A token short enough to guess is worse than none."""
        v = v.strip()
        if v and len(v) < 32:
            raise ValueError("QA_FEEDBACK_TOKEN must be at least 32 characters long, or empty")
        return v


# The public Overpass API, run by FOSSGIS e.V. in Germany.
OVERPASS_PUBLIC_URL = "https://overpass-api.de/api/interpreter"

# The default map tiles, served by FOSSGIS e.V. in Germany. The tile server of
# the OpenStreetMap Foundation runs behind Fastly in the USA, so an
# installation in Germany serves its maps from here instead.
TILE_URL_DEFAULT = "https://tile.openstreetmap.de/{z}/{x}/{y}.png"


def parse_lat_lng(value: str) -> tuple[float, float]:
    """Read ``"48.3705,10.8978"`` as latitude and longitude, or raise ValueError."""
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise ValueError(f"expected 'lat,lng', got {value!r}")
    lat, lng = float(parts[0]), float(parts[1])
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError(f"latitude or longitude out of range in {value!r}")
    return lat, lng


class GeocoderSettings(BaseSettings):
    """The address search of the station form.

    The search asks the regional place index first. The operator fills that
    index with ``python -m app.cli geo import``. ``upstream`` names the outside
    service that answers what the index does not hold. Every request to it
    leaves from this server, so the service sees the server and the search
    text, never the creator's browser.

    - ``none``: the default. No request leaves the server, and the search
      answers from the imported index only. A fresh install sends no search
      text to a third party unless the operator chooses one.
    - ``nominatim``: the public Nominatim instance or ``upstream_url``. Its
      public instance forbids suggestions while typing, so it is asked only on Enter.
    - ``photon``: a Photon instance, public or self-hosted. It may answer
      while the creator types.

    ``map_centre`` is where the map of the station form starts before a pin
    exists, as ``lat,lng``. Empty means the middle of the imported index, and
    without an index the frontend's own fallback.

    ``overpass_url`` is the Overpass API that ``python -m app.cli geo import``
    downloads a region from. Empty means the public instance of FOSSGIS e.V.
    """

    model_config = SettingsConfigDict(
        env_prefix="GEOCODER_",
        case_sensitive=False,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    upstream: Literal["nominatim", "photon", "none"] = Field(
        default="none",
        description="Outside search service: nominatim, photon or none",
    )
    upstream_url: str = Field(
        default="",
        description="Base URL of the service. Empty means its public instance",
    )
    contact: str = Field(
        default="",
        description="Contact of the operator, sent in the User-Agent as the public services ask",
    )
    map_centre: str = Field(
        default="",
        description="Start point of the station map as 'lat,lng'. Empty means the middle of the place index",
    )
    overpass_url: str = Field(
        default="",
        description="Overpass API endpoint of the place import. Empty means the public instance of FOSSGIS e.V.",
    )
    map_tile_url: str = Field(
        default="",
        description=(
            "Tile server of both maps, with {z}, {x} and {y}. Empty means the FOSSGIS tiles in Germany, "
            "https://tile.openstreetmap.de/{z}/{x}/{y}.png"
        ),
    )
    map_tile_attribution: str = Field(
        default="",
        description="Attribution of the map tiles as HTML. Empty means the attribution the frontend derives from the tile host",
    )

    @field_validator("map_tile_url")
    @classmethod
    def validate_map_tile_url(cls, v: str) -> str:
        """A tile address the maps cannot use stops the start, instead of a map without tiles."""
        v = v.strip()
        if not v:
            return TILE_URL_DEFAULT
        if not v.startswith(("https://", "http://")):
            raise ValueError("GEOCODER_MAP_TILE_URL must start with https:// or http://")
        if not all(part in v for part in ("{z}", "{x}", "{y}")):
            raise ValueError("GEOCODER_MAP_TILE_URL must hold {z}, {x} and {y}")
        return v

    @field_validator("overpass_url")
    @classmethod
    def validate_overpass_url(cls, v: str) -> str:
        """An endpoint must be a web address. A typo shows at start, not at the next import."""
        v = v.strip()
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("GEOCODER_OVERPASS_URL must start with https:// or http://")
        return v

    @property
    def overpass_endpoint(self) -> str:
        """The Overpass API the import asks: the setting, else the public instance."""
        return self.overpass_url or OVERPASS_PUBLIC_URL

    @field_validator("map_centre")
    @classmethod
    def validate_map_centre(cls, v: str) -> str:
        """A start point the map cannot show stops the start, instead of a map of the open sea."""
        v = v.strip()
        if v:
            parse_lat_lng(v)
        return v

    @property
    def centre(self) -> tuple[float, float] | None:
        """The start point of the setting, or None when the setting is empty."""
        return parse_lat_lng(self.map_centre) if self.map_centre else None

    @property
    def base_url(self) -> str:
        """The base URL without a trailing slash."""
        public = {"nominatim": "https://nominatim.openstreetmap.org", "photon": "https://photon.komoot.io", "none": ""}
        return (self.upstream_url or public[self.upstream]).rstrip("/")


class Settings(BaseSettings):
    """Main application settings aggregating all configuration sections."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Nested settings
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    webauthn: WebAuthnSettings = Field(default_factory=WebAuthnSettings)
    cors: CORSSettings = Field(default_factory=CORSSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    uploads: UploadSettings = Field(default_factory=UploadSettings)
    mail: MailSettings = Field(default_factory=MailSettings)
    qa_feedback: QaFeedbackSettings = Field(default_factory=QaFeedbackSettings)
    geocoder: GeocoderSettings = Field(default_factory=GeocoderSettings)

    # Environment
    environment: str = Field(
        default="development",
        description="Application environment (development, staging, production)",
    )

    # Instance-wide product name. One value for every place that shows it: the
    # frontend reads it once from GET /api/public/config at boot, and
    # transactional mail interpolates it into the signature. Never per organization.
    brand_name: str = Field(
        default="Clavis",
        description="Instance-wide product name shown in the app shell and transactional mail.",
    )

    # When enabled, SQLAlchemy engine logs are routed through the structlog
    # pipeline (one rendered line). Off by default everywhere, including dev,
    # so the every-10s health-check `SELECT 1` probe does not spam the logs.
    # Opt in with SQL_ECHO=1.
    sql_echo: bool = Field(
        default=False,
        description="Emit SQLAlchemy engine SQL logs through structlog. Off by default.",
    )

    @model_validator(mode="after")
    def _public_base_url_from_webauthn_origin(self) -> "Settings":
        """An unset PUBLIC_BASE_URL takes WEBAUTHN_ORIGIN.

        The app is single-origin: the browser reaches the frontend and the API
        at one origin, and WEBAUTHN_ORIGIN is that public origin. It is
        required, so the fallback always exists. An operator who serves mail
        links from another origin sets PUBLIC_BASE_URL.
        """
        if not self.mail.public_base_url:
            self.mail.public_base_url = self.webauthn.origin.rstrip("/")
            self._public_base_url_is_fallback = True
        return self

    # True when PUBLIC_BASE_URL was not set and the WebAuthn origin stands in.
    _public_base_url_is_fallback: bool = PrivateAttr(default=False)

    @property
    def public_base_url_is_fallback(self) -> bool:
        """Whether the public origin comes from WEBAUTHN_ORIGIN, not from PUBLIC_BASE_URL."""
        return self._public_base_url_is_fallback


def startup_warnings(settings: "Settings") -> list[str]:
    """Settings an operator most likely forgot, as sentences for the start log.

    None of them stops the API: an installation without mail must still start.
    """
    warnings: list[str] = []
    mail = settings.mail
    if mail.smtp_host.strip() and not mail.mail_from_address:
        warnings.append(
            "SMTP_HOST is set, but MAIL_FROM_ADDRESS is empty. The app sends no mail until "
            "MAIL_FROM_ADDRESS names the sender address the mail server accepts."
        )
    if mail.enabled and not mail.mail_reply_to:
        warnings.append(
            "MAIL_REPLY_TO is empty. Mail goes out without a Reply-To header, so answers go to "
            "MAIL_FROM_ADDRESS. Set it to a mailbox a person reads."
        )
    if settings.public_base_url_is_fallback:
        warnings.append(
            f"PUBLIC_BASE_URL is not set. Links in mail and the result link use WEBAUTHN_ORIGIN "
            f"({mail.public_base_url})."
        )
    # uvicorn reads the variable itself (app/client_address.py) and trusts every peer only for the exact value
    # "*". This check also warns for "*" written with spaces, which uvicorn 0.51 does not trust: the warning is
    # cautious, because the next uvicorn may strip the value first.
    forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS")
    if forwarded_allow_ips is not None and [e.strip() for e in forwarded_allow_ips.split(",")] == ["*"]:
        warnings.append(
            "FORWARDED_ALLOW_IPS is *, so any client can choose its own address for the audit and every limit: "
            "set it to the address or network of the reverse proxy, for example 172.20.0.0/16."
        )
    return warnings


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings, so the environment is parsed once.

    Raises ``ValueError`` that lists every missing or invalid variable.
    """
    try:
        return Settings()
    except ValidationError as e:
        error_messages = []
        for error in e.errors():
            field = " -> ".join(str(loc) for loc in error["loc"])
            msg = error["msg"]
            error_messages.append(f"  • {field}: {msg}")

        raise ValueError(
            "Configuration validation failed. The following issues were found:\n"
            + "\n".join(error_messages)
            + "\n\nPlease check your .env file or environment variables. "
            + "See .env.example for required variables and their formats."
        ) from e


settings = get_settings()
