"""
Transports for transactional email, selected by configuration:

- :class:`SMTPTransport` submits over SMTP, with implicit TLS or STARTTLS.
- :class:`LoggingTransport` records what would have been sent and sends nothing.
  It is the default when ``SMTP_HOST`` is empty, in local development and the
  whole test suite. A CI run must never be able to reach a real mailbox.

Composing the messages is the job of :mod:`app.services.mail.service`.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

import structlog

from app.config import MailSettings

logger = structlog.get_logger("app")


@dataclass
class OutgoingMessage:
    """A composed message, ready for any transport."""

    to: str
    subject: str
    text_body: str
    html_body: str
    # Free-form label for logs and the test transport ("invitation", "password_reset", …).
    kind: str = "generic"

    def to_mime(self, settings: MailSettings) -> EmailMessage:
        """Render into a ``multipart/alternative`` MIME message.

        Both parts carry the same information: a mail client that refuses HTML,
        or a screen reader behind it, must not get a message without the link.
        """
        message = EmailMessage()
        message["From"] = formataddr((settings.mail_from_name, settings.mail_from_address))
        message["To"] = self.to
        message["Subject"] = self.subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=settings.mail_from_address.split("@")[-1])
        if settings.mail_reply_to:
            message["Reply-To"] = settings.mail_reply_to
        # Transactional mail must never be interpreted as a bulk campaign, and
        # must not generate out-of-office storms.
        message["Auto-Submitted"] = "auto-generated"
        message["X-Auto-Response-Suppress"] = "All"

        message.set_content(self.text_body)
        message.add_alternative(self.html_body, subtype="html")
        return message


class MailTransportError(RuntimeError):
    """A message could not be handed to the mail server."""


class LoggingTransport:
    """Records messages instead of sending them.

    Used when no SMTP host is configured. ``sent`` keeps the messages in memory
    so tests can assert on them without a mail server.
    """

    def __init__(self) -> None:
        self.sent: list[OutgoingMessage] = []

    async def send(self, message: OutgoingMessage, settings: MailSettings) -> None:  # noqa: ARG002
        # The unused `settings` keeps the signature interchangeable with SMTPTransport.
        self.sent.append(message)
        logger.info(
            "mail_suppressed",
            recipient=message.to,
            kind=message.kind,
            subject=message.subject,
            reason="no SMTP host configured",
        )

    def clear(self) -> None:
        self.sent.clear()


@dataclass
class SMTPTransport:
    """Submits messages over SMTP.

    A fresh connection per message. The volume is a handful of mails per event,
    so a persistent connection would add reconnect and staleness handling for no
    measurable gain.
    """

    _context: ssl.SSLContext = field(default_factory=ssl.create_default_context)

    async def send(self, message: OutgoingMessage, settings: MailSettings) -> None:
        # Imported lazily so a deployment without the dependency installed still
        # boots as long as mail stays disabled.
        import aiosmtplib

        mime = message.to_mime(settings)
        use_tls = settings.smtp_tls_mode == "ssl"
        start_tls = settings.smtp_tls_mode == "starttls"

        try:
            await aiosmtplib.send(
                mime,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username or None,
                password=settings.smtp_password or None,
                use_tls=use_tls,
                start_tls=start_tls or None,
                tls_context=self._context if (use_tls or start_tls) else None,
                timeout=settings.smtp_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 — every transport failure is reported the same way
            # str(exc) on an aiosmtplib error carries the server's response, never
            # the credentials we passed in. Keep it that way when touching this.
            raise MailTransportError(f"{type(exc).__name__}: {exc}") from exc

        logger.info("mail_sent", recipient=message.to, kind=message.kind)


Transport = LoggingTransport | SMTPTransport


def build_transport(settings: MailSettings) -> Transport:
    """Pick the transport that matches the configuration."""
    if not settings.enabled:
        return LoggingTransport()
    return SMTPTransport()
