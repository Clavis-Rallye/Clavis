"""
Pydantic schemas for API request and response models.

These schemas define the structure of data sent to and received from the API endpoints.
They provide validation, serialization, and documentation.
"""

import contextlib
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, EmailStr, Field, field_validator, model_validator

from app.models.challenge import ChallengeType
from app.models.feed_event import FeedEventType
from app.models.run import RunState

# Email addresses are matched case-insensitively. Normalize every inbound email
# to lowercase at the validation boundary, so storage and lookups never see
# mixed case. EmailStr already lowercases the domain part. This also lowercases
# the local part.
NormalizedEmail = Annotated[EmailStr, AfterValidator(str.lower)]

# A content language of an organization is a free code, not one of a list the server ships.
# The frontend suggests codes, but a code outside its suggestions stays valid. The server
# checks the shape only: a language tag of two or three ASCII letters, then parts of two to
# eight ASCII letters or digits joined by hyphens, at most 35 characters. The code is stored
# as it is sent.
LanguageTag = Annotated[str, Field(max_length=35, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")]
MAX_ORGANIZATION_LANGUAGES = 50


def _refuse_duplicate_languages(value: list[str] | None) -> list[str] | None:
    """Refuse a list that names one language twice, compared without regard to case."""
    if value is not None and len({code.lower() for code in value}) != len(value):
        raise ValueError("languages must not contain a duplicate")
    return value


def lift_created_by_display_name(data: Any) -> Any:
    """Lift `created_by.display_name` onto `created_by_display_name`.

    Works with both ORM model instances (where `created_by` is a relationship)
    and raw dicts (already-flat shape).
    """
    if (
        hasattr(data, "created_by")
        and getattr(data, "created_by", None)
        and not getattr(data, "created_by_display_name", None)
    ):
        # Set the attribute on the object so from_attributes picks it up.
        with contextlib.suppress(AttributeError, TypeError):
            object.__setattr__(
                data,
                "created_by_display_name",
                data.created_by.display_name,
            )
    return data


# ============================================================================
# Auth Schemas
# ============================================================================


class RegisterRequest(BaseModel):
    """Request body for user registration, in one of two modes.

    - **Bootstrap mode** (`organization_name` set): creates a new organisation
      and an `org_admin` user. The operator setup flow at `/register/organization`
      uses it.
    - **Self-signup mode** (`organization_name` omitted): registers a
      `participant` user in the organisation named by `organization_slug`, which
      must already exist.
    """

    organization_name: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Name of the organisation to create (bootstrap mode). Omit for self-signup into an existing organisation."
        ),
        examples=["Stadtverwaltung Augsburg"],
    )
    organization_slug: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z0-9-]+$",
        description=(
            "URL-safe organisation identifier. In bootstrap mode this is the "
            "slug for the new org; in self-signup mode it identifies the "
            "existing org to join."
        ),
        examples=["stadtverwaltung-augsburg"],
    )
    email: NormalizedEmail = Field(
        ...,
        description="User email address",
        examples=["admin@augsburg.example"],
    )
    password: str = Field(
        ...,
        min_length=1,  # Let endpoint validate against policy
        max_length=128,
        description="User password (must meet policy)",
        examples=["SecurePassword123!"],
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="User display name",
        examples=["Admin User"],
    )


class RegisterResponse(BaseModel):
    """Response after successful registration."""

    user_id: UUID = Field(..., description="UUID of the created user")
    organization_id: UUID = Field(..., description="UUID of the created organization")
    email: str = Field(..., description="User email")
    display_name: str = Field(..., description="User display name")
    role: str = Field(
        ...,
        description="User role ('org_admin' for bootstrap, 'participant' for self-signup)",
    )
    requires_2fa: bool = Field(
        default=False,
        description=(
            "Whether the newly registered user must complete a 2FA setup before "
            "the next protected action. True when `enforce_org_admin_totp` is on "
            "and the user is an org_admin."
        ),
    )
    setup_token: str | None = Field(
        default=None,
        description=(
            "Restricted JWT for the 2FA setup. Present instead of a session "
            "when `requires_2fa` is true: the org_admin must enroll TOTP "
            "before they get a session. Expires in 30 minutes."
        ),
    )
    message: str = Field(
        default="Registration successful",
        description="Success message",
    )


class LoginRequest(BaseModel):
    """Request body for user login."""

    email: NormalizedEmail = Field(
        ...,
        description="User email address",
        examples=["admin@augsburg.example"],
    )
    password: str = Field(
        ...,
        description="User password",
        examples=["SecurePassword123!"],
    )


class LoginResponse(BaseModel):
    """Response after successful login (or 2FA required/setup required)."""

    status: str = Field(
        ...,
        description="Login status: 'success', '2fa_required', or '2fa_setup_required'",
        examples=["success", "2fa_required", "2fa_setup_required"],
    )
    user_id: UUID | None = Field(
        None,
        description="UUID of the user (present if status='success')",
    )
    email: str | None = Field(
        None,
        description="User email (present if status='success')",
    )
    display_name: str | None = Field(
        None,
        description="User display name (present if status='success')",
    )
    role: str | None = Field(
        None,
        description="User role (present if status='success')",
    )
    organization_id: UUID | None = Field(
        None,
        description="Organization UUID (present if status='success')",
    )
    requires_2fa: bool | None = Field(
        None,
        description="Whether 2FA is required (present if status='2fa_required')",
    )
    setup_token: str | None = Field(
        None,
        description="Restricted JWT token for 2FA setup (present if status='2fa_setup_required', expires in 30 minutes)",
    )
    setup_url: str | None = Field(
        None,
        description="URL for 2FA setup (present if status='2fa_setup_required')",
    )
    message: str = Field(
        ...,
        description="Status message",
        examples=["Login successful", "2FA required", "2FA setup required for organization administrators"],
    )


class CurrentUserResponse(BaseModel):
    """Response for the current authenticated user."""

    id: UUID = Field(..., description="User UUID")
    # Guests have no email (anonymous join via code). Permit None so /auth/me
    # can serve guest sessions without 500ing on schema validation.
    email: str | None = Field(..., description="User email (null for guests)")
    display_name: str = Field(..., description="User display name")
    role: str = Field(..., description="User role")
    organization_id: UUID = Field(..., description="Organization UUID")
    organization_slug: str = Field(..., description="Organization slug (for org-scoped API routes)")
    is_active: bool = Field(..., description="Whether the user account is active")
    totp_enabled: bool = Field(..., description="Whether TOTP 2FA is enrolled")
    last_login: datetime | None = Field(None, description="Last successful login timestamp")
    created_at: datetime = Field(..., description="Account creation timestamp")

    class Config:
        from_attributes = True


class LogoutResponse(BaseModel):
    """Response after logout."""

    message: str = Field(
        default="Logout successful",
        description="Success message",
    )


# ============================================================================
# Instance Operator Auth Schemas
# ============================================================================


class InstanceLoginRequest(BaseModel):
    """Request body for instance-operator login."""

    email: NormalizedEmail = Field(
        ...,
        description="Operator email address",
        examples=["operator@example.org"],
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Operator password",
    )
    totp_code: str | None = Field(
        default=None,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit TOTP code (required once the TOTP setup is confirmed)",
        examples=["123456"],
    )


class InstanceLoginResponse(BaseModel):
    """Response for instance-operator login.

    ``totp_setup_required`` with a ``provisioning_uri`` is returned ONCE on
    the first login after account creation; afterwards the code is confirmed
    via ``POST /api/instance/auth/totp/confirm`` and login requires it.
    """

    status: str = Field(
        ...,
        description="'success' or 'totp_setup_required'",
        examples=["success", "totp_setup_required"],
    )
    totp_setup_required: bool = Field(
        default=False,
        description="Whether the operator must complete the TOTP setup first",
    )
    provisioning_uri: str | None = Field(
        default=None,
        description="otpauth:// provisioning URI — returned only on the first login",
    )
    email: str | None = Field(
        default=None,
        description="Operator email (present if status='success')",
    )


class InstanceTotpConfirmRequest(BaseModel):
    """Request body to confirm the instance-operator TOTP setup."""

    email: NormalizedEmail = Field(
        ...,
        description="Operator email address",
        examples=["operator@example.org"],
    )
    password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Operator password",
    )
    totp_code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit TOTP code scanned from the provisioning URI",
        examples=["123456"],
    )


class InstanceTotpConfirmResponse(BaseModel):
    """Response after confirming the TOTP setup (operator is logged in)."""

    status: str = Field(
        default="success",
        description="Confirmation status",
    )
    email: str = Field(
        ...,
        description="Operator email",
    )


class InstanceMeResponse(BaseModel):
    """Response for the current instance operator."""

    id: UUID = Field(..., description="Operator UUID")
    email: str = Field(..., description="Operator email")
    totp_confirmed: bool = Field(..., description="Whether the TOTP setup is confirmed")


class InstanceLogoutResponse(BaseModel):
    """Response after instance-operator logout."""

    message: str = Field(
        default="Logout successful",
        description="Success message",
    )


class InstancePasswordChangeRequest(BaseModel):
    """Request body for the signed-in operator's own password change."""

    old_password: str = Field(
        ...,
        description="Current password for verification",
    )
    new_password: str = Field(
        ...,
        min_length=1,  # Let the endpoint validate against the policy
        max_length=128,
        description="New password (must meet the password policy)",
    )


class InstancePasswordChangeResponse(BaseModel):
    """Response after a successful operator password change."""

    message: str = Field(
        default="Password changed successfully",
        description="Success message",
    )


# ============================================================================
# Instance Organization Schemas
# ============================================================================


class InstanceOrganizationListItem(BaseModel):
    """One organization in the operator's list — counts only, never member data."""

    name: str = Field(..., description="Organization name")
    slug: str = Field(..., description="URL-safe unique identifier")
    content_languages: list[str] = Field(..., description="Enabled locale codes")
    created_at: datetime = Field(..., description="Creation timestamp")
    is_archived: bool = Field(..., description="Whether the operator archived the organization")
    user_count: int = Field(..., description="Non-deleted users in the organization")
    rallye_count: int = Field(..., description="Non-deleted rallye templates")
    run_count: int = Field(..., description="Non-deleted runs")
    org_admin_count: int = Field(..., description="Non-deleted users with the org_admin role")
    pending_admin_invitation_count: int = Field(..., description="Open org_admin invitations, never their addresses")
    active_run_count: int = Field(..., description="Non-deleted runs currently in the active state")
    last_activity_at: datetime | None = Field(
        None, description="Latest submission or run start (null when neither exists)"
    )
    deletion_due_at: datetime | None = Field(
        None, description="When a scheduled deletion will run (null = none scheduled)"
    )


class InstanceOrganizationListResponse(BaseModel):
    """Response for GET /api/instance/organizations."""

    items: list[InstanceOrganizationListItem] = Field(..., description="Every organization")
    total: int = Field(..., description="Total number of organizations")


class InstanceOrganizationCreateRequest(BaseModel):
    """Request body for POST /api/instance/organizations."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Organization name",
        examples=["Stadtverwaltung Augsburg"],
    )
    slug: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z0-9-]+$",
        description="URL-safe unique identifier (same rules as bootstrap registration)",
        examples=["stadtverwaltung-augsburg"],
    )
    content_languages: list[str] = Field(
        ...,
        min_length=1,
        description="Enabled locale codes; the first one is the language of the first-admin invitation",
        examples=[["de", "en"]],
    )
    first_admin_email: NormalizedEmail = Field(
        ...,
        description="Email of the first org admin — receives the invitation mail",
        examples=["admin@augsburg.example"],
    )


class InstanceOrganizationResponse(BaseModel):
    """Response after create/archive/restore — no counts, no member data."""

    id: UUID = Field(..., description="Organization UUID")
    name: str = Field(..., description="Organization name")
    slug: str = Field(..., description="URL-safe unique identifier")
    content_languages: list[str] = Field(..., description="Enabled locale codes")
    created_at: datetime = Field(..., description="Creation timestamp")
    is_archived: bool = Field(..., description="Whether the organization is archived")


class InstanceOrganizationActionResponse(BaseModel):
    """Response after archive/restore."""

    slug: str = Field(..., description="URL-safe unique identifier")
    is_archived: bool = Field(..., description="Archive state after the action")
    message: str = Field(..., description="What happened")


class InstanceOrganizationUpdateRequest(BaseModel):
    """Request body for PATCH /api/instance/organizations/{slug}.

    Both fields are optional. The slug is never accepted: it stays fixed for the
    life of the organization. The endpoint refuses a blank name and an
    unsupported or empty language list with a coded error, not with a field
    constraint, so the client always gets ``error_code`` instead of FastAPI's
    plain validation body.
    """

    name: str | None = Field(
        None,
        max_length=255,
        description="New organization name",
        examples=["Stadtverwaltung Augsburg"],
    )
    content_languages: list[str] | None = Field(
        None,
        description="New enabled locale codes",
        examples=[["de", "en"]],
    )


class InstanceOrganizationDeletionRequest(BaseModel):
    """Request body for POST /api/instance/organizations/{slug}/deletion.

    ``confirm_slug`` must match the organization's own slug exactly. The
    operator retypes it to confirm a hard delete that cannot be undone.
    """

    confirm_slug: str = Field(
        ...,
        description="The organization's own slug, retyped to confirm the deletion",
        examples=["stadtverwaltung-augsburg"],
    )


class InstanceOrganizationDeletionResponse(BaseModel):
    """Response after scheduling an organization's deletion."""

    slug: str = Field(..., description="URL-safe unique identifier")
    deletion_due_at: datetime = Field(..., description="When the scheduled deletion will run")
    message: str = Field(default="Organization deletion scheduled", description="What happened")


