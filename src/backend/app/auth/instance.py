"""
Instance-operator accounts: the commands the CLI and the tests share, and the audit actor of operator events.

Operators administer the instance, the layer above organizations. Only the CLI
(``python -m app.cli instance-admin create``) creates an account, no HTTP route.
A command on an existing account raises ``InstanceAdminNotFoundError`` when no
operator has the email.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import check_password_reuse, hash_password, validate_password_policy
from app.auth.totp import generate_totp_secret
from app.models.instance_admin import InstanceAdmin
from app.repositories.instance_admin import InstanceAdminRepository


class InstanceAdminExistsError(Exception):
    """Raised when creating an operator whose email is already taken."""


class InstanceAdminNotFoundError(Exception):
    """Raised when an account command targets an email with no matching operator."""


class InstanceAdminPasswordPolicyError(Exception):
    """Raised when the chosen password violates the configured policy.

    Carries the policy error messages so callers (the CLI) can print them.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Password does not meet policy requirements")


class InstanceAdminPasswordReuseError(Exception):
    """Raised when the chosen password matches the account's current or last password."""

    def __init__(self) -> None:
        super().__init__("Cannot reuse the current or the last password")


async def create_instance_admin(session: AsyncSession, email: str, password: str) -> InstanceAdmin:
    """
    Create an operator account with a pending TOTP setup. The email is stored lowercased.

    The TOTP secret is generated here, so the first login can hand out the
    provisioning URI. ``totp_confirmed_at`` stays null until the operator
    confirms a code (``POST /api/instance/auth/totp/confirm``).

    Raises ``InstanceAdminPasswordPolicyError`` or ``InstanceAdminExistsError``.
    """
    policy_errors = validate_password_policy(password)
    if policy_errors:
        raise InstanceAdminPasswordPolicyError(policy_errors)

    normalized_email = email.strip().lower()

    repo = InstanceAdminRepository(session)
    existing = await repo.get_by_email(normalized_email)
    if existing is not None:
        raise InstanceAdminExistsError(f"An instance admin with email '{normalized_email}' already exists")

    admin = await repo.create(
        email=normalized_email,
        password_hash=hash_password(password),
        totp_secret=generate_totp_secret(),
        totp_confirmed_at=None,
    )
    await session.commit()
    await session.refresh(admin)
    return admin


async def list_instance_admins(session: AsyncSession) -> list[InstanceAdmin]:
    """List every operator account, ordered by email (``instance-admin list``)."""
    return await InstanceAdminRepository(session).list_all()


async def _get_admin_or_raise(repo: InstanceAdminRepository, email: str) -> InstanceAdmin:
    normalized_email = email.strip().lower()
    admin = await repo.get_by_email(normalized_email)
    if admin is None:
        raise InstanceAdminNotFoundError(f"No instance admin with email '{normalized_email}'")
    return admin


async def disable_instance_admin(session: AsyncSession, email: str) -> InstanceAdmin:
    """
    Disable an operator account (``instance-admin disable``).

    A disabled operator cannot sign in (403 INSTANCE_ADMIN_DISABLED), and an
    open session of it is refused at its next request.
    """
    repo = InstanceAdminRepository(session)
    admin = await _get_admin_or_raise(repo, email)
    updated = await repo.update(admin.id, disabled_at=datetime.now(UTC))
    await session.commit()
    assert updated is not None  # admin was just found above
    return updated


async def enable_instance_admin(session: AsyncSession, email: str) -> InstanceAdmin:
    """Re-enable a disabled operator account (``instance-admin enable``)."""
    repo = InstanceAdminRepository(session)
    admin = await _get_admin_or_raise(repo, email)
    updated = await repo.update(admin.id, disabled_at=None)
    await session.commit()
    assert updated is not None  # admin was just found above
    return updated


async def reset_instance_admin_totp(session: AsyncSession, email: str) -> InstanceAdmin:
    """
    Rotate an operator's TOTP secret and end every session (``instance-admin reset-totp``).

    It clears ``totp_confirmed_at`` and ``last_login_at``, so the next login
    hands out a new provisioning URI, as after creation. The command answers a
    lost or exposed authenticator, and a session opened with the old secret
    must not outlive it. So the update also increments ``session_version``, and a token
    minted before it is refused at its next request.
    """
    repo = InstanceAdminRepository(session)
    admin = await _get_admin_or_raise(repo, email)
    # The version rises in the same UPDATE as the new secret. The database
    # computes it, not the row read above, so two concurrent revocations
    # raise it twice.
    updated = await repo.raise_session_version(
        admin.id,
        totp_secret=generate_totp_secret(),
        totp_confirmed_at=None,
        last_login_at=None,
    )
    await session.commit()
    assert updated is not None  # admin was just found above
    return updated


async def set_instance_admin_password(session: AsyncSession, email: str, password: str) -> InstanceAdmin:
    """
    Set an operator's password without the current one (``instance-admin set-password``).

    The person who runs the command has server access, so it asks for no
    current password, unlike ``POST /api/instance/auth/password``. Like that
    route, it refuses the current or the last password and keeps the old hash
    for the next such check. The update also increments ``session_version``,
    so every session ends and a token minted before it is refused at its next
    request.

    Raises ``InstanceAdminPasswordPolicyError`` or ``InstanceAdminPasswordReuseError``.
    """
    policy_errors = validate_password_policy(password)
    if policy_errors:
        raise InstanceAdminPasswordPolicyError(policy_errors)

    repo = InstanceAdminRepository(session)
    admin = await _get_admin_or_raise(repo, email)

    if check_password_reuse(password, admin.password_hash) or check_password_reuse(password, admin.last_password_hash):
        raise InstanceAdminPasswordReuseError

    # The version rises in the same UPDATE as the new hash. The database
    # computes it, not the row read above, so two concurrent revocations
    # raise it twice.
    updated = await repo.raise_session_version(
        admin.id,
        password_hash=hash_password(password),
        last_password_hash=admin.password_hash,
    )
    await session.commit()
    assert updated is not None  # admin was just found above
    return updated


def actor_from_instance_admin(admin: InstanceAdmin, ip: str) -> dict[str, Any]:
    """Return the denormalized actor of an operator audit event.

    The router records operator events with ``organization_id=None``, so the
    actor is what ties the event to the person behind it.
    """
    return {
        "instance_admin_id": str(admin.id),
        "email": admin.email,
        "ip": ip,
    }
