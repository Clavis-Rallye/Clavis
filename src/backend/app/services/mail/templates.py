"""
Email content in German and English.

Every message exists in both languages and in both plain text and HTML, with
the same information in each. The HTML is deliberately plain: tables and inline
styles, no external stylesheet, no image, no web font, no tracking pixel. Mail
clients strip everything else, and the recipients here — city staff on Outlook,
participants on whatever their phone ships with — are the worst case for a
clever layout.

Values coming from the database (display names, organisation names) are HTML-
escaped at interpolation; the plain-text part needs no escaping but must never
be built by stripping tags out of the HTML.
"""

from __future__ import annotations

from html import escape

from app.config import settings
from app.services.mail.transport import OutgoingMessage

# Falls back to German: the deployment is a German municipality, and an
# unreadable German mail is still better than one in a language nobody set.
DEFAULT_LANGUAGE = "de"
SUPPORTED_LANGUAGES = ("de", "en")

BRAND_PRIMARY = "#1D47A4"
TEXT_COLOR = "#1D1B20"
MUTED_COLOR = "#49454F"


def normalize_language(language: str | None) -> str:
    """Reduce any locale tag to a language we actually have copy for."""
    if not language:
        return DEFAULT_LANGUAGE
    base = language.strip().lower().replace("_", "-").split("-")[0]
    return base if base in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def _layout(
    *,
    language: str,
    title: str,
    intro: str,
    body_blocks: list[str],
    cta: tuple[str, str] | None,
    footer: str,
) -> str:
    """Wrap content blocks in the shared HTML shell.

    ``body_blocks`` and ``intro`` are inserted verbatim — callers escape their
    own interpolated values, because some blocks legitimately contain markup
    (a ``<strong>`` around a role name) and blanket-escaping here would print it.
    """
    button = ""
    if cta is not None:
        label, url = cta
        button = f"""
        <tr>
          <td style="padding: 8px 0 24px 0;">
            <a href="{escape(url, quote=True)}"
               style="display: inline-block; background: {BRAND_PRIMARY}; color: #FFFFFF;
                      text-decoration: none; padding: 14px 28px; border-radius: 100px;
                      font-weight: 600; font-size: 16px;">{escape(label)}</a>
          </td>
        </tr>
        <tr>
          <td style="padding: 0 0 24px 0; font-size: 13px; color: {MUTED_COLOR}; line-height: 1.6;">
            {escape(_FALLBACK_HINT[language])}<br>
            <span style="word-break: break-all;">{escape(url)}</span>
          </td>
        </tr>
        """

    blocks = "".join(
        f'<tr><td style="padding: 0 0 16px 0; font-size: 16px; line-height: 1.6;">{block}</td></tr>'
        for block in body_blocks
    )

    return f"""<!DOCTYPE html>
<html lang="{language}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title></head>
<body style="margin: 0; padding: 0; background: #F4F3F7;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background: #F4F3F7; padding: 24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="max-width: 560px; background: #FFFFFF; border-radius: 16px; padding: 32px;
                    font-family: 'Noto Sans', 'Segoe UI', Helvetica, Arial, sans-serif;
                    color: {TEXT_COLOR};">
        <tr>
          <td style="padding: 0 0 8px 0; font-size: 14px; font-weight: 700; letter-spacing: 0.08em;
                     text-transform: uppercase; color: {BRAND_PRIMARY};">Clavis</td>
        </tr>
        <tr>
          <td style="padding: 0 0 16px 0; font-size: 22px; font-weight: 700; line-height: 1.3;">
            {escape(title)}
          </td>
        </tr>
        <tr><td style="padding: 0 0 16px 0; font-size: 16px; line-height: 1.6;">{intro}</td></tr>
        {blocks}
        {button}
        <tr>
          <td style="padding: 16px 0 0 0; border-top: 1px solid #E7E0EC; font-size: 13px;
                     line-height: 1.6; color: {MUTED_COLOR};">{footer}</td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


_FALLBACK_HINT = {
    "de": "Falls der Button nicht funktioniert, kopiere diese Adresse in deinen Browser:",
    "en": "If the button does not work, copy this address into your browser:",
}


def _render(language: str, title: str, intro: str, blocks: list[str], cta: tuple[str, str] | None, footer: str) -> str:
    return _layout(language=language, title=title, intro=intro, body_blocks=blocks, cta=cta, footer=footer)


def _signature(language: str, organization: str) -> tuple[str, str]:
    """Return the (text, html) closing line shared by every message.

    The platform name is ``settings.brand_name``, not a
    literal, so an instance branded via ``BRAND_NAME`` brands its mail too.
    """
    org = escape(organization)
    brand = settings.brand_name
    if language == "de":
        text = (
            "Diese Nachricht wurde automatisch von Clavis verschickt, "
            f"der {brand}-Plattform von {organization}. "
            "Auf Antworten an diese Mail erhältst du eine Antwort von einem Menschen."
        )
        html = (
            "Diese Nachricht wurde automatisch von Clavis verschickt, "
            f"der {escape(brand)}-Plattform von {org}. "
            "Auf Antworten an diese Mail erhältst du eine Antwort von einem Menschen."
        )
    else:
        text = (
            f"This message was sent automatically by Clavis, the {brand} platform used by "
            f"{organization}. If you reply to it, a person will read your reply."
        )
        html = (
            f"This message was sent automatically by Clavis, the {escape(brand)} platform used by "
            f"{org}. If you reply to it, a person will read your reply."
        )
    return text, html


ROLE_LABELS: dict[str, dict[str, str]] = {
    "de": {
        "participant": "Teilnehmende",
        "coordinator": "Spielleitung",
        "creator": "Erstellung",
        "org_admin": "Verwaltung",
        "guest": "Gast",
    },
    "en": {
        "participant": "Participant",
        "coordinator": "Coordinator",
        "creator": "Creator",
        "org_admin": "Administrator",
        "guest": "Guest",
    },
}


def role_label(role: str, language: str) -> str:
    """Human-readable role name; falls back to the raw value for unknown roles."""
    return ROLE_LABELS.get(language, ROLE_LABELS[DEFAULT_LANGUAGE]).get(role, role)


def invitation_message(
    *,
    to: str,
    language: str,
    organization_name: str,
    role: str,
    inviter_name: str,
    accept_url: str,
    expires_in_days: int,
) -> OutgoingMessage:
    """ "You have been given an account — set your password" mail."""
    language = normalize_language(language)
    role_text = role_label(role, language)
    sig_text, sig_html = _signature(language, organization_name)

    if language == "de":
        subject = f"Dein Zugang zu Clavis bei {organization_name}"
        title = "Willkommen bei Clavis"
        intro_text = (
            f"{inviter_name} hat dir bei {organization_name} einen Clavis-Zugang eingerichtet — "
            f"mit der Rolle {role_text}."
        )
        intro_html = (
            f"{escape(inviter_name)} hat dir bei {escape(organization_name)} einen Clavis-Zugang "
            f"eingerichtet — mit der Rolle <strong>{escape(role_text)}</strong>."
        )
        blocks_text = [
            "Über den folgenden Link legst du dein eigenes Passwort fest und kannst dich danach anmelden. "
            "Niemand außer dir kennt dieses Passwort.",
            f"Der Link ist {expires_in_days} Tage gültig. Danach bitte eine neue Einladung anfordern.",
        ]
        blocks_html = [
            "Über den folgenden Link legst du dein eigenes Passwort fest und kannst dich danach anmelden. "
            "Niemand außer dir kennt dieses Passwort.",
            f"Der Link ist <strong>{expires_in_days} Tage</strong> gültig. Danach bitte eine neue Einladung anfordern.",
        ]
        cta_label = "Passwort festlegen"
        footer_text = (
            "Du erhältst diese Mail, weil deine Adresse für einen Zugang eingetragen wurde. "
            "War das ein Irrtum, ignoriere die Nachricht einfach — ohne den Link passiert nichts.\n\n" + sig_text
        )
        footer_html = (
            "Du erhältst diese Mail, weil deine Adresse für einen Zugang eingetragen wurde. "
            "War das ein Irrtum, ignoriere die Nachricht einfach — ohne den Link passiert nichts."
            f"<br><br>{sig_html}"
        )
    else:
        subject = f"Your Clavis access at {organization_name}"
        title = "Welcome to Clavis"
        intro_text = (
            f"{inviter_name} has set up a Clavis account for you at {organization_name}, with the role {role_text}."
        )
        intro_html = (
            f"{escape(inviter_name)} has set up a Clavis account for you at "
            f"{escape(organization_name)}, with the role <strong>{escape(role_text)}</strong>."
        )
        blocks_text = [
            "Use the link below to choose your own password and sign in. Nobody but you will know it.",
            f"The link is valid for {expires_in_days} days. After that, ask for a new invitation.",
        ]
        blocks_html = [
            "Use the link below to choose your own password and sign in. Nobody but you will know it.",
            f"The link is valid for <strong>{expires_in_days} days</strong>. After that, ask for a new invitation.",
        ]
        cta_label = "Set your password"
        footer_text = (
            "You are receiving this because your address was entered for an account. "
            "If that was a mistake, simply ignore this message — nothing happens without the link.\n\n" + sig_text
        )
        footer_html = (
            "You are receiving this because your address was entered for an account. "
            "If that was a mistake, simply ignore this message — nothing happens without the link."
            f"<br><br>{sig_html}"
        )

    text = "\n\n".join([intro_text, *blocks_text, f"{cta_label}: {accept_url}", footer_text])
    html = _render(language, title, intro_html, blocks_html, (cta_label, accept_url), footer_html)
    return OutgoingMessage(to=to, subject=subject, text_body=text, html_body=html, kind="invitation")


def password_reset_message(
    *,
    to: str,
    language: str,
    organization_name: str,
    display_name: str,
    reset_url: str,
    expires_in_minutes: int,
) -> OutgoingMessage:
    """ "Here is your password-reset link" mail."""
    language = normalize_language(language)
    sig_text, sig_html = _signature(language, organization_name)

    if language == "de":
        subject = "Passwort zurücksetzen — Clavis"
        title = "Neues Passwort festlegen"
        intro_text = f"Hallo {display_name}, für deinen Clavis-Zugang wurde ein neues Passwort angefordert."
        intro_html = f"Hallo {escape(display_name)}, für deinen Clavis-Zugang wurde ein neues Passwort angefordert."
        blocks_text = [
            f"Der Link ist {expires_in_minutes} Minuten gültig und funktioniert genau einmal.",
        ]
        blocks_html = [
            f"Der Link ist <strong>{expires_in_minutes} Minuten</strong> gültig und funktioniert genau einmal.",
        ]
        cta_label = "Passwort zurücksetzen"
        footer_text = (
            "Hast du das nicht angefordert, musst du nichts tun — dein bisheriges Passwort bleibt "
            "gültig, solange der Link nicht benutzt wird.\n\n" + sig_text
        )
        footer_html = (
            "Hast du das nicht angefordert, musst du nichts tun — dein bisheriges Passwort bleibt "
            f"gültig, solange der Link nicht benutzt wird.<br><br>{sig_html}"
        )
    else:
        subject = "Reset your password — Clavis"
        title = "Choose a new password"
        intro_text = f"Hello {display_name}, a new password was requested for your Clavis account."
        intro_html = f"Hello {escape(display_name)}, a new password was requested for your Clavis account."
        blocks_text = [f"The link is valid for {expires_in_minutes} minutes and works exactly once."]
        blocks_html = [f"The link is valid for <strong>{expires_in_minutes} minutes</strong> and works exactly once."]
        cta_label = "Reset password"
        footer_text = (
            "If you did not request this, there is nothing to do — your current password stays "
            "valid as long as the link goes unused.\n\n" + sig_text
        )
        footer_html = (
            "If you did not request this, there is nothing to do — your current password stays "
            f"valid as long as the link goes unused.<br><br>{sig_html}"
        )

    text = "\n\n".join([intro_text, *blocks_text, f"{cta_label}: {reset_url}", footer_text])
    html = _render(language, title, intro_html, blocks_html, (cta_label, reset_url), footer_html)
    return OutgoingMessage(to=to, subject=subject, text_body=text, html_body=html, kind="password_reset")


def password_changed_message(
    *,
    to: str,
    language: str,
    organization_name: str,
    display_name: str,
    changed_at: str,
) -> OutgoingMessage:
    """Security notice after a password was changed or reset."""
    language = normalize_language(language)
    sig_text, sig_html = _signature(language, organization_name)

    if language == "de":
        subject = "Dein Clavis-Passwort wurde geändert"
        title = "Passwort geändert"
        intro_text = f"Hallo {display_name}, das Passwort deines Clavis-Zugangs wurde am {changed_at} geändert."
        intro_html = (
            f"Hallo {escape(display_name)}, das Passwort deines Clavis-Zugangs wurde am {escape(changed_at)} geändert."
        )
        blocks_text = [
            "Warst du das, ist alles in Ordnung und du musst nichts weiter tun.",
            "Warst du das nicht, setze dein Passwort sofort über „Passwort vergessen“ zurück und "
            "melde dich bei der Verwaltung deiner Organisation.",
        ]
        blocks_html = [
            "Warst du das, ist alles in Ordnung und du musst nichts weiter tun.",
            "Warst du das <strong>nicht</strong>, setze dein Passwort sofort über „Passwort vergessen“ "
            "zurück und melde dich bei der Verwaltung deiner Organisation.",
        ]
        footer_text = sig_text
        footer_html = sig_html
    else:
        subject = "Your Clavis password was changed"
        title = "Password changed"
        intro_text = f"Hello {display_name}, the password for your Clavis account was changed on {changed_at}."
        intro_html = (
            f"Hello {escape(display_name)}, the password for your Clavis account was changed on {escape(changed_at)}."
        )
        blocks_text = [
            "If this was you, everything is fine and there is nothing more to do.",
            "If it was not you, reset your password immediately via “Forgot password” and "
            "contact your organisation's administrator.",
        ]
        blocks_html = [
            "If this was you, everything is fine and there is nothing more to do.",
            "If it was <strong>not</strong> you, reset your password immediately via “Forgot "
            "password” and contact your organisation's administrator.",
        ]
        footer_text = sig_text
        footer_html = sig_html

    text = "\n\n".join([intro_text, *blocks_text, footer_text])
    html = _render(language, title, intro_html, blocks_html, None, footer_html)
    return OutgoingMessage(to=to, subject=subject, text_body=text, html_body=html, kind="password_changed")


def role_changed_message(
    *,
    to: str,
    language: str,
    organization_name: str,
    display_name: str,
    new_role: str,
) -> OutgoingMessage:
    """Notice that an administrator changed the recipient's role."""
    language = normalize_language(language)
    role_text = role_label(new_role, language)
    sig_text, sig_html = _signature(language, organization_name)

    if language == "de":
        subject = "Deine Rolle in Clavis wurde geändert"
        title = "Neue Rolle"
        intro_text = f"Hallo {display_name}, deine Rolle bei {organization_name} wurde auf {role_text} geändert."
        intro_html = (
            f"Hallo {escape(display_name)}, deine Rolle bei {escape(organization_name)} wurde auf "
            f"<strong>{escape(role_text)}</strong> geändert."
        )
        blocks_text = ["Die Änderung gilt ab sofort — beim nächsten Anmelden siehst du die passenden Bereiche."]
        blocks_html = list(blocks_text)
        footer_text = sig_text
        footer_html = sig_html
    else:
        subject = "Your role in Clavis has changed"
        title = "New role"
        intro_text = f"Hello {display_name}, your role at {organization_name} was changed to {role_text}."
        intro_html = (
            f"Hello {escape(display_name)}, your role at {escape(organization_name)} was changed to "
            f"<strong>{escape(role_text)}</strong>."
        )
        blocks_text = ["The change is effective now — you will see the matching areas at your next sign-in."]
        blocks_html = list(blocks_text)
        footer_text = sig_text
        footer_html = sig_html

    text = "\n\n".join([intro_text, *blocks_text, footer_text])
    html = _render(language, title, intro_html, blocks_html, None, footer_html)
    return OutgoingMessage(to=to, subject=subject, text_body=text, html_body=html, kind="role_changed")