class InstanceOrgAdminInvitationRequest(BaseModel):
    """Request body for POST /api/instance/organizations/{slug}/admin-invitations."""

    email: NormalizedEmail = Field(
        ...,
        description="Address the org-admin invitation is sent to",
        examples=["neue.admin@augsburg.example"],
    )
    language: Literal["de", "en"] | None = Field(
        None,
        description="Language of the invitation mail; defaults to the organization's first content language",
    )
    replace_admin_ids: list[UUID] = Field(
        default_factory=list,
        description="Current org admins the invited person takes over from. They are deactivated when the invitation is accepted",
    )


class InstanceOrgAdminSummary(BaseModel):
    """One org admin as the operator sees it, to pick whom an invitation replaces.

    This is a narrow exception to "the operator sees no people": the operator
    must name whom to replace. It shows org admins only, and only these fields.
    """

    id: UUID
    email: str | None
    display_name: str
    is_active: bool
    last_login: datetime | None


class InstanceOrgAdminListResponse(BaseModel):
    items: list[InstanceOrgAdminSummary]


class TakeoverNoticeResponse(BaseModel):
    """What a new admin took over: the accounts an operator invitation replaced."""

    accepted_at: datetime | None = None
    replaced: list[InstanceOrgAdminSummary] = Field(default_factory=list)


class InstanceOrgAdminInvitationResponse(BaseModel):
    """Response after inviting (or renewing an invitation for) an organization admin.

    Carries no counts. The operator's list holds the aggregates.
    """

    id: UUID = Field(..., description="Invitation UUID")
    email: str = Field(..., description="Address the invitation was sent to")
    language: str = Field(..., description="Language the invitation mail is written in")
    invited_by_name: str = Field(..., description="Name shown to the invitee as the inviter (the instance brand)")
    created_at: datetime = Field(..., description="Creation timestamp (unchanged by a renewal)")
    expires_at: datetime = Field(..., description="When the invitation link expires")
    mail_delivered: bool = Field(
        True,
        description="Whether this deployment could hand the mail to a transport",
    )


class PasswordChangeRequest(BaseModel):
    """Request body for password change."""

    old_password: str = Field(
        ...,
        description="Current password for verification",
    )
    new_password: str = Field(
        ...,
        min_length=1,  # Let endpoint validate against policy
        max_length=128,
        description="New password (must meet policy)",
    )


class PasswordChangeResponse(BaseModel):
    """Response after successful password change."""

    message: str = Field(
        default="Password changed successfully",
        description="Success message",
    )


class PasswordForgotRequest(BaseModel):
    """Request body for password reset initiation (forgot password)."""

    email: NormalizedEmail = Field(
        ...,
        description="Email address of the account to reset",
        examples=["user@augsburg.example"],
    )


class PasswordForgotResponse(BaseModel):
    """Response after password reset request."""

    message: str = Field(
        default="If the email exists, a password reset link has been sent",
        description="Success message (generic for security)",
    )


class PasswordResetRequest(BaseModel):
    """Request body for password reset completion."""

    token: str = Field(
        ...,
        description="Password reset token from email/log",
        min_length=1,  # Let endpoint validate token format
        max_length=100,
    )
    new_password: str = Field(
        ...,
        min_length=1,  # Let endpoint validate against policy
        max_length=128,
        description="New password (must meet policy)",
    )


class PasswordResetResponse(BaseModel):
    """Response after successful password reset."""

    message: str = Field(
        default="Password has been reset successfully",
        description="Success message",
    )


class ProfileUpdateRequest(BaseModel):
    """Request body for updating own profile."""

    display_name: str | None = Field(
        None,
        min_length=1,
        max_length=100,
        description="New display name",
        examples=["Max Mustermann"],
    )


class ProfileUpdateResponse(BaseModel):
    """Response after profile update."""

    id: UUID = Field(..., description="User UUID")
    email: str | None = Field(..., description="User email (null for guests)")
    display_name: str = Field(..., description="Updated display name")
    role: str = Field(..., description="User role")
    organization_id: UUID = Field(..., description="Organization UUID")
    message: str = Field(
        default="Profile updated successfully",
        description="Success message",
    )

    class Config:
        from_attributes = True


# ============================================================================
# 2FA Schemas
# ============================================================================


class TotpSetupResponse(BaseModel):
    """Response after initiating TOTP 2FA setup."""

    secret: str = Field(
        ...,
        description="Base32-encoded TOTP secret (store securely)",
        examples=["JBSWY3DPEHPK3PXP"],
    )
    provisioning_uri: str = Field(
        ...,
        description="otpauth:// URI for QR code generation",
        examples=["otpauth://totp/Clavis:user@example.com?secret=..."],
    )
    recovery_codes: list[str] = Field(
        ...,
        description="8 single-use recovery codes (display once, user must save)",
        examples=[["ABC12345", "DEF67890", "GHI23456", "JKL78901", "MNO34567", "PQR89012", "STU45678", "VWX90123"]],
    )
    message: str = Field(
        default="TOTP setup initiated. Save recovery codes securely.",
        description="Instructions for user",
    )


class TotpVerifyRequest(BaseModel):
    """Request body to verify and enable TOTP 2FA."""

    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6-digit TOTP code from authenticator app",
        examples=["123456"],
    )


class TotpVerifyResponse(BaseModel):
    """Response after successfully enabling TOTP 2FA.

    When called with a 2fa_setup token (from org_admin login without TOTP),
    this response also includes login completion fields: user_id, email,
    display_name, role, and status.
    """

    message: str = Field(
        default="TOTP 2FA enabled successfully",
        description="Success message",
    )
    # Optional fields for when completing login via setup token
    status: str | None = Field(
        None,
        description="Login status (present when completing 2FA setup from login flow)",
    )
    user_id: UUID | None = Field(
        None,
        description="UUID of the authenticated user (present when completing login)",
    )
    email: str | None = Field(
        None,
        description="User email (present when completing login)",
    )
    display_name: str | None = Field(
        None,
        description="User display name (present when completing login)",
    )
    role: str | None = Field(
        None,
        description="User role (present when completing login)",
    )


class TotpDisableRequest(BaseModel):
    """Request body to disable TOTP 2FA."""

    password: str = Field(
        ...,
        description="Current password for confirmation",
        examples=["MyCurrentPassword123!"],
    )


class TotpDisableResponse(BaseModel):
    """Response after successfully disabling TOTP 2FA."""

    message: str = Field(
        default="TOTP has been disabled",
        description="Success message",
    )


class TotpAuthenticateRequest(BaseModel):
    """Request body to complete login with TOTP code or recovery code."""

    email: NormalizedEmail = Field(
        ...,
        description="User email address (from login step)",
        examples=["admin@augsburg.example"],
    )
    code: str = Field(
        ...,
        min_length=6,
        max_length=8,
        description="6-digit TOTP code or 8-character recovery code",
        examples=["123456", "ABC12345"],
    )


class TotpAuthenticateResponse(BaseModel):
    """Response after successful 2FA authentication."""

    status: str = Field(
        default="success",
        description="Authentication status",
    )
    user_id: UUID = Field(
        ...,
        description="UUID of the authenticated user",
    )
    email: str = Field(
        ...,
        description="User email",
    )
    display_name: str = Field(
        ...,
        description="User display name",
    )
    role: str = Field(
        ...,
        description="User role",
    )
    recovery_code_used: bool | None = Field(
        None,
        description="True if recovery code was used instead of TOTP",
    )
    remaining_recovery_codes: int | None = Field(
        None,
        description="Number of recovery codes remaining (if recovery code was used)",
    )
    message: str = Field(
        default="2FA authentication successful",
        description="Success message",
    )


# ============================================================================
# Error Schemas
# ============================================================================


class ErrorResponse(BaseModel):
    """Standard error response format."""

    detail: str = Field(
        ...,
        description="Error message",
        examples=["Invalid credentials", "Account locked"],
    )
    error_code: str | None = Field(
        None,
        description="Optional error code for programmatic handling",
        examples=["ACCOUNT_LOCKED", "INVALID_PASSWORD"],
    )


class ValidationErrorResponse(BaseModel):
    """Validation error response format."""

    detail: str = Field(
        ...,
        description="Error message",
    )
    errors: list[str] = Field(
        ...,
        description="List of validation error messages",
        examples=[["Password must be at least 8 characters long"]],
    )


# ============================================================================
# Organization Schemas
# ============================================================================


class OrganizationResponse(BaseModel):
    """Response model for organization data."""

    id: UUID = Field(..., description="Organization UUID")
    name: str = Field(..., description="Organization name")
    slug: str = Field(..., description="URL-safe unique identifier")
    languages: list[str] = Field(..., description="Enabled locale codes")
    settings: dict[str, Any] = Field(..., description="Organization-specific configuration")
    settings_unreadable: bool = Field(
        False,
        description="True when the stored settings cannot be read: `settings` is then empty, "
        "and a PUT with whole settings replaces the stored value",
    )
    legal_imprint: str | None = Field(None, description="Imprint markdown override (NULL → frontend bundled copy)")
    legal_privacy: str | None = Field(None, description="Privacy markdown override (NULL → frontend bundled copy)")
    legal_accessibility: str | None = Field(
        None, description="Accessibility markdown override (NULL → frontend bundled copy)"
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class OrganizationUpdateRequest(BaseModel):
    """Request body for updating an organization."""

    name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Organization name",
    )
    slug: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        pattern=r"^[a-z0-9-]+$",
        description="URL-safe unique identifier",
    )
    languages: list[LanguageTag] | None = Field(
        None,
        max_length=MAX_ORGANIZATION_LANGUAGES,
        description="Enabled locale codes: language tags such as 'de' or 'pt-BR', no duplicate",
    )
    settings: dict[str, Any] | None = Field(
        None,
        description="Organization-specific configuration",
    )

    _no_duplicate_languages = field_validator("languages")(_refuse_duplicate_languages)

    legal_imprint: str | None = Field(None, max_length=20000, description="Imprint markdown override")
    legal_privacy: str | None = Field(None, max_length=20000, description="Privacy markdown override")
    legal_accessibility: str | None = Field(None, max_length=20000, description="Accessibility markdown override")


class OrganizationDeleteResponse(BaseModel):
    """Response after organization deletion."""

    message: str = Field(
        default="Organization deleted successfully",
        description="Success message",
    )


# ============================================================================
# Rallye Schemas
# ============================================================================


class RallyeCreateRequest(BaseModel):
    """Request body for creating a new rallye."""

    title: dict[str, str] = Field(
        ...,
        description="Multilingual title",
        examples=[{"de": "Clavis", "en": "Clavis"}],
    )
    description: dict[str, str] = Field(
        ...,
        description="Multilingual description",
        examples=[{"de": "Eine spannende Tour durch die Stadt", "en": "An exciting tour through the city"}],
    )


class RallyeUpdateRequest(BaseModel):
    """Request body for updating a rallye."""

    title: dict[str, str] | None = Field(
        None,
        description="Multilingual title",
    )
    description: dict[str, str] | None = Field(
        None,
        description="Multilingual description",
    )
    is_published: bool | None = Field(
        None,
        description="Whether the rallye is published",
    )


