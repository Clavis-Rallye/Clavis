"""Transactional email — transport, templates and the service that joins them."""

from app.services.mail.service import (
    compose_invitation,
    compose_password_changed,
    compose_password_reset,
    compose_role_changed,
    compose_test,
    get_transport,
    invitation_url,
    is_enabled,
    mail_settings,
    password_reset_url,
    send_in_background,
    send_now,
    set_transport,
)
from app.services.mail.transport import (
    LoggingTransport,
    MailTransportError,
    OutgoingMessage,
    SMTPTransport,
)

__all__ = [
    "LoggingTransport",
    "MailTransportError",
    "OutgoingMessage",
    "SMTPTransport",
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
