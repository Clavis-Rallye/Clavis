"""
PII filtering of responses by the role of the caller, per the matrix below.

Every API response with user data must pass through this module, on the backend
before it is sent. A filter in the frontend alone is never enough.

PII Visibility Matrix:
    | PII Scope                           | Guest | Participant | Coordinator | Creator | Org Admin |
    |-------------------------------------|:-----:|:-----------:|:-----------:|:-------:|:---------:|
    | Own profile data                    |   x   |      x      |      x      |    x    |     x     |
    | Own team members (display name)     |   x   |      x      |      x      |    x    |     x     |
    | All participants in run (name+team) |       |             |      x      |    x    |     x     |
    | Participant email / account details |       |             |             |         |     x     |
    | Audit log actor references          |       |             |             |         |     x     |
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.user import User, UserRole


class UserPublicProfile(BaseModel):
    """
    Public user profile visible to guests and participants.

    This is the minimal profile visible to users viewing their own team
    or viewing other teams within a run (depending on role).

    Fields:
        id: User UUID (for internal linking, not PII)
        display_name: User's display name
        role: User's role (for UI display purposes)
    """

    id: UUID = Field(..., description="User UUID")
    display_name: str = Field(..., description="User display name")
    role: str = Field(..., description="User role")

    class Config:
        from_attributes = True


class UserCoordinatorProfile(UserPublicProfile):
    """
    User profile visible to coordinators, creators, and org_admins.

    Extends public profile with additional metadata useful for run coordination,
    but still excludes sensitive account details like email.

    Coordinators can see all participants in their runs but not account-level details.
    """

    # No fields beyond UserPublicProfile yet.
    pass


class UserFullProfile(UserCoordinatorProfile):
    """
    Full user profile visible only to org_admins.

    Includes all user information including account details and contact info.
    This is the only schema that exposes email addresses and security metadata.

    Fields (in addition to UserCoordinatorProfile):
        email: User email address
        is_active: Whether user account is active
        last_login: Timestamp of last login (null if never logged in)
        created_at: Account creation timestamp
        organization_id: Parent organization UUID
    """

    email: str = Field(..., description="User email address")
    is_active: bool = Field(..., description="Whether account is active")
    last_login: str | None = Field(None, description="Last login timestamp (ISO 8601)")
    created_at: str = Field(..., description="Account creation timestamp (ISO 8601)")
    organization_id: UUID = Field(..., description="Parent organization UUID")

    class Config:
        from_attributes = True

    @classmethod
    def from_user(cls, user: User) -> "UserFullProfile":
        return cls(
            id=user.id,
            display_name=user.display_name,
            role=user.role.value,
            email=user.email,
            is_active=user.is_active,
            last_login=user.last_login.isoformat() if user.last_login else None,
            created_at=user.created_at.isoformat(),
            organization_id=user.organization_id,
        )


def filter_user_profile(
    user: User,
    viewer: User,
    _context: str | None = None,
) -> UserPublicProfile | UserCoordinatorProfile | UserFullProfile:
    """Return the profile of ``user`` that the role of ``viewer`` may see (the PII visibility matrix).

    - Guest and participant see the public profile (id, display_name, role).
    - Coordinator and creator see the coordinator profile, which has the same
      fields as the public one today.
    - Org admin sees the full profile with email and account details.
    - Every user sees their own full profile, whatever the role.

    ``_context`` is unused and reserved for finer filtering.
    """
    if user.id == viewer.id:
        return UserFullProfile.from_user(user)

    # The function does not check the organization, so a caller must pass only
    # users of the viewer's organization.
    if viewer.role == UserRole.ORG_ADMIN:
        return UserFullProfile.from_user(user)

    if viewer.role in (UserRole.COORDINATOR, UserRole.CREATOR):
        return UserCoordinatorProfile(
            id=user.id,
            display_name=user.display_name,
            role=user.role.value,
        )

    return UserPublicProfile(
        id=user.id,
        display_name=user.display_name,
        role=user.role.value,
    )


def filter_user_list(
    users: list[User],
    viewer: User,
    _context: str | None = None,
) -> list[UserPublicProfile | UserCoordinatorProfile | UserFullProfile]:
    return [filter_user_profile(user, viewer, _context) for user in users]


def filter_dict_fields(
    data: dict[str, Any],
    viewer: User,
    sensitive_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Remove the sensitive fields from a dict unless the viewer is an org admin.

    For unstructured data such as audit log entries. The fields are removed from
    the dict, from every nested dict and from every dict that is a direct item of
    a list. A list inside a list, and any other container, keeps its fields.
    ``sensitive_fields`` replaces the default list.
    """
    if sensitive_fields is None:
        sensitive_fields = [
            "email",
            "password_hash",
            "totp_secret",
            "webauthn_credentials",
            "totp_recovery_codes",
            "last_password_hash",
            "failed_login_attempts",
            "locked_until",
        ]

    if viewer.role == UserRole.ORG_ADMIN:
        return data

    # A shallow copy is enough: the recursion below replaces nested containers
    # with filtered copies, so the input is never changed.
    filtered = data.copy()

    for field in sensitive_fields:
        if field in filtered:
            del filtered[field]

    for key, value in filtered.items():
        if isinstance(value, dict):
            filtered[key] = filter_dict_fields(value, viewer, sensitive_fields)
        elif isinstance(value, list):
            filtered[key] = [
                filter_dict_fields(item, viewer, sensitive_fields) if isinstance(item, dict) else item for item in value
            ]

    return filtered