class RallyeResponse(BaseModel):
    """Response model for rallye data."""

    id: UUID = Field(..., description="Rallye UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    title: dict[str, str] = Field(..., description="Multilingual title")
    description: dict[str, str] = Field(..., description="Multilingual description")
    is_published: bool = Field(..., description="Whether the rallye is published")
    is_deleted: bool = Field(False, description="Archived (soft-deleted). An org_admin sees it with include_deleted")
    deleted_at: datetime | None = Field(None, description="Archive timestamp (nullable)")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    created_by_user_id: UUID | None = Field(
        None,
        description="ID of the user who created this rallye (nullable for legacy rows)",
    )
    created_by_display_name: str | None = Field(
        None,
        description="Display name of the user who created this rallye (joined)",
    )

    @model_validator(mode="before")
    @classmethod
    def _populate_created_by(cls, data: Any) -> Any:
        return lift_created_by_display_name(data)

    class Config:
        from_attributes = True


class RallyeListResponse(BaseModel):
    """Response model for paginated rallye list."""

    items: list[RallyeResponse] = Field(..., description="List of rallyes")
    total: int = Field(..., description="Total count of rallyes")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


# ============================================================================
# Station Schemas
# ============================================================================


# Bounds of a station's tips. The frontend mirrors them in utils/stationTips.ts.
# The heading is rich text, so its bound counts the markup too. A bound of 120
# refused a heading of two short sentences.
TIPS_HEADER_MAX_LENGTH = 2000
TIPS_LABEL_MAX_LENGTH = 200


class StationTipLink(BaseModel):
    """One external link inside the tips popup of a station."""

    label: dict[str, str] = Field(
        ...,
        min_length=1,
        description="Customizable multilingual link label shown instead of the raw URL",
        examples=[{"de": "Integreat-Artikel", "en": "Integreat article"}],
    )
    url: str = Field(
        ...,
        max_length=1000,
        description="External link target; http(s) only",
        examples=["https://integreat.app/augsburg/de"],
    )

    @field_validator("label")
    @classmethod
    def _label_values_bounded(cls, v: dict[str, str]) -> dict[str, str]:
        for text in v.values():
            if not text.strip():
                raise ValueError("Link label must not be empty")
            if len(text) > TIPS_LABEL_MAX_LENGTH:
                raise ValueError(f"Link label must be at most {TIPS_LABEL_MAX_LENGTH} characters")
        return v

    @field_validator("url")
    @classmethod
    def _url_http_only(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("Link URL must start with http:// or https://")
        return v


class StationTips(BaseModel):
    """The tips popup of a station: a customizable header and labeled links."""

    header: dict[str, str] = Field(
        ...,
        min_length=1,
        description="Customizable multilingual popup header",
        examples=[{"de": "Mehr über den Dom", "en": "More about the cathedral"}],
    )
    links: list[StationTipLink] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="External links shown in the popup (1-10)",
    )

    @field_validator("header")
    @classmethod
    def _header_values_bounded(cls, v: dict[str, str]) -> dict[str, str]:
        for text in v.values():
            if not text.strip():
                raise ValueError("Popup header must not be empty")
            if len(text) > TIPS_HEADER_MAX_LENGTH:
                raise ValueError(f"Popup header must be at most {TIPS_HEADER_MAX_LENGTH} characters")
        return v


class StationCreateRequest(BaseModel):
    """Request body for creating a new station."""

    title: dict[str, str] = Field(
        ...,
        description="Multilingual title",
        examples=[{"de": "Rathaus", "en": "City Hall"}],
    )
    description: dict[str, str] = Field(
        ...,
        description="Multilingual description",
        examples=[{"de": "Das historische Rathaus von Augsburg", "en": "The historic city hall of Augsburg"}],
    )
    address: str | None = Field(
        None,
        description="Optional human-readable address",
        max_length=500,
        examples=["Rathausplatz 1, 86150 Augsburg"],
    )
    location_lat: float | None = Field(
        None,
        description="Latitude coordinate",
        ge=-90.0,
        le=90.0,
        examples=[48.3698],
    )
    location_lng: float | None = Field(
        None,
        description="Longitude coordinate",
        ge=-180.0,
        le=180.0,
        examples=[10.8985],
    )
    tips: StationTips | None = Field(
        None,
        description="Optional tips popup with links. Null means no popup",
    )

    @model_validator(mode="after")
    def _address_needs_coordinates(self) -> "StationCreateRequest":
        """An address without a map position would put a station on the wrong house."""
        if (self.address or "").strip() and (self.location_lat is None or self.location_lng is None):
            raise ValueError("A station with an address needs location_lat and location_lng")
        return self


class StationUpdateRequest(BaseModel):
    """Request body for updating a station (propagates to all rallyes using it)."""

    title: dict[str, str] | None = Field(
        None,
        description="Multilingual title",
    )
    description: dict[str, str] | None = Field(
        None,
        description="Multilingual description",
    )
    address: str | None = Field(
        None,
        description="Optional human-readable address",
        max_length=500,
    )
    location_lat: float | None = Field(
        None,
        description="Latitude coordinate",
        ge=-90.0,
        le=90.0,
    )
    location_lng: float | None = Field(
        None,
        description="Longitude coordinate",
        ge=-180.0,
        le=180.0,
    )
    tips: StationTips | None = Field(
        None,
        description="Tips popup with links. An explicit null clears the popup. A missing field leaves it unchanged.",
    )


SortOrder = Literal["asc", "desc"]
StationSortKey = Literal["title", "created_at", "usage", "author"]
ChallengeSortKey = Literal["title", "created_at", "usage", "author", "points", "type", "duration"]


class StationUsage(BaseModel):
    """Where a library station is used (the rallyes that link it)."""

    rallye_ids: list[UUID] = Field(default_factory=list, description="Rallyes that link this station")
    rallye_titles: list[dict[str, str]] = Field(
        default_factory=list, description="Titles of rallyes that link this station (same order as rallye_ids)"
    )


class StationResponse(BaseModel):
    """Response model for a library station."""

    id: UUID = Field(..., description="Station UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    title: dict[str, str] = Field(..., description="Multilingual title")
    description: dict[str, str] = Field(..., description="Multilingual description")
    address: str | None = Field(None, description="Optional human-readable address")
    location_lat: float | None = Field(None, description="Latitude coordinate")
    location_lng: float | None = Field(None, description="Longitude coordinate")
    title_image_url: str | None = Field(
        None,
        description="Presigned URL of the station's title image, if set (nullable). Derived from title_image_key.",
    )
    tips: StationTips | None = Field(None, description="Tips popup with links (nullable)")
    is_deleted: bool = Field(False, description="Archived (soft-deleted). An org_admin sees it with include_deleted")
    deleted_at: datetime | None = Field(None, description="Archive timestamp (nullable)")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    created_by_user_id: UUID | None = Field(
        None,
        description="ID of the user who created this station (nullable for legacy rows)",
    )
    created_by_display_name: str | None = Field(
        None,
        description="Display name of the user who created this station (joined)",
    )
    usage: StationUsage | None = Field(
        None,
        description="Usage + aggregates (populated by library list/detail endpoints)",
    )
    usage_count: int = Field(0, ge=0, description="Number of distinct, non-archived rallyes that use this station")

    @model_validator(mode="before")
    @classmethod
    def _populate_created_by(cls, data: Any) -> Any:
        return lift_created_by_display_name(data)

    class Config:
        from_attributes = True


class StationListResponse(BaseModel):
    """Response model for paginated station list."""

    items: list[StationResponse] = Field(..., description="List of stations")
    total: int = Field(..., description="Total count of stations")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


class RallyeStationResponse(BaseModel):
    """A station as composed into a rallye (carries the per-rallye sort_order)."""

    link_id: UUID = Field(..., description="rallye_stations link UUID")
    rallye_id: UUID = Field(..., description="Rallye UUID")
    sort_order: int = Field(..., description="Position of this station within the rallye")
    station: StationResponse = Field(..., description="The linked library station")
    challenge_count: int = Field(0, description="Number of challenges matched to this station in this rallye")
    total_points: int = Field(0, description="Sum of max_points across matched challenges (best-effort)")
    total_duration_minutes: int = Field(0, description="Sum of time_estimate_minutes across matched challenges")


class RallyeStationListResponse(BaseModel):
    """Ordered stations composed into a rallye."""

    items: list[RallyeStationResponse] = Field(..., description="Stations in rallye order")
    total: int = Field(..., description="Total stations linked to the rallye")


class ImportStationRequest(BaseModel):
    """Link an existing library station into a rallye."""

    station_id: UUID = Field(..., description="Library station to link")
    sort_order: int | None = Field(None, description="Optional explicit position; appended to the end if omitted", ge=0)


class ReorderRequest(BaseModel):
    """Reorder linked children by supplying their IDs in the desired order."""

    ids: list[UUID] = Field(..., description="Child IDs (station or challenge) in the desired order")


# ============================================================================
# Challenge Schemas
# ============================================================================


# Types a creator can no longer choose. The value stays in the enum, because frozen
# run snapshots and their answers may carry it. Migration 040 turned the library's
# creative challenges into photo challenges, whose answers take a text as a fallback.
RETIRED_CHALLENGE_TYPES = frozenset({ChallengeType.CREATIVE})


def _refuse_retired_type(value: ChallengeType | None) -> ChallengeType | None:
    if value in RETIRED_CHALLENGE_TYPES:
        raise ValueError(
            "The type 'creative' is no longer offered. Use photo, video or audio: each takes a text answer as well"
        )
    return value


class ChallengeCreateRequest(BaseModel):
    """Request body for creating a new challenge."""

    challenge_type: ChallengeType = Field(
        ...,
        description="Type of challenge (photo, video, audio, text, qr_scan, quiz). 'creative' is refused",
        examples=[ChallengeType.PHOTO],
    )
    title: dict[str, str] = Field(
        ...,
        description="Multilingual title",
        examples=[{"de": "Fotografiere das Rathaus", "en": "Take a photo of the city hall"}],
    )
    instructions: dict[str, str] = Field(
        ...,
        description="Multilingual instructions",
        examples=[{"de": "Mache ein Foto vom Haupteingang", "en": "Take a photo of the main entrance"}],
    )
    scoring_rules: dict[str, Any] = Field(
        ...,
        description="Flexible scoring configuration (type-specific)",
        examples=[{"max_points": 100, "auto_approve": True, "time_bonus": 10}],
    )
    time_estimate_minutes: int | None = Field(
        None,
        description="Estimated time to complete the challenge, in minutes (optional)",
        ge=0,
        le=600,
        examples=[5],
    )
    suggested_solution: str | None = Field(
        None,
        description=(
            "Reviewer-facing reference answer (not shown to participants). Rich text: "
            "the editor's HTML, sanitised on write. The bound counts the markup too"
        ),
        examples=["<p>The <strong>Goldener Saal</strong> in the Augsburger Rathaus.</p>"],
        max_length=10000,
    )

    _no_retired_type = field_validator("challenge_type")(_refuse_retired_type)


class ChallengeUpdateRequest(BaseModel):
    """Request body for updating a challenge."""

    challenge_type: ChallengeType | None = Field(
        None,
        description="Type of challenge (photo, video, audio, text, qr_scan, quiz). 'creative' is refused",
    )
    title: dict[str, str] | None = Field(
        None,
        description="Multilingual title",
    )
    instructions: dict[str, str] | None = Field(
        None,
        description="Multilingual instructions",
    )
    scoring_rules: dict[str, Any] | None = Field(
        None,
        description="Flexible scoring configuration (type-specific)",
    )
    time_estimate_minutes: int | None = Field(
        None,
        description="Estimated time to complete the challenge, in minutes (optional)",
        ge=0,
        le=600,
    )
    suggested_solution: str | None = Field(
        None,
        description=(
            "Reviewer-facing reference answer (not shown to participants). Rich text, "
            "sanitised on write. null clears it, a missing field keeps it"
        ),
        max_length=10000,
    )

    _no_retired_type = field_validator("challenge_type")(_refuse_retired_type)


class ChallengeResponse(BaseModel):
    """Response model for challenge data."""

    id: UUID = Field(..., description="Challenge UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    challenge_type: ChallengeType = Field(..., description="Type of challenge")
    title: dict[str, str] = Field(..., description="Multilingual title")
    instructions: dict[str, str] = Field(..., description="Multilingual instructions")
    scoring_rules: dict[str, Any] = Field(..., description="Flexible scoring configuration")
    time_estimate_minutes: int | None = Field(
        None,
        description="Estimated time to complete the challenge, in minutes",
    )
    suggested_solution: str | None = Field(
        None,
        description="Reviewer-facing reference answer",
    )
    is_deleted: bool = Field(False, description="Archived (soft-deleted). An org_admin sees it with include_deleted")
    deleted_at: datetime | None = Field(None, description="Archive timestamp (nullable)")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    created_by_user_id: UUID | None = Field(
        None,
        description="ID of the user who created this challenge (nullable for legacy rows)",
    )
    created_by_display_name: str | None = Field(
        None,
        description="Display name of the user who created this challenge (joined)",
    )
    usage: "ChallengeUsage | None" = Field(
        None,
        description="Usage info (populated by library list/detail endpoints)",
    )
    usage_count: int = Field(0, ge=0, description="Number of distinct, non-archived rallyes that use this challenge")

    @model_validator(mode="before")
    @classmethod
    def _populate_created_by(cls, data: Any) -> Any:
        return lift_created_by_display_name(data)

    class Config:
        from_attributes = True


class ChallengeUsage(BaseModel):
    """Where a library challenge is used (rallyes that match it on one of their stations)."""

    station_ids: list[UUID] = Field(
        default_factory=list, description="Stations that match this challenge (via rallye-stations)"
    )
    station_titles: list[dict[str, str]] = Field(
        default_factory=list, description="Titles of those stations (same order as station_ids)"
    )
    rallye_ids: list[UUID] = Field(
        default_factory=list, description="Rallyes that use this challenge (via their rallye-stations)"
    )
    rallye_titles: list[dict[str, str]] = Field(
        default_factory=list, description="Titles of those rallyes (same order as rallye_ids)"
    )


# ChallengeResponse references ChallengeUsage as a forward ref; resolve it now.
ChallengeResponse.model_rebuild()


class ChallengeListResponse(BaseModel):
    """Response model for paginated challenge list."""

    items: list[ChallengeResponse] = Field(..., description="List of challenges")
    total: int = Field(..., description="Total count of challenges")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


class RallyeStationChallengeResponse(BaseModel):
    """A challenge as matched to a rallye's station (carries the per-rallye-station sort_order)."""

    link_id: UUID = Field(..., description="rallye_station_challenges link UUID")
    rallye_station_id: UUID = Field(..., description="RallyeStation link UUID")
    sort_order: int = Field(..., description="Position of this challenge within the rallye-station")
    challenge: ChallengeResponse = Field(..., description="The linked library challenge")


class RallyeStationChallengeListResponse(BaseModel):
    """Ordered challenges matched to a rallye's station."""

    items: list[RallyeStationChallengeResponse] = Field(..., description="Challenges in rallye-station order")
    total: int = Field(..., description="Total challenges linked to the rallye-station")


class ImportChallengeRequest(BaseModel):
    """Link an existing library challenge into a rallye's station."""

    challenge_id: UUID = Field(..., description="Library challenge to link")
    sort_order: int | None = Field(None, description="Optional explicit position; appended to the end if omitted", ge=0)


# ============================================================================
# Run Schemas
# ============================================================================


class RunCreateRequest(BaseModel):
    """Request body for creating a new run."""

    rallye_id: UUID = Field(
        ...,
        description="UUID of the template rallye to create run from",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Display name for this run instance",
        examples=["Summer Rally 2026", "Integration Workshop Run"],
    )
    scheduled_for: datetime | None = Field(
        None,
        description="When the run is scheduled to start (optional)",
        examples=["2026-07-15T10:00:00Z"],
    )
    settings: dict[str, Any] | None = Field(
        None,
        description="Flexible run-specific configuration",
        examples=[{"allow_late_joins": True, "enable_hints": False, "time_limit_minutes": 120}],
    )
    max_teams: int | None = Field(
        None,
        description="Maximum number of teams allowed (null = unlimited)",
        ge=1,
        examples=[10, 20],
    )
    content_languages: list[str] | None = Field(
        None,
        description="ISO codes of languages this run is configured to display (default ['de','en'])",
        examples=[["de", "en", "ar"]],
    )


class RunUpdateRequest(BaseModel):
    """Request body for updating a run."""

    name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        description="Display name for this run instance",
    )
    scheduled_for: datetime | None = Field(
        None,
        description="When the run is scheduled to start",
    )
    settings: dict[str, Any] | None = Field(
        None,
        description="Flexible run-specific configuration",
    )
    max_teams: int | None = Field(
        None,
        description="Maximum number of teams allowed (null = unlimited)",
        ge=1,
    )
    content_languages: list[str] | None = Field(
        None,
        description="ISO codes of languages this run is configured to display",
    )


class RunScheduleRequest(BaseModel):
    """Request body for scheduling a run."""

    scheduled_for: datetime = Field(
        ...,
        description="When the run should start",
        examples=["2026-07-15T10:00:00Z"],
    )


class RunResponse(BaseModel):
    """Response model for run data."""

    id: UUID = Field(..., description="Run UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    rallye_id: UUID | None = Field(..., description="Template rallye UUID (nullable if template deleted)")
    name: str = Field(..., description="Display name for this run instance")
    state: RunState = Field(..., description="Current state of the run")
    join_code: str = Field(..., description="Unique 6-character code for teams to join")
    scheduled_for: datetime | None = Field(..., description="When the run is scheduled to start")
    started_at: datetime | None = Field(..., description="When the run actually started")
    paused_at: datetime | None = Field(..., description="When the run was last paused")
    ended_at: datetime | None = Field(..., description="When the run ended")
    settings: dict[str, Any] = Field(..., description="Flexible run-specific configuration")
    max_teams: int | None = Field(..., description="Maximum number of teams allowed (null = unlimited)")
    content_languages: list[str] = Field(
        default_factory=lambda: ["de", "en"],
        description="ISO codes of languages this run is configured to display",
    )
    grading_complete: bool = False
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class RunListResponse(BaseModel):
    """Response model for paginated run list."""

    items: list[RunResponse] = Field(..., description="List of runs")
    total: int = Field(..., description="Total count of runs")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


class TeamProgressEntry(BaseModel):
    """Per-team aggregate progress for a run, for the run dashboard."""

    team_id: UUID = Field(..., description="Team UUID")
    team_name: str | None = Field(..., description="Team display name (null while the team has no name yet)")
    member_count: int = Field(..., description="Number of users on this team")
    stations_completed: int = Field(
        ..., description="Number of stations where all challenges have a submission from this team"
    )
    stations_total: int = Field(..., description="Total number of stations in the run")
    challenges_submitted: int = Field(..., description="Distinct challenges this team has submitted at least once")
    challenges_total: int = Field(..., description="Total number of challenges across all stations")
    challenges_scored: int = Field(..., description="Submissions that have a Score row (reviewer or auto-scored)")
    total_points: int = Field(..., description="Sum of points across all scored submissions for this team")
    total_max_points: int = Field(
        ..., description="Sum of max_points across all scored submissions (for percentage calc)"
    )
    requested_help: bool = Field(
        False,
        description="Whether the team currently has a pending coordinator-help request",
    )


class TeamProgressResponse(BaseModel):
    """List of per-team progress entries for a run."""

    run_id: UUID = Field(..., description="Run UUID")
    teams: list[TeamProgressEntry] = Field(..., description="Per-team aggregate progress")


class RunHistoryEntry(BaseModel):
    """Per-run summary in the participant Run History page."""

    run_id: UUID = Field(..., description="Run UUID")
    run_name: str = Field(..., description="Run display name")
    rallye_title: dict[str, str] = Field(
        default_factory=dict,
        description="Multilingual rallye title (snapshot from when the user joined)",
    )
    team_id: UUID = Field(..., description="Team UUID the user was a member of")
    team_name: str | None = Field(..., description="Team display name (null while the team has no name yet)")
    started_at: datetime | None = Field(None, description="When the run started")
    ended_at: datetime | None = Field(None, description="When the run ended (NULL if still running)")
    state: RunState = Field(..., description="Current state of the run")
    placement: int | None = Field(
        None,
        description="1-indexed rank of the team on the final scoreboard (NULL if no scores yet)",
    )
    total_teams: int | None = Field(
        None,
        description="Total number of teams in the run (NULL if no scores yet)",
    )
    total_points: int = Field(0, description="Sum of points scored by the team")


class RunHistoryResponse(BaseModel):
    """Participant Run History — list of past + current run participations."""

    items: list[RunHistoryEntry] = Field(..., description="Run participations, newest-first")


# ============================================================================
# RunStation Schemas
# ============================================================================


class RunStationResponse(BaseModel):
    """Response model for run station data (frozen snapshot)."""

    id: UUID = Field(..., description="RunStation UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    run_id: UUID = Field(..., description="Run UUID")
    source_station_id: UUID | None = Field(
        ..., description="Original template station UUID (nullable if template deleted)"
    )
    title: dict[str, str] = Field(..., description="Multilingual title (frozen from source)")
    description: dict[str, str] = Field(..., description="Multilingual description (frozen from source)")
    location_lat: float | None = Field(..., description="Latitude coordinate (frozen from source)")
    location_lng: float | None = Field(..., description="Longitude coordinate (frozen from source)")
    address: str | None = Field(None, description="Human-readable address (frozen from source)")
    title_image_url: str | None = Field(
        None,
        description="Presigned URL of the station's title image, if set (frozen from source; nullable).",
    )
    tips: StationTips | None = Field(None, description="Tips popup with links (frozen from source, nullable)")
    sort_order: int = Field(..., description="Sort order within run (frozen from source)")
    created_at: datetime = Field(..., description="Creation timestamp (when snapshot created)")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class RunStationListResponse(BaseModel):
    """Response model for paginated run station list."""

    items: list[RunStationResponse] = Field(..., description="List of run stations")
    total: int = Field(..., description="Total count of run stations")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


# ============================================================================
# RunChallenge Schemas
# ============================================================================


class RunChallengeResponse(BaseModel):
    """Response model for run challenge data (frozen snapshot)."""

    id: UUID = Field(..., description="RunChallenge UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    run_id: UUID = Field(..., description="Run UUID")
    run_station_id: UUID = Field(..., description="RunStation UUID")
    source_challenge_id: UUID | None = Field(
        ..., description="Original template challenge UUID (nullable if template deleted)"
    )
    challenge_type: ChallengeType = Field(..., description="Type of challenge (frozen from source)")
    title: dict[str, str] = Field(..., description="Multilingual title (frozen from source)")
    instructions: dict[str, str] = Field(..., description="Multilingual instructions (frozen from source)")
    scoring_rules: dict[str, Any] = Field(..., description="Flexible scoring configuration (frozen from source)")
    sort_order: int = Field(..., description="Sort order within run station (frozen from source)")
    time_estimate_minutes: int | None = Field(
        None,
        description="Snapshotted estimated minutes to complete this challenge",
    )
    suggested_solution: str | None = Field(
        None,
        description="Snapshotted reviewer-facing reference answer. Coordination only: null for guests and participants",
    )
    created_at: datetime = Field(..., description="Creation timestamp (when snapshot created)")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class RunChallengeListResponse(BaseModel):
    """Response model for paginated run challenge list."""

    items: list[RunChallengeResponse] = Field(..., description="List of run challenges")
    total: int = Field(..., description="Total count of run challenges")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


class RunStationOverviewItem(RunStationResponse):
    """A run station with its challenges, for the participant station list."""

    challenges: list[RunChallengeResponse] = Field(
        default_factory=list, description="The station's challenges, participant-safe below coordinator"
    )


class RunParticipantOverviewResponse(BaseModel):
    """Stations, their challenges and the caller's answered challenges in one answer.

    The participant station list refreshes every 15 s. This one request replaces
    the request for the stations, one request per station and the request for
    the submissions list.
    """

    stations: list[RunStationOverviewItem] = Field(..., description="Stations in route order")
    answered_challenge_ids: list[UUID] = Field(
        ..., description="Run challenges the caller's own team has answered (empty without a team)"
    )
    is_team_member: bool = Field(
        ...,
        description=(
            "Whether the caller is in a team of this run. False for a participant a coordinator "
            "removed, so the app can say so and leave the run"
        ),
    )


# ============================================================================
# Run Result Link Schemas
# ============================================================================


class ResultLinkPublishResponse(BaseModel):
    """Response after publishing (or re-publishing) a run's result link."""

    url: str = Field(..., description="Full public URL of the published result")
    slug: str = Field(..., description="The public slug (not persisted server-side)")
    published_at: datetime = Field(..., description="When this link was (re-)published")
    expires_at: datetime = Field(..., description="When this link stops resolving")


class ResultLinkStatusResponse(BaseModel):
    """Coordinator-facing status of a run's result link."""

    status: str = Field(..., description="One of: unpublished, published, revoked")
    url: str | None = Field(None, description="Public URL, present when status is 'published'")
    published_at: datetime | None = Field(None, description="Present when status is 'published'")
    expires_at: datetime | None = Field(None, description="Present when status is 'published'")


# ============================================================================
# Team Schemas
# ============================================================================


def _trim_team_name(value: Any) -> Any:
    """Strip surrounding whitespace before the length bounds apply.

    Team names are unique per run, compared on the trimmed and
    lowercased string. Trimming at the validation boundary keeps what is stored
    identical to what the uniqueness check compares, so " Fuechse " can never
    slip past the check as a second "Fuechse".
    """
    return value.strip() if isinstance(value, str) else value


class TeamCreateRequest(BaseModel):
    """Request body for creating a new team."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Display name for the team. Whitespace is trimmed before validation.",
        examples=["The Explorers", "Team Alpha"],
    )
    color: str | None = Field(
        None,
        min_length=7,
        max_length=7,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Hex color code for visual identification (e.g., '#FF5733')",
        examples=["#FF5733", "#3498DB"],
    )

    @field_validator("name", mode="before")
    @classmethod
    def _trim_name(cls, v: Any) -> Any:
        return _trim_team_name(v)


class TeamResponse(BaseModel):
    """Response model for team data."""

    id: UUID = Field(..., description="Team UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    run_id: UUID = Field(..., description="Run UUID")
    name: str | None = Field(
        ...,
        description="Display name for the team. Null while the creator has not set a name yet. The UI shows an 'Unnamed Team' fallback then.",
    )
    color: str | None = Field(..., description="Hex color code for visual identification")
    member_count: int = Field(..., description="Number of members in the team (computed)")
    members: list["TeamMemberResponse"] | None = Field(
        default=None,
        description=(
            "Optional member list, oldest member first. The single-team detail "
            "endpoint (GET /teams/{id}) fills it for members of the team and "
            "for callers with the role coordinator or above, so the wait screens "
            "can list the members. The listing (GET /teams/) fills it for those "
            "callers only, so the run page can name the members in each team row. "
            "Every other caller gets None."
        ),
    )
    share_code: str | None = Field(
        default=None,
        description=(
            "Team share-code. Filled only when the requesting user is a member "
            "of this team or has the role coordinator or above. None otherwise."
        ),
    )
    coordinator_chat_open: bool = Field(
        default=False,
        description=(
            "Whether the team's coordinator assist window is open: true after "
            "the team requested coordinator assistance, false again once a "
            "coordinator leaves the chat. Gates coordinator chat access."
        ),
    )
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class TeamListResponse(BaseModel):
    """Response model for paginated team list."""

    items: list[TeamResponse] = Field(..., description="List of teams")
    total: int = Field(..., description="Total count of teams")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


class TeamStageAdvanceRequest(BaseModel):
    """Request body for advancing the creator's team-flow stage.

    The creator's wizard sends it when it moves from the Invite step to the
    PickTeamName step. Members who already joined then go from the 'waiting'
    state to the 'pending' state (waiting for the name). Only one stage exists
    today."""

    stage: Literal["naming"] = Field(
        ...,
        description="The new stage the creator has reached. 'naming' = creator left Invite and is now on PickTeamName.",
    )


class TeamRenameRequest(BaseModel):
    """Request body for setting or changing a team's display name.

    The creator sets the name on the PickTeamName step. A coordinator or
    org admin can correct it. Sending the current name again changes nothing:
    the answer is 200 and no WebSocket event goes out."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="New team display name. Whitespace is trimmed before validation.",
        examples=["Speedy Foxes"],
    )

    @field_validator("name", mode="before")
    @classmethod
    def _trim_name(cls, v: Any) -> Any:
        return _trim_team_name(v)


class TeamMemberResponse(BaseModel):
    """Response model for team member data."""

    id: UUID = Field(..., description="TeamMember UUID")
    display_name: str = Field(..., description="Display name for the team member")
    is_guest: bool = Field(..., description="Whether this is a guest participant")
    joined_at: datetime = Field(..., description="Timestamp when the member joined the team")

    class Config:
        from_attributes = True


class TeamMemberMoveRequest(BaseModel):
    """Request body for moving a member to another team of the same run."""

    target_team_id: UUID = Field(
        ...,
        description="UUID of the team the member is moved to. Must be another team of the same run.",
    )
    confirm_dissolution: bool = Field(
        False,
        description=(
            "True when the client confirmed that this move dissolves the source team. Without it a move "
            "of the last member answers 409 TEAM_WOULD_DISSOLVE. Ignored when other members stay."
        ),
    )


class TeamLeaveRequest(BaseModel):
    """Optional request body for leaving a team."""

    confirm_dissolution: bool = Field(
        False,
        description=(
            "True when the client confirmed that leaving dissolves the team. Without it the last member "
            "to leave gets 409 TEAM_WOULD_DISSOLVE. Ignored when other members stay."
        ),
    )


class TeamConflictResponse(ErrorResponse):
    """The 409 answer of an act that can dissolve a team.

    ``TEAM_BUSY``: other requests are changing the team, try again.
    ``TEAM_WOULD_DISSOLVE``: the act would dissolve the team and the client did
    not send ``confirm_dissolution``. Nothing was changed. The four count and
    flag fields are present only with this code. They hold the state at the
    moment of the refusal, for the confirmation the client shows before it
    sends the request again.
    """

    member_count: int | None = Field(
        None, description="TEAM_WOULD_DISSOLVE only: members the team has now, the one to take out included."
    )
    answer_count: int | None = Field(
        None, description="TEAM_WOULD_DISSOLVE only: answers of the team that would leave the ranking."
    )
    result_published: bool | None = Field(
        None, description="TEAM_WOULD_DISSOLVE only: whether the run has a live public result link."
    )
    run_ended: bool | None = Field(
        None, description="TEAM_WOULD_DISSOLVE only: whether the run has ended, so its final ranking would change."
    )


# ============================================================================
# Submission Schemas
# ============================================================================


class SubmissionCreateRequest(BaseModel):
    """Request body for creating a new submission."""

    run_challenge_id: UUID = Field(
        ...,
        description="UUID of the run challenge being answered",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    content: dict[str, Any] = Field(
        ...,
        description="Flexible submission data (type-specific: text, photo_url, code, selected_option, etc.)",
        examples=[
            {"text": "Das Rathaus wurde 1620 erbaut"},
            {"photo_url": "https://storage.example.com/photos/abc123.jpg"},
            {"code": "ABC123"},
            {"selected_option": 2},
            {"text": "Creative answer", "media_urls": ["https://example.com/video.mp4"]},
        ],
    )


class SubmissionUpdateRequest(BaseModel):
    """Request body for replacing an unscored submission's content."""

    content: dict[str, Any] = Field(
        ...,
        description="Flexible submission data (type-specific: text, photo_url, code, selected_option, etc.)",
        examples=[
            {"text": "Das Rathaus wurde 1620 erbaut"},
            {"selected_option": "A"},
        ],
    )


class SubmissionResponse(BaseModel):
    """Response model for submission data."""

    id: UUID = Field(..., description="Submission UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    run_id: UUID = Field(..., description="Run UUID")
    team_id: UUID = Field(..., description="Team UUID that made the submission")
    run_challenge_id: UUID = Field(..., description="RunChallenge UUID being answered")
    content: dict[str, Any] = Field(..., description="Flexible submission data (type-specific)")
    submitted_at: datetime = Field(..., description="Timestamp when the submission was created (server-set)")
    is_scored: bool = Field(False, description="Whether this submission already has a score")
    # Carried on the submission so a team can see its result: participants may
    # not read /scores/ (coordinator+ only), and this is the response they do get.
    points: int | None = Field(None, description="Points awarded, once scored")
    max_points: int | None = Field(None, description="Points obtainable, once scored")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class SubmissionListResponse(BaseModel):
    """Response model for paginated submission list."""

    items: list[SubmissionResponse] = Field(..., description="List of submissions")
    total: int = Field(..., description="Total count of submissions")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


# ============================================================================
# Score Schemas
# ============================================================================


class ScoreCreateRequest(BaseModel):
    """Request body for creating a new score."""

    submission_id: UUID = Field(
        ...,
        description="UUID of the submission being scored",
        examples=["123e4567-e89b-12d3-a456-426614174000"],
    )
    points: int = Field(
        ...,
        description="Points awarded for this submission (must be >= 0)",
        ge=0,
        examples=[8, 10, 0],
    )
    comment: str | None = Field(
        None,
        min_length=1,
        max_length=1000,
        description="Optional feedback comment from the coordinator",
        examples=["Gute Antwort, aber ein Detail fehlt.", "Perfekt!"],
    )


class ScoreUpdateRequest(BaseModel):
    """Request body for updating a score."""

    points: int | None = Field(
        None,
        description="Points awarded for this submission (must be >= 0)",
        ge=0,
    )
    comment: str | None = Field(
        None,
        min_length=1,
        max_length=1000,
        description="Optional feedback comment from the coordinator",
    )


class ScoreResponse(BaseModel):
    """Response model for score data."""

    id: UUID = Field(..., description="Score UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    submission_id: UUID = Field(..., description="UUID of the submission being scored")
    points: int = Field(..., description="Points awarded for this submission")
    max_points: int = Field(..., description="Maximum points possible for this challenge")
    comment: str | None = Field(..., description="Optional feedback comment from coordinator")
    scored_by: UUID | None = Field(..., description="UUID of the user who created the score (nullable)")
    scored_at: datetime = Field(..., description="Timestamp when the score was created")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class ScoreListResponse(BaseModel):
    """Response model for paginated score list."""

    items: list[ScoreResponse] = Field(..., description="List of scores")
    total: int = Field(..., description="Total count of scores")


class ScoreboardEntry(BaseModel):
    """Single entry in a run scoreboard."""

    team_id: UUID = Field(..., description="UUID of the team")
    team_name: str | None = Field(..., description="Display name of the team (null while the team has no name yet)")
    total_points: int = Field(..., description="Total points earned by the team")
    total_max_points: int = Field(..., description="Total maximum points possible across all scored submissions")
    scored_count: int = Field(..., description="Number of submissions that have been scored")
    last_scored_at: datetime | None = Field(None, description="Timestamp of the team's most recent score, if any")
    rank: int = Field(..., description="Team's rank in the scoreboard (1 = first place)")
    is_winner: bool


class ScoreboardResponse(BaseModel):
    """Response model for a run's scoreboard."""

    run_id: UUID = Field(..., description="UUID of the run")
    entries: list[ScoreboardEntry] = Field(..., description="List of scoreboard entries, ordered by rank")
    is_final: bool
    scored_count: int
    submission_count: int


# ============================================================================
# ChatMessage Schemas
# ============================================================================


class ChatMessageCreateRequest(BaseModel):
    """Request body for creating a new chat message."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Text content of the message",
        examples=["We've arrived at Station 2!", "Great job team!"],
    )


class ChatMessageResponse(BaseModel):
    """Response model for chat message data."""

    id: UUID = Field(..., description="ChatMessage UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    team_id: UUID = Field(..., description="Team UUID this message belongs to")
    author_id: UUID | None = Field(
        ..., description="UUID of the user who sent the message (nullable for system messages)"
    )
    content: str = Field(..., description="Text content of the message")
    is_system_message: bool = Field(..., description="Whether this is a system-generated message")
    author_role: str | None = Field(
        None,
        description="Role of the author at the time the message was sent (used to colour coordinator bubbles)",
    )
    author_display_name: str | None = Field(
        None,
        description=(
            "Display name of the author, resolved at read time from the User row. "
            "None for system messages, or when the author no longer exists (e.g. deleted user)."
        ),
    )
    created_at: datetime = Field(..., description="Timestamp when the message was created (server-set, immutable)")

    class Config:
        from_attributes = True


class ChatMessageListResponse(BaseModel):
    """Response model for paginated chat message list."""

    items: list[ChatMessageResponse] = Field(..., description="List of chat messages")
    total: int = Field(..., description="Total count of chat messages")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


# ============================================================================
# FeedEvent Schemas
# ============================================================================


class FeedEventCreateRequest(BaseModel):
    """Request body for creating a new feed event."""

    event_type: FeedEventType = Field(
        ...,
        description="Type of event (system, announcement, milestone)",
        examples=[FeedEventType.ANNOUNCEMENT],
    )
    content: dict[str, Any] = Field(
        ...,
        description="JSONB content (multilingual or structured data)",
        examples=[
            {"de": "Pause bis 15:00 Uhr", "en": "Break until 3:00 PM"},
            {"de": "Team Alpha hat Station 1 erreicht", "en": "Team Alpha reached Station 1"},
        ],
    )
    target_team_id: UUID | None = Field(
        None,
        description="If set, this event is only delivered to this team; NULL broadcasts to the whole run.",
    )


class FeedEventResponse(BaseModel):
    """Response model for feed event data."""

    id: UUID = Field(..., description="FeedEvent UUID")
    organization_id: UUID = Field(..., description="Organization UUID")
    run_id: UUID = Field(..., description="Run UUID this event belongs to")
    event_type: FeedEventType = Field(..., description="Type of event (system, announcement, milestone)")
    content: dict[str, Any] = Field(..., description="JSONB content (multilingual or structured data)")
    author_id: UUID | None = Field(
        ..., description="UUID of the user who created the event (nullable for system events)"
    )
    target_team_id: UUID | None = Field(
        None,
        description="Team this event targets (NULL = broadcast)",
    )
    created_at: datetime = Field(..., description="Timestamp when the event was created (server-set, immutable)")

    class Config:
        from_attributes = True


class FeedEventListResponse(BaseModel):
    """Response model for paginated feed event list."""

    items: list[FeedEventResponse] = Field(..., description="List of feed events")
    total: int = Field(..., description="Total count of feed events")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


# ============================================================================
# Feedback Schemas
# ============================================================================


class FeedbackCreateRequest(BaseModel):
    """Request body for creating anonymous feedback."""

    rating: int = Field(
        ...,
        ge=1,
        le=5,
        description="Rating from 1 to 5",
        examples=[5, 4, 3],
    )
    comment: str | None = Field(
        None,
        min_length=1,
        max_length=2000,
        description="Optional text comment with additional feedback",
        examples=["Great experience! Would do it again.", "Some challenges were too difficult."],
    )


class FeedbackResponse(BaseModel):
    """Response model for feedback data (no user info for anonymity)."""

    id: UUID = Field(..., description="Feedback UUID")
    run_id: UUID = Field(..., description="UUID of the run this feedback is about")
    rating: int = Field(..., description="Rating from 1 to 5")
    comment: str | None = Field(..., description="Optional text comment with additional feedback")
    created_at: datetime = Field(..., description="Timestamp when the feedback was submitted")

    class Config:
        from_attributes = True


class FeedbackListResponse(BaseModel):
    """Response model for paginated feedback list."""

    items: list[FeedbackResponse] = Field(..., description="List of feedback entries")
    total: int = Field(..., description="Total count of feedback entries")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


class FeedbackSummaryResponse(BaseModel):
    """Response model for feedback summary statistics."""

    average_rating: float = Field(
        ...,
        description="Average rating across all feedback (0.0 if no feedback)",
        examples=[4.2, 3.8, 5.0],
    )
    total_count: int = Field(
        ...,
        description="Total number of feedback entries",
        examples=[42, 15, 0],
    )
    distribution: dict[int, int] = Field(
        ...,
        description="Count of feedback by rating (1-5 as keys)",
        examples=[{1: 2, 2: 3, 3: 5, 4: 10, 5: 22}],
    )


# ============================================================================
# User Management Schemas
# ============================================================================


class UserCreateRequest(BaseModel):
    """Request body for creating a new user (org_admin only)."""

    email: NormalizedEmail = Field(
        ...,
        description="User email address",
        examples=["user@augsburg.example"],
    )
    display_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="User display name",
        examples=["Max Mustermann"],
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="User password (must meet policy)",
        examples=["SecurePassword123!"],
    )
    role: str = Field(
        ...,
        pattern=r"^(participant|coordinator|creator|org_admin)$",
        description="User role (participant, coordinator, creator, or org_admin)",
        examples=["participant"],
    )


class UserResponse(BaseModel):
    """Response model for user data (org_admin view)."""

    id: UUID = Field(..., description="User UUID")
    # Anonymized (soft-deleted) users have email/display_name nulled out by
    # the data-deletion flow but still appear in the list until hard delete.
    # Allow None so the list endpoint can render them instead of 500ing.
    email: str | None = Field(..., description="User email (null for anonymized users)")
    display_name: str | None = Field(..., description="User display name (null for anonymized users)")
    role: str = Field(..., description="User role")
    is_active: bool = Field(..., description="Whether user account is active")
    totp_enabled: bool = Field(..., description="Whether TOTP 2FA is enabled")
    created_at: datetime = Field(..., description="Creation timestamp")
    last_login: datetime | None = Field(..., description="Last login timestamp")
    is_deleted: bool = Field(False, description="Soft-deleted (grace window or anonymized)")
    deleted_at: datetime | None = Field(None, description="Soft-delete timestamp (grace window runs from here)")

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    """Response model for paginated user list."""

    items: list[UserResponse] = Field(..., description="List of users")
    total: int = Field(..., description="Total count of users")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


class UserUpdateRequest(BaseModel):
    """Request body for updating a user (org_admin only)."""

    role: str | None = Field(
        None,
        pattern=r"^(participant|coordinator|creator|org_admin)$",
        description="User role (participant, coordinator, creator, or org_admin)",
        examples=["coordinator"],
    )
    is_active: bool | None = Field(
        None,
        description="Whether user account is active",
    )
    display_name: str | None = Field(
        None,
        min_length=1,
        max_length=100,
        description="User display name",
        examples=["Max Mustermann"],
    )
    email: NormalizedEmail | None = Field(
        None,
        description="User email address (normalized to lowercase; org-scoped uniqueness enforced)",
        examples=["max@example.com"],
    )


class UserFullProfile(BaseModel):
    """Full user profile response model (org_admin view with all fields)."""

    id: UUID = Field(..., description="User UUID")
    email: str | None = Field(..., description="User email address (null for guests)")
    display_name: str = Field(..., description="User display name")
    role: str = Field(..., description="User role")
    organization_id: UUID = Field(..., description="Organization UUID")
    is_active: bool = Field(..., description="Whether user account is active")
    is_guest: bool = Field(..., description="Whether this is a guest user")
    totp_enabled: bool = Field(..., description="Whether TOTP 2FA is enabled")
    webauthn_enabled: bool = Field(..., description="Whether WebAuthn 2FA is registered")
    failed_login_attempts: int = Field(..., description="Number of failed login attempts")
    locked_until: datetime | None = Field(..., description="Timestamp until which account is locked")
    last_login: datetime | None = Field(..., description="Last login timestamp")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


# ============================================================================
# File Upload Schemas
# ============================================================================


class FileUploadResponse(BaseModel):
    """Response model for file upload."""

    object_key: str = Field(
        ...,
        description="Object key (path) in the storage bucket",
        examples=["org-uuid/run-uuid/team-uuid/abc123_photo.jpg"],
    )
    url: str = Field(
        ...,
        description="Presigned URL for accessing the file (valid for 1 hour)",
        examples=["https://s3.example.com/bucket/path?X-Amz-Signature=..."],
    )
    content_type: str = Field(
        ...,
        description="MIME type of the uploaded file",
        examples=["image/jpeg", "image/png", "audio/mpeg"],
    )
    size: int = Field(
        ...,
        description="Size of the uploaded file in bytes",
        examples=[1024, 524288, 10485760],
    )


# ============================================================================
# Guest Join Schemas
# ============================================================================


class GuestJoinCreateRequest(BaseModel):
    """Request body for creating a new team while joining a run as a guest."""

    mode: Literal["create"] = Field(default="create", description="Discriminator: 'create'")
    display_name: str | None = Field(
        None,
        min_length=1,
        max_length=50,
        description="Display name for the guest (auto-generated if not provided)",
        examples=["Maria"],
    )
    team_name: str | None = Field(
        None,
        max_length=100,
        description=(
            "Team name to create. Omit it or send null to create a team without a name. "
            "The creator sets the name later with PATCH /api/orgs/{slug}/runs/{run_id}/teams/{team_id}. "
            "When provided, it must be 1-100 characters."
        ),
        examples=["Die Drachen"],
    )
    team_color: str | None = Field(
        None,
        pattern=r"^#[0-9A-Fa-f]{6}$",
        description="Hex color (auto-picked if not provided)",
    )


class GuestJoinByCodeRequest(BaseModel):
    """Request body for joining an existing team by team code."""

    mode: Literal["join"] = Field(default="join", description="Discriminator: 'join'")
    display_name: str | None = Field(
        None,
        min_length=1,
        max_length=50,
        description="Display name for the guest (auto-generated if not provided)",
        examples=["Anna"],
    )
    team_code: str = Field(
        ...,
        min_length=4,
        max_length=6,
        description="Team share-code (case-insensitive; whitespace-trimmed)",
        examples=["7K2XQ"],
    )


# Discriminated union (Pydantic v2 style): the request states how to join, by
# creating a team or by team code. Joining never auto-picks a team.
GuestJoinRequest = Annotated[
    GuestJoinCreateRequest | GuestJoinByCodeRequest,
    Field(discriminator="mode"),
]


class JoinByCodeRequest(BaseModel):
    """Request body for a registered participant to join a team by team code."""

    team_code: str = Field(
        ...,
        min_length=4,
        max_length=6,
        description="Team share-code (case-insensitive; whitespace-trimmed)",
        examples=["7K2XQ"],
    )


class GuestJoinResponse(BaseModel):
    """Response after successfully joining a run as a guest."""

    token: str = Field(
        ...,
        description="JWT token for authentication",
    )
    user_id: UUID = Field(
        ...,
        description="UUID of the created guest user",
    )
    display_name: str = Field(
        ...,
        description="Display name of the guest user",
    )
    team_id: UUID = Field(
        ...,
        description="UUID of the team the guest joined",
    )
    team_name: str | None = Field(
        ...,
        description="Name of the team the guest joined. Null while the team has no name yet.",
    )
    team_share_code: str = Field(
        ...,
        description="The joined team's share-code, to show on the success screen",
    )
    run_id: UUID = Field(
        ...,
        description="UUID of the run the guest joined",
    )


# ============================================================================
# Admin / Log Viewer Schemas
# ============================================================================


class LogEntryResponse(BaseModel):
    """Response model for a single log entry."""

    timestamp: datetime | None = Field(None, description="ISO 8601 timestamp of the log entry")
    category: str | None = Field(None, description="Log category (audit, app, error, access)")
    event: str | None = Field(None, description="Event type identifier (e.g., user_login)")
    actor: dict[str, Any] | None = Field(None, description="Who performed the action")
    target: dict[str, Any] | None = Field(None, description="What was affected")
    changes: dict[str, Any] | None = Field(None, description="What changed (for update events)")
    media_type: str | None = Field(
        None, description="Kind of media a purge deleted: photo, video or audio (org.media_purged only)"
    )
    purged: int | None = Field(None, description="Number of media files a purge deleted (org.media_purged only)")
    request_id: str | None = Field(None, description="Request correlation ID")
    integrity_hash: str | None = Field(None, description="HMAC-SHA256 integrity hash for audit chain")
    organization_id: str | None = Field(None, description="Organization UUID")


class LogListResponse(BaseModel):
    """Response model for paginated log list."""

    items: list[LogEntryResponse] = Field(..., description="List of log entries")
    total: int = Field(..., description="Total count of matching log entries (before pagination)")
    skip: int = Field(..., description="Number of items skipped (offset)")
    limit: int = Field(..., description="Maximum items per page")


# ============================================================================
# Instance Audit Log Schemas
# ============================================================================


class InstanceAuditLogEntryResponse(BaseModel):
    """One `instance.*` audit event in the operator's own log.

    Carries no organization data. The operator acts above organizations, and
    the log holds the operator's own actions, not a member's.
    """

    timestamp: datetime | None = Field(None, description="ISO 8601 timestamp of the event")
    event: str | None = Field(None, description="Event type identifier, e.g. instance.org_created")
    operator_id: str | None = Field(None, description="UUID of the operator who performed the action")
    operator_email: str | None = Field(None, description="Email of the operator who performed the action")
    target_slug: str | None = Field(None, description="Organization slug the action targeted, if any")
    target_email: str | None = Field(None, description="Operator account the action targeted, if any")
    actor_type: str | None = Field(None, description="'cli' when a server command made the change")


class InstanceAuditLogListResponse(BaseModel):
    """Response for GET /api/instance/audit."""

    items: list[InstanceAuditLogEntryResponse] = Field(..., description="instance.* events, newest first")
    total: int = Field(..., description="Total count of matching events (before pagination)")
    offset: int = Field(..., description="Number of items skipped")
    limit: int = Field(..., description="Maximum items per page")


# ============================================================================
# Instance Legal Schemas
# ============================================================================


class InstanceLegalResponse(BaseModel):
    """The instance's own legal texts, as the operator reads and writes them.

    Every field is null until the operator's first save. A fresh instance
    answers with the same shape before anything is stored.
    """

    legal_imprint: str | None = Field(None, description="Instance-wide imprint text (HTML), null until set")
    legal_privacy: str | None = Field(None, description="Instance-wide privacy text (HTML), null until set")
    legal_accessibility: str | None = Field(None, description="Instance-wide accessibility text (HTML), null until set")
    updated_at: datetime | None = Field(None, description="When any field was last saved (null = never)")
    backup_retention_days: int = Field(
        ..., description="Days a backup of this installation can still hold deleted data (effective value)"
    )
    backup_retention_source: Literal["instance", "default"] = Field(
        ..., description="'instance' when the operator stated the value, 'default' for BACKUP_ROTATION_DAYS"
    )
    retention_days: dict[str, int] = Field(
        ..., description="The installation's default retention period per configurable Löschklasse, in days"
    )
    technical_log_days: int = Field(
        ...,
        description="Longest of the app, error and access log periods, in days (APP/ERROR/ACCESS_LOG_RETENTION_DAYS)",
    )
    audit_log_days: int = Field(..., description="Security log retention period in days (AUDIT_LOG_RETENTION_DAYS)")
    abandoned_run_days: int = Field(
        ...,
        description=(
            "Days after coalesce(started_at, created_at) at which a run that was never ended is swept "
            "(ABANDONED_RUN_DAYS), for the privacy template"
        ),
    )
    result_link_days: int = Field(
        ...,
        description="Days a published result link stays resolvable, restarted on every re-publish (RESULT_LINK_DAYS)",
    )
    map_tile_url: str = Field(
        ...,
        description="The tile server both maps load from (MAP_TILE_URL or the default), for the privacy template",
    )
    address_search_sources: list[Literal["local", "nominatim", "photon"]] = Field(
        ...,
        description="Where the address search takes its hits from, the same answer as /api/geocode/config",
    )


class InstanceLegalUpdateRequest(BaseModel):
    """Request body for PUT /api/instance/legal.

    Each field is optional; sending a field with an explicit ``null`` clears
    that text. A field left out of the payload entirely is not touched.
    """

    legal_imprint: str | None = Field(None, max_length=20000)
    legal_privacy: str | None = Field(None, max_length=20000)
    legal_accessibility: str | None = Field(None, max_length=20000)
    backup_retention_days: int | None = Field(
        None,
        ge=1,
        le=3650,
        description="Days a backup can still hold deleted data. Explicit null reverts to BACKUP_ROTATION_DAYS.",
    )


class PublicLegalResponse(BaseModel):
    """Response for GET /api/public/legal/{kind}."""

    kind: Literal["imprint", "privacy", "accessibility"] = Field(..., description="Which legal text this is")
    source: Literal["organization", "instance", "none"] = Field(
        ..., description="Where the text came from, in priority order"
    )
    text: str | None = Field(None, description="The text (HTML), or null when source is 'none'")


class CoordinatorRequestResponse(BaseModel):
    """Response after successfully submitting a coordinator request."""

    message: str = Field(
        default="Coordinator assistance requested",
        description="Confirmation message",
    )


# ============================================================================
# Run Analytics Schemas
# ============================================================================
#
# All fields here are aggregated across teams/challenges: no user ids, names,
# or e-mails are ever included. Team names are permitted (they are
# already shown on public leaderboards).


class RunAnalyticsParticipation(BaseModel):
    """Participation tile aggregates for a run."""

    team_count: int = Field(..., description="Number of non-deleted teams in this run")
    participant_count: int = Field(..., description="Total team_member rows across all teams (guests + registered)")
    guest_count: int = Field(..., description="Team members with no linked user account")
    registered_count: int = Field(..., description="Team members linked to a registered user account")


class RunAnalyticsStationFunnel(BaseModel):
    """Per-station completion funnel entry."""

    run_station_id: UUID = Field(..., description="RunStation UUID (frozen snapshot)")
    title: dict[str, str] = Field(..., description="Multilingual station title (frozen from source)")
    teams_attempted: int = Field(..., description="Distinct teams with at least one submission at this station")
    teams_scored: int = Field(..., description="Distinct teams with at least one scored submission at this station")


class RunAnalyticsChallengeStat(BaseModel):
    """Per-challenge stats entry."""

    run_challenge_id: UUID = Field(..., description="RunChallenge UUID (frozen snapshot)")
    run_station_id: UUID = Field(..., description="Parent RunStation UUID")
    station_title: dict[str, str] = Field(..., description="Multilingual title of the parent station")
    title: dict[str, str] = Field(..., description="Multilingual challenge title (frozen from source)")
    type: ChallengeType = Field(..., description="Challenge type (photo, text, qr_scan, quiz, creative)")
    submissions: int = Field(..., description="Total submissions received for this challenge")
    scored: int = Field(..., description="Submissions that have been scored")
    avg_points: float | None = Field(
        ..., description="Average awarded points across scored submissions (null if none scored)"
    )
    max_points: int | None = Field(..., description="Maximum points configured for this challenge (from scoring_rules)")
    time_team_count: int = Field(0, description="Teams whose time to the answer of this task is measured")
    time_median_seconds: int | None = Field(
        None,
        description="Median time from a team's previous answer (or its start) to the answer of this task, in seconds",
    )
    time_p25_seconds: int | None = Field(None, description="Lower quartile of that time, in seconds")
    time_p75_seconds: int | None = Field(None, description="Upper quartile of that time, in seconds")
    time_min_seconds: int | None = Field(None, description="Shortest time of a team, in seconds")
    time_max_seconds: int | None = Field(None, description="Longest time of a team, in seconds")


class RunAnalyticsTeamSummary(BaseModel):
    """Per-team summary entry."""

    team_id: UUID = Field(..., description="Team UUID")
    team_name: str | None = Field(..., description="Team display name (null while the team has no name yet)")
    member_count: int = Field(..., description="Number of team_member rows for this team")
    guest_count: int = Field(..., description="Members with no linked user account")
    total_points: int = Field(..., description="Sum of awarded points across all scored submissions")
    challenges_attempted: int = Field(..., description="Distinct challenges this team submitted for")
    challenges_scored: int = Field(..., description="Distinct challenges this team has been scored on")
    started_at: datetime | None = Field(
        None, description="Start of the team's clock: the run start, or the team's creation when it joined later"
    )
    last_answer_at: datetime | None = Field(None, description="Moment of the team's last answer")
    duration_seconds: int | None = Field(
        None, description="Working time: from the team's start to its last answer, in seconds (null without an answer)"
    )
    longest_gap_seconds: int | None = Field(
        None, description="Longest time between two answers in a row, in seconds (null with fewer than two answers)"
    )


class AnalyticsWorkingTime(BaseModel):
    """Working time of the teams of one run or of a set of runs.

    A team's working time runs from its start to its last answer. Pauses of the
    run count as working time, because the app keeps no history of them.
    """

    teams_measured: int = Field(..., description="Teams with at least one answer and a known start")
    median_seconds: int | None = Field(..., description="Median working time of these teams, in seconds")
    p25_seconds: int | None = Field(..., description="Lower quartile, in seconds")
    p75_seconds: int | None = Field(..., description="Upper quartile, in seconds")
    min_seconds: int | None = Field(..., description="Shortest working time, in seconds")
    max_seconds: int | None = Field(..., description="Longest working time, in seconds")


class RunAnalyticsTimelineBucket(BaseModel):
    """One 15-minute submissions-over-time bucket."""

    bucket_start: datetime = Field(..., description="Start of this 15-minute bucket")
    submissions: int = Field(..., description="Submissions received within this bucket")


class RunAnalyticsResponse(BaseModel):
    """Aggregated analytics document for a single run. It holds no personal data."""

    run_id: UUID = Field(..., description="Run UUID this analytics document is about")
    participation: RunAnalyticsParticipation = Field(..., description="Team/participant tile aggregates")
    stations: list[RunAnalyticsStationFunnel] = Field(..., description="Per-station completion funnel")
    challenges: list[RunAnalyticsChallengeStat] = Field(..., description="Per-challenge stats")
    teams: list[RunAnalyticsTeamSummary] = Field(..., description="Per-team summary")
    timeline: list[RunAnalyticsTimelineBucket] = Field(..., description="Submissions bucketed in 15-minute intervals")
    feedback: FeedbackSummaryResponse | None = Field(
        ..., description="Aggregated feedback (null when the run has no feedback yet)"
    )
    started_at: datetime | None = Field(None, description="When the run was started")
    ended_at: datetime | None = Field(None, description="When the run was ended")
    working_time: AnalyticsWorkingTime | None = Field(None, description="Working time over the teams of this run")


# ============================================================================
# Retention Configuration Schemas
# ============================================================================


class RetentionPeriodInfo(BaseModel):
    """One configurable Löschklasse period, with its effective value and source."""

    deletion_class: Literal["LK_A", "LK_B", "LK_C", "LK_H"] = Field(..., description="The Löschklasse")
    days: int = Field(..., description="Effective retention period in days")
    source: Literal["override", "instance", "default"] = Field(
        ..., description="Where the effective value comes from: org override, instance setting, or code default"
    )
    description: str = Field(..., description="Human-readable description of what this class governs")


class RetentionConfigResponse(BaseModel):
    """Current effective retention periods for the org plus the validation bounds."""

    periods: list[RetentionPeriodInfo] = Field(..., description="Effective LK_A/LK_B/LK_C/LK_H periods")
    min_days: int = Field(..., description="Lower bound (BACKUP_ROTATION_DAYS); no period may be shorter")
    max_days: int = Field(..., description="Upper bound for any period")
    backup_retention_days: int = Field(
        ...,
        description=(
            "Days a backup of this installation can still hold deleted data. The operator sets it, "
            "an organization reads it."
        ),
    )
    backup_retention_source: Literal["instance", "default"] = Field(
        ..., description="'instance' when the operator stated the value, 'default' for BACKUP_ROTATION_DAYS"
    )
    technical_log_days: int = Field(
        ...,
        description="Longest of the app, error and access log periods, in days (APP/ERROR/ACCESS_LOG_RETENTION_DAYS)",
    )
    audit_log_days: int = Field(..., description="Security log retention period in days (AUDIT_LOG_RETENTION_DAYS)")
    abandoned_run_days: int = Field(
        ...,
        description=(
            "Days after coalesce(started_at, created_at) at which a run that was never ended is swept "
            "(ABANDONED_RUN_DAYS), for the privacy template"
        ),
    )
    result_link_days: int = Field(
        ...,
        description="Days a published result link stays resolvable, restarted on every re-publish (RESULT_LINK_DAYS)",
    )


class RetentionConfigUpdate(BaseModel):
    """Full-set replace of the org's configurable retention overrides.

    Each field is the desired override for that class. ``null`` reverts the class
    to the instance or code default. The UI sends all of them together and
    prefills them from the GET response. A client that leaves out ``lk_h_days``
    does not change it.
    """

    lk_a_days: int | None = Field(..., description="LK_A override in days, or null to use the default")
    lk_b_days: int | None = Field(..., description="LK_B override in days, or null to use the default")
    lk_c_days: int | None = Field(..., description="LK_C override in days, or null to use the default")
    lk_h_days: int | None = Field(
        None, description="LK_H override in days, or null to use the default. Left out: not changed"
    )


# ============================================================================
# Art. 20 Data Portability — self-service export
# ============================================================================


class DataExportAccount(BaseModel):
    """The data subject's own account/profile fields."""

    user_id: UUID
    email: str | None = Field(..., description="Null if the account was anonymized")
    display_name: str | None
    role: str
    organization_id: UUID
    created_at: datetime | None
    last_login: datetime | None


class DataExportScore(BaseModel):
    points: int
    max_points: int


class DataExportParticipation(BaseModel):
    """One run the subject took part in (via a team membership)."""

    run_id: UUID
    run_name: str
    run_state: str
    team_id: UUID
    team_name: str | None
    joined_at: datetime | None
    run_started_at: datetime | None
    run_ended_at: datetime | None


class DataExportSubmission(BaseModel):
    """Metadata + content of a submission by one of the subject's teams."""

    submission_id: UUID
    run_id: UUID
    run_challenge_id: UUID
    challenge_type: str
    submitted_at: datetime | None
    content: dict[str, Any] = Field(
        ..., description="The team's answer; media fields are object-storage references, not binaries"
    )
    score: DataExportScore | None


class DataExportResponse(BaseModel):
    """GDPR Art. 20 data-portability document for a registered user.

    The keys are stable and documented, so the export is portable. Media
    binaries are not embedded: content carries references and keys only.
    """

    export_version: str = Field("1.0", description="Schema version of this export document")
    generated_at: datetime
    account: DataExportAccount
    participations: list[DataExportParticipation]
    submissions: list[DataExportSubmission]


# ============================================================================
# Date-Range Analytics — org-scoped timeseries
# ============================================================================


class AnalyticsTimeseriesMetrics(BaseModel):
    """Scalar aggregates for a set of runs (a bucket, or the range total)."""

    run_count: int = Field(..., description="Distinct runs")
    participant_count: int = Field(..., description="Total team members across the runs")
    guest_count: int = Field(..., description="Guest team members")
    registered_count: int = Field(..., description="Registered team members (participant_count − guest_count)")
    team_count: int = Field(..., description="Teams across the runs")
    submission_count: int = Field(..., description="Submissions across the runs")
    scored_count: int = Field(..., description="Submissions that have a score")
    unscored_count: int = Field(..., description="Submissions without a score (submission_count − scored_count)")
    # Points and working time. Defaults keep older callers valid.
    total_points: int = Field(0, description="Sum of the awarded points")
    avg_points_per_team: float | None = Field(
        None, description="Awarded points divided by the number of teams (null without a team)"
    )
    avg_points_per_answer: float | None = Field(
        None, description="Awarded points divided by the number of scored answers (null without a scored answer)"
    )
    points_percent: float | None = Field(
        None, description="Awarded points as a share of the points the scored answers could reach, 0 to 100"
    )
    teams_measured: int = Field(0, description="Teams with a measured working time")
    median_duration_seconds: int | None = Field(
        None, description="Median working time of these teams, in seconds (null without a measured team)"
    )


class AnalyticsPeriodTask(BaseModel):
    """One task over every run of the period that holds it."""

    key: str = Field(..., description="Stable key: the library task, or station and task title for a task without one")
    station_title: dict[str, str] = Field(..., description="Multilingual title of the station")
    title: dict[str, str] = Field(..., description="Multilingual title of the task")
    type: ChallengeType = Field(..., description="Task type")
    run_count: int = Field(..., description="Runs of the period that hold this task")
    submissions: int = Field(..., description="Answers over these runs")
    scored: int = Field(..., description="Scored answers over these runs")
    avg_points: float | None = Field(..., description="Average awarded points per scored answer")
    max_points: int | None = Field(..., description="Maximum points of the task (the highest value over the runs)")
    time_team_count: int = Field(..., description="Teams with a measured time to the answer")
    time_median_seconds: int | None = Field(..., description="Median time to the answer over these teams, in seconds")
    time_p25_seconds: int | None = Field(..., description="Lower quartile, in seconds")
    time_p75_seconds: int | None = Field(..., description="Upper quartile, in seconds")


class AnalyticsPeriodTeam(BaseModel):
    """One team of a run of the period. Team level only, no person."""

    run_id: UUID
    run_name: str
    run_date: datetime | None = Field(..., description="Start of the run, or its planned or creation time")
    team_id: UUID
    team_name: str | None
    points: int = Field(..., description="Awarded points of the team")
    answers: int = Field(..., description="Tasks the team answered")
    scored: int = Field(..., description="Answers with a score")
    duration_seconds: int | None = Field(
        ..., description="Working time: from the team's start to its last answer, in seconds"
    )


class AnalyticsTimeseriesBucket(AnalyticsTimeseriesMetrics):
    """One date bucket on the x-axis, plus its stacked metrics."""

    bucket_start: datetime = Field(
        ..., description="Start of the bucket (instance-local, truncated to the granularity)"
    )

    @property
    def median_duration_minutes(self) -> float | None:
        """The median working time for a spreadsheet cell. Not part of the JSON."""
        return None if self.median_duration_seconds is None else round(self.median_duration_seconds / 60, 1)


class AnalyticsTimeseriesRange(BaseModel):
    date_from: date = Field(..., description="Inclusive range start")
    date_to: date = Field(..., description="Inclusive range end")
    bucket: Literal["day", "week", "month"] = Field(..., description="Bucket granularity")


class AnalyticsTimeseriesResponse(BaseModel):
    """Date-bucketed org analytics over a range. All runs whose bucketing date
    (coalesce(ended_at, started_at, created_at)) falls in the range are included.
    Zero PII — aggregate counts only."""

    range: AnalyticsTimeseriesRange
    totals: AnalyticsTimeseriesMetrics
    buckets: list[AnalyticsTimeseriesBucket] = Field(..., description="One entry per non-empty date bucket, ascending")
    tasks: list[AnalyticsPeriodTask] = Field(
        default_factory=list, description="Points and time per task over the runs of the period"
    )
    teams: list[AnalyticsPeriodTeam] = Field(
        default_factory=list, description="Points and working time per team over the runs of the period"
    )


# ============================================================================
# Run comparison
# ============================================================================


class AnalyticsCompareRun(BaseModel):
    """The measures of one run in a comparison. Zero PII, team level only."""

    run_id: UUID
    name: str
    state: str = Field(..., description="Run state")
    run_date: datetime | None = Field(..., description="Start of the run, or its planned or creation time")
    started_at: datetime | None
    ended_at: datetime | None
    station_count: int
    task_count: int
    team_count: int
    participant_count: int
    guest_count: int
    registered_count: int
    submission_count: int
    scored_count: int
    completion_percent: float | None = Field(
        ..., description="Answers as a share of teams times tasks, 0 to 100 (null without a team or a task)"
    )
    teams_finished: int = Field(..., description="Teams that answered every task")
    total_points: int
    achievable_points: int = Field(..., description="Points one team can reach: the sum of the tasks' maximum")
    avg_points_per_team: float | None
    median_points_per_team: float | None
    points_percent: float | None = Field(
        ..., description="Average points per team as a share of the achievable points, 0 to 100"
    )
    working_time: AnalyticsWorkingTime


class AnalyticsCompareStationCell(BaseModel):
    """One station in one run."""

    team_count: int
    teams_attempted: int = Field(..., description="Teams with at least one answer at this station")
    percent: float | None = Field(..., description="teams_attempted as a share of the teams, 0 to 100")


class AnalyticsCompareStation(BaseModel):
    """One station row. A run that does not hold the station has no cell."""

    key: str
    title: dict[str, str]
    cells: dict[str, AnalyticsCompareStationCell] = Field(..., description="run_id -> cell")


class AnalyticsCompareTaskCell(BaseModel):
    """One task in one run."""

    team_count: int
    submissions: int
    percent: float | None = Field(..., description="Answers as a share of the teams, 0 to 100")
    scored: int
    avg_points: float | None
    max_points: int | None
    time_team_count: int
    time_median_seconds: int | None


class AnalyticsCompareTask(BaseModel):
    """One task row. A run that does not hold the task has no cell."""

    key: str
    station_key: str
    station_title: dict[str, str]
    title: dict[str, str]
    type: ChallengeType
    cells: dict[str, AnalyticsCompareTaskCell] = Field(..., description="run_id -> cell")


class AnalyticsCompareResponse(BaseModel):
    """Two to six runs side by side."""

    runs: list[AnalyticsCompareRun] = Field(..., description="In the order of the request")
    stations: list[AnalyticsCompareStation]
    tasks: list[AnalyticsCompareTask]


# ============================================================================
# Invitation Schemas
# ============================================================================


class InvitationCreateRequest(BaseModel):
    """Request body for inviting a person into the organization."""

    email: NormalizedEmail = Field(
        ...,
        description="Address the invitation is sent to; becomes the user's login",
        examples=["kollegin@augsburg.example"],
    )
    role: str = Field(
        ...,
        pattern=r"^(participant|coordinator|creator|org_admin)$",
        description="Role the invitee receives on acceptance",
        examples=["coordinator"],
    )
    display_name: str | None = Field(
        None,
        min_length=1,
        max_length=100,
        description="Optional name suggestion, pre-filled on the accept form",
        examples=["Erika Beispiel"],
    )
    language: Literal["de", "en"] = Field(
        "de",
        description="Language the invitation mail is written in",
    )


class InvitationResponse(BaseModel):
    """A pending invitation as shown to an administrator."""

    id: UUID
    email: str
    role: str
    display_name: str | None
    language: str
    invited_by_name: str
    created_at: datetime
    expires_at: datetime
    # False when the mail could not be handed to the mail server, or when this
    # deployment has no mail configured at all. The admin needs to see that,
    # otherwise a silent non-delivery reads as "they just haven't clicked yet".
    mail_delivered: bool = True


class InvitationListResponse(BaseModel):
    items: list[InvitationResponse]
    total: int


class InvitationPreviewResponse(BaseModel):
    """What an invitation link reveals before it is accepted.

    Strictly limited to what the recipient already knows from the mail in their
    inbox — no user list, no organization internals.
    """

    email: str
    role: str
    organization_name: str
    display_name_suggestion: str | None
    language: str
    expires_at: datetime


class InvitationAcceptRequest(BaseModel):
    """Request body for redeeming an invitation."""

    display_name: str = Field(..., min_length=1, max_length=100, description="Name shown in the app")
    password: str = Field(..., min_length=8, max_length=128, description="Password (must meet policy)")


class InvitationAcceptResponse(BaseModel):
    message: str
    email: str
    role: str


class MailTestRequest(BaseModel):
    """Request body for the operator's delivery probe."""

    email: NormalizedEmail = Field(..., description="Address the test message is sent to")
    language: Literal["de", "en"] = Field("de", description="Language of the test message")


class MailTestResponse(BaseModel):
    """Outcome of the delivery probe.

    ``error`` carries the transport's own message so an operator can act on it
    (bad credentials, blocked port, unknown host) without server access. The
    SMTP password is never part of that string.
    """

    success: bool
    message: str
    error: str | None = None


class MailStatusResponse(BaseModel):
    """Whether this deployment can send mail, and as whom."""

    enabled: bool
    from_address: str | None = None
    reply_to: str | None = None


# ============================================================================
# Media purge — org-admin count & purge of participant media
# ============================================================================


class MediaSummaryResponse(BaseModel):
    """How many participant media objects of each type the organization stores.

    Counts the distinct keys the org's submissions reference in their typed
    media fields — the same set a purge of that type would delete. Derived
    photo variants ride along with their original uncounted.
    """

    photo: int = Field(..., description="Stored participant photos (referenced original keys)")
    video: int = Field(..., description="Stored participant videos")
    audio: int = Field(..., description="Stored participant audio files")


class MediaPurgeRequest(BaseModel):
    """Delete every participant medium of one type in the organization.

    ``confirm_slug`` must equal the organization's own slug — the same
    confirmation gesture the instance org-deletion endpoint uses — so this
    bulk delete cannot be triggered by a stray click.
    """

    type: Literal["photo", "video", "audio"] = Field(..., description="Which media type to purge")
    confirm_slug: str = Field(..., description="Must match the organization's slug")


class MediaPurgeResponse(BaseModel):
    """Outcome of a media purge: what was purged and how many objects went."""

    type: Literal["photo", "video", "audio"] = Field(..., description="The purged media type")
    purged: int = Field(..., description="Media objects purged (variants ride along uncounted)")
