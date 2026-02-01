"""
Audit logging service with HMAC-SHA256 integrity chain.

Provides tamper-evident audit logging for security-relevant events. Each audit
log entry carries an HMAC-SHA256 hash chained to the previous entry, anchored
at ``GENESIS_HASH``.

Ownership: this module **builds** entry data and **emits** events through the
``audit`` structlog logger. The HMAC chain itself — hashing, sidecar
persistence, rotation, segment markers — lives in
:mod:`app.audit_handler.AuditLogHandler` so writes-to-disk and chain-bumps
happen atomically under the same lock.
"""

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog

from app.config import get_settings

if TYPE_CHECKING:
    from app.models.user import User

# Genesis hash for the start of the integrity chain
GENESIS_HASH = "0" * 64


def compute_integrity_hash(entry_data: dict[str, Any], previous_hash: str) -> str:
    """
    Compute the HMAC-SHA256 chain hash for a finalized audit entry.

    ``entry_data`` MUST already exclude the writer's transport meta-fields
    (``integrity_hash``, ``level``, ``logger``, ``service``, ``category``).
    Both writer and verifier strip the same set so they hash identical bytes.
    Timestamp and request_id remain in the hash so neither can be rewritten
    without breaking the chain.
    """
    settings = get_settings()
    hmac_key = settings.logging.hmac_audit_key

    serialized = json.dumps(entry_data, sort_keys=True, default=str)
    message = f"{serialized}|{previous_hash}"

    return hmac.new(
        hmac_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def record_audit_event(
    event: str,
    actor: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    changes: dict[str, Any] | None = None,
    organization_id: UUID | None = None,
    **extra_context: Any,
) -> None:
    """
    Emit a single audit event through the ``audit`` structlog logger.

    The :class:`~app.audit_handler.AuditLogHandler` registered on the ``audit``
    logger is responsible for hashing, fsyncing, and the sidecar bump. This
    function only assembles the structured event_dict.
    """
    audit_logger = structlog.get_logger("audit")

    entry_data: dict[str, Any] = {"event": event}
    if actor is not None:
        entry_data["actor"] = actor
    if target is not None:
        entry_data["target"] = target
    if changes is not None:
        entry_data["changes"] = changes
    if organization_id is not None:
        entry_data["organization_id"] = str(organization_id)
    entry_data.update(extra_context)

    log_event = entry_data.pop("event")
    audit_logger.info(log_event, **entry_data)


def reset_integrity_chain() -> None:
    """
    Reset the in-memory chain to genesis. Test-only.

    The audit handler owns chain state; this delegates to it. In production
    code paths the chain is recovered from the sidecar at startup.
    """
    # Imported here to avoid an import cycle at module load.
    from app.audit_handler import get_handler

    handler = get_handler()
    if handler is not None:
        handler.reset_chain_state()


# Denormalized actor/target builders.
#
# Audit entries must stay meaningful after the referenced user row is
# anonymized (GDPR erasure rewrites email + display_name in the DB, but the
# tamper-evident log is retained for a year, ``DeletionClass.LK_F``).
# display_name and email are therefore embedded at emit time instead of being
# joined at read time.


def actor_from_user(user: "User", ip: str) -> dict[str, Any]:
    """Build the standard denormalized actor dict for an authenticated user."""
    actor: dict[str, Any] = {
        "user_id": str(user.id),
        "display_name": user.display_name,
        "role": user.role.value,
        "ip": ip,
    }
    if user.email:
        actor["email"] = user.email
    return actor


def target_from_user(user: "User") -> dict[str, Any]:
    """Build the standard denormalized target dict for a user entity."""
    target: dict[str, Any] = {
        "entity": "user",
        "id": str(user.id),
        "display_name": user.display_name,
    }
    if user.email:
        target["email"] = user.email
    return target


# Standard audit event helpers for common security events


def audit_user_login(
    user: "User",
    ip: str,
    organization_id: UUID,
    method: str = "password",
) -> None:
    """Record a successful user login event."""
    record_audit_event(
        event="user_login",
        actor=actor_from_user(user, ip),
        target=target_from_user(user),
        organization_id=organization_id,
        auth_method=method,
    )


def audit_user_login_failed(
    email: str,
    ip: str,
    reason: str,
    organization_id: UUID | None = None,
) -> None:
    """Record a failed login attempt."""
    record_audit_event(
        event="user_login_failed",
        actor={"ip": ip},
        target={"entity": "user", "email": email},
        organization_id=organization_id,
        reason=reason,
    )


def audit_user_logout(
    user: "User",
    ip: str,
    organization_id: UUID,
    logout_type: str = "manual",
) -> None:
    """Record a user logout event."""
    record_audit_event(
        event="user_logout",
        actor=actor_from_user(user, ip),
        target=target_from_user(user),
        organization_id=organization_id,
        logout_type=logout_type,
    )


def audit_user_created(
    actor_user: "User",
    actor_ip: str,
    new_user: "User",
    organization_id: UUID,
) -> None:
    """Record a user creation event."""
    record_audit_event(
        event="user_created",
        actor=actor_from_user(actor_user, actor_ip),
        target=target_from_user(new_user),
        changes={"role": {"from": None, "to": new_user.role.value}},
        organization_id=organization_id,
    )


def audit_user_updated(
    actor_user: "User",
    actor_ip: str,
    target_user: "User",
    changes: dict[str, Any],
    organization_id: UUID,
) -> None:
    """Record a user profile update event."""
    record_audit_event(
        event="user_updated",
        actor=actor_from_user(actor_user, actor_ip),
        target=target_from_user(target_user),
        changes=changes,
        organization_id=organization_id,
    )


def audit_user_role_changed(
    actor_user: "User",
    actor_ip: str,
    target_user: "User",
    old_role: str,
    new_role: str,
    organization_id: UUID,
) -> None:
    """Record a user role change event."""
    record_audit_event(
        event="user_role_changed",
        actor=actor_from_user(actor_user, actor_ip),
        target=target_from_user(target_user),
        changes={"role": {"from": old_role, "to": new_role}},
        organization_id=organization_id,
    )


def audit_user_locked(
    user: "User",
    ip: str,
    organization_id: UUID,
    reason: str = "max_failed_attempts",
) -> None:
    """Record a user account lockout event. Lockouts are triggered by an
    unauthenticated party, so the actor carries only the IP."""
    record_audit_event(
        event="user_locked",
        actor={"ip": ip},
        target=target_from_user(user),
        organization_id=organization_id,
        reason=reason,
    )


def audit_user_deleted(
    actor_user: "User",
    actor_ip: str,
    target_user: "User",
    organization_id: UUID,
    deletion_mode: str = "immediate",
) -> None:
    """Record a user deletion event. Must be emitted before anonymization so
    the target dict still carries the real display_name/email.

    deletion_mode: "grace" (soft-delete, restorable for the ``DeletionClass.LK_G`` window) or
    "immediate" (GDPR Art. 17 — anonymized right away).
    """
    record_audit_event(
        event="user_deleted",
        actor=actor_from_user(actor_user, actor_ip),
        target=target_from_user(target_user),
        organization_id=organization_id,
        deletion_mode=deletion_mode,
    )


def audit_user_restored(
    actor_user: "User",
    actor_ip: str,
    target_user: "User",
    organization_id: UUID,
) -> None:
    """Record the restoration of a grace-deleted user account."""
    record_audit_event(
        event="user_restored",
        actor=actor_from_user(actor_user, actor_ip),
        target=target_from_user(target_user),
        organization_id=organization_id,
    )


def audit_config_changed(
    actor_user: "User",
    actor_ip: str,
    config_key: str,
    old_value: Any,
    new_value: Any,
    organization_id: UUID,
) -> None:
    """Record a configuration change event."""
    record_audit_event(
        event="config_changed",
        actor=actor_from_user(actor_user, actor_ip),
        target={"entity": "config", "key": config_key},
        changes={config_key: {"from": old_value, "to": new_value}},
        organization_id=organization_id,
    )


def audit_data_deletion_requested(
    actor: dict[str, Any],
    target: dict[str, Any],
    organization_id: UUID,
    reason: str = "gdpr_request",
) -> None:
    """Record a data deletion request event (GDPR right to erasure).

    Takes prebuilt actor/target dicts (``actor_from_user`` /
    ``target_from_user``): scheduled retention jobs act without a user
    context, and the target must be captured *before* anonymization rewrites
    the user's PII fields.
    """
    record_audit_event(
        event="data_deletion_requested",
        actor=actor,
        target=target,
        organization_id=organization_id,
        reason=reason,
    )


def audit_retention_swept(
    actor: dict[str, Any],
    deletion_class: str,
    swept: int,
    organization_id: UUID | None = None,
    **scope: Any,
) -> None:
    """Record a scheduled retention-sweep action (LK_A/LK_B/LK_C enforcement).

    Emitted by the daily ``RetentionSweeper`` under the system actor. ``swept``
    is the count of objects/rows/runs the class acted on; ``scope`` carries
    class-specific context (e.g. ``run_id``, ``object_key``, ``kind``). Only
    emitted when something was actually swept, so an idle sweep stays quiet.
    """
    record_audit_event(
        event="retention_swept",
        actor=actor,
        organization_id=organization_id,
        deletion_class=deletion_class,
        swept=swept,
        **scope,
    )


def audit_organization_updated(
    actor_user: "User",
    actor_ip: str,
    organization_id: UUID,
    changes: dict[str, Any],
) -> None:
    """Record an organization update event."""
    record_audit_event(
        event="organization_updated",
        actor=actor_from_user(actor_user, actor_ip),
        target={"entity": "organization", "id": str(organization_id)},
        changes=changes,
        organization_id=organization_id,
    )


def audit_organization_deleted(
    actor_user: "User",
    actor_ip: str,
    organization_id: UUID,
    organization_name: str,
) -> None:
    """Record an organization deletion event."""
    record_audit_event(
        event="organization_deleted",
        actor=actor_from_user(actor_user, actor_ip),
        target={"entity": "organization", "id": str(organization_id), "name": organization_name},
        organization_id=organization_id,
    )