def test_message(*, to: str, language: str, organization_name: str, triggered_by: str) -> OutgoingMessage:
    """Operator probe: proves the deployment can actually deliver mail."""
    language = normalize_language(language)
    sig_text, sig_html = _signature(language, organization_name)

    if language == "de":
        subject = "Clavis: Test der E-Mail-Zustellung"
        title = "Zustellung funktioniert"
        intro_text = f"Diese Testnachricht wurde von {triggered_by} aus den Organisationseinstellungen ausgelöst."
        intro_html = (
            f"Diese Testnachricht wurde von {escape(triggered_by)} aus den Organisationseinstellungen ausgelöst."
        )
        blocks_text = [
            "Da sie angekommen ist, kann Clavis Einladungen und Passwort-Links zustellen.",
            "Landet sie im Spam-Ordner, markiere sie als „Kein Spam“. Dann kommen auch die echten "
            "Nachrichten zuverlässig an.",
        ]
        blocks_html = [
            "Da sie angekommen ist, kann Clavis Einladungen und Passwort-Links zustellen.",
            "Landet sie im Spam-Ordner, markiere sie als „Kein Spam“. Dann kommen auch die echten "
            "Nachrichten zuverlässig an.",
        ]
        footer_text = sig_text
        footer_html = sig_html
    else:
        subject = "Clavis: email delivery test"
        title = "Delivery works"
        intro_text = f"This test message was triggered by {triggered_by} from the organisation settings."
        intro_html = f"This test message was triggered by {escape(triggered_by)} from the organisation settings."
        blocks_text = [
            "Since it arrived, Clavis can deliver invitations and password links.",
            "If it landed in your spam folder, mark it as “not spam” so the real messages arrive reliably.",
        ]
        blocks_html = list(blocks_text)
        footer_text = sig_text
        footer_html = sig_html

    text = "\n\n".join([intro_text, *blocks_text, footer_text])
    html = _render(language, title, intro_html, blocks_html, None, footer_html)
    return OutgoingMessage(to=to, subject=subject, text_body=text, html_body=html, kind="test")
