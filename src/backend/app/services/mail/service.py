"""
Transactional email service.

The one rule that shapes this module: **a mail failure never breaks the
operation that triggered it.** An invitation whose mail bounces still exists and
can be resent; a password reset whose confirmation fails still reset the
password. Callers therefore use :func:`send_in_background`, which swallows and
logs transport errors, and reserve :func:`send_now` for the operator test where
the error *is* the answer.
"""

from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import BackgroundTasks

from app.config import MailSettings, get_settings
from app.services.mail import templates
from app.services.mail.transport import (
    LoggingTransport,
    MailTransportError,
    OutgoingMessage,
    Transport,
    build_transport,
)

logger = structlog.get_logger("app")

_transport: Transport | None = None


def get_transport() -> Transport:
    """Return the process-wide transport, building it on first use."""
    global _transport
    if _transport is None:
        _transport = build_transport(get_settings().mail)
    return _transport


def set_transport(transport: Transport | None) -> None:
    """Replace the transport. Tests use this to install a recording one."""
    global _transport
    _transport = transport


def mail_settings() -> MailSettings:
    return get_settings().mail


def is_enabled() -> bool:
    """Whether this deployment can actually deliver mail."""
    return mail_settings().enabled


async def send_now(message: OutgoingMessage) -> None:
    """Send and let transport errors propagate.

    Only for the operator test-mail endpoint, where a silent failure would
    defeat the entire point of the probe.
    """
    await get_transport().send(message, mail_settings())


def send_in_background(background: BackgroundTasks, message: OutgoingMessage) -> None:
    """Queue a message to go out after the response has been returned.

    Delivery to a remote MX takes seconds; making a user wait for it would turn
    "invite a colleague" into a request that looks hung.
    """

    async def _task() -> None:
        try:
            await get_transport().send(message, mail_settings())
        except MailTransportError as exc:
            logger.error(
                "mail_send_failed",
                recipient=message.to,
                kind=message.kind,
                error=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — a background task must never escape
            logger.error(
                "mail_send_failed",
                recipient=message.to,
                kind=message.kind,
                error=f"{type(exc).__name__}: {exc}",
            )

    background.add_task(_task)


# ---------------------------------------------------------------------------
# Link builders
#
# Every link is built from the configured public base URL. Deriving it from the
# request's Host header would let an attacker who can set that header receive a
# victim's reset link (a classic host-header poisoning).
# ---------------------------------------------------------------------------


def invitation_url(token: str) -> str:
    return f"{mail_settings().public_base_url}/accept-invite?token={token}"


def password_reset_url(token: str) -> str:
    return f"{mail_settings().public_base_url}/reset-password?token={token}"


# ---------------------------------------------------------------------------
# Composers — one per message type the application sends
# ---------------------------------------------------------------------------


def compose_invitation(
    *,
    to: str,
    language: str,
    organization_name: str,
    role: str,
    inviter_name: str,
    token: str,
) -> OutgoingMessage:
    return templates.invitation_message(
        to=to,
        language=language,
        organization_name=organization_name,
        role=role,
        inviter_name=inviter_name,
        accept_url=invitation_url(token),
        expires_in_days=mail_settings().invitation_expiry_days,
    )


def compose_password_reset(
    *,
    to: str,
    language: str,
    organization_name: str,
    display_name: str,
    token: str,
    expires_in_minutes: int,
) -> OutgoingMessage:
    return templates.password_reset_message(
        to=to,
        language=language,
        organization_name=organization_name,
        display_name=display_name,
        reset_url=password_reset_url(token),
        expires_in_minutes=expires_in_minutes,
    )


def compose_password_changed(
    *,
    to: str,
    language: str,
    organization_name: str,
    display_name: str,
    changed_at: datetime,
) -> OutgoingMessage:
    language = templates.normalize_language(language)
    # Formatted server-side because the recipient reads a static mail, not a
    # locale-aware UI. UTC is stated explicitly so "that wasn't me at 03:00"
    # is answerable.
    stamp = changed_at.strftime("%d.%m.%Y, %H:%M UTC" if language == "de" else "%Y-%m-%d, %H:%M UTC")
    return templates.password_changed_message(
        to=to,
        language=language,
        organization_name=organization_name,
        display_name=display_name,
        changed_at=stamp,
    )


def compose_role_changed(
    *,
    to: str,
    language: str,
    organization_name: str,
    display_name: str,
    new_role: str,
) -> OutgoingMessage:
    return templates.role_changed_message(
        to=to,
        language=language,
        organization_name=organization_name,
        display_name=display_name,
        new_role=new_role,
    )


def compose_test(
    *,
    to: str,
    language: str,
    organization_name: str,
    triggered_by: str,
) -> OutgoingMessage:
    return templates.test_message(
        to=to,
        language=language,
        organization_name=organization_name,
        triggered_by=triggered_by,
    )


__all__ = [
    "LoggingTransport",
    "MailTransportError",
    "OutgoingMessage",
    "compose_invitation",
    "compose_password_changed",
    "compose_password_reset",
    "compose_role_changed",
    "compose_test",
    "get_transport",
    "invitation_url",
    "is_enabled",
    "mail_settings",
    "password_reset_url",
    "send_in_background",
    "send_now",
    "set_transport",
]
