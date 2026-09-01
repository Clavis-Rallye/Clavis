"""Transactional mail carries the configured brand name.

The German signature interpolates ``settings.brand_name``. An instance branded through
``BRAND_NAME`` shows the configured value in every rendered mail, in both MIME parts.
The plain-text part is never built by stripping tags out of the HTML.
"""

import pytest

from app.config import get_settings
from app.services.mail import templates


@pytest.fixture
def configured_brand():
    """Set the cached Settings instance to an example brand name.

    ``get_settings()`` is ``lru_cache``d and ``templates`` imports the same
    singleton, so the change is visible to the rendered mail and restored after.
    """
    settings = get_settings()
    previous = settings.brand_name
    settings.brand_name = "Stadtrallye Musterstadt"
    yield "Stadtrallye Musterstadt"
    settings.brand_name = previous


@pytest.mark.parametrize(
    "build",
    [
        lambda: templates.invitation_message(
            to="a@b.de",
            language="de",
            organization_name="Stadt Augsburg",
            role="coordinator",
            inviter_name="Aaron",
            accept_url="https://app.clavis-rallye.de/accept-invite?token=t",
            expires_in_days=7,
        ),
        lambda: templates.password_reset_message(
            to="a@b.de",
            language="de",
            organization_name="Stadt Augsburg",
            display_name="Test",
            reset_url="https://app.clavis-rallye.de/reset-password?token=t",
            expires_in_minutes=60,
        ),
    ],
    ids=["invitation", "password_reset"],
)
def test_default_brand_in_rendered_mail(build):
    """With BRAND_NAME unset the default brand reaches the mail."""
    brand = get_settings().brand_name
    message = build()
    assert brand in message.text_body
    assert brand in message.html_body


@pytest.mark.parametrize(
    "build",
    [
        lambda: templates.invitation_message(
            to="a@b.de",
            language="de",
            organization_name="Stadt Augsburg",
            role="coordinator",
            inviter_name="Aaron",
            accept_url="https://app.clavis-rallye.de/accept-invite?token=t",
            expires_in_days=7,
        ),
        lambda: templates.password_reset_message(
            to="a@b.de",
            language="de",
            organization_name="Stadt Augsburg",
            display_name="Test",
            reset_url="https://app.clavis-rallye.de/reset-password?token=t",
            expires_in_minutes=60,
        ),
    ],
    ids=["invitation", "password_reset"],
)
def test_configured_brand_in_both_mime_parts(build, configured_brand: str):
    """A configured BRAND_NAME reaches text and HTML alike."""
    message = build()
    assert configured_brand in message.text_body
    assert configured_brand in message.html_body


def test_configured_brand_in_both_mime_parts_of_an_english_mail(configured_brand: str):
    """The English signature names the configured brand too, not just the German one."""
    message = templates.password_reset_message(
        to="a@b.de",
        language="en",
        organization_name="City of Augsburg",
        display_name="Test",
        reset_url="https://app.clavis-rallye.de/reset-password?token=t",
        expires_in_minutes=60,
    )
    assert configured_brand in message.text_body
    assert configured_brand in message.html_body


def test_brand_is_html_escaped_in_the_html_part(monkeypatch):
    """The brand is operator input. Markup in it is escaped in the HTML part and verbatim in the text part."""
    settings = get_settings()
    monkeypatch.setattr(settings, "brand_name", "Brause & <Rallye>")

    message = templates.password_reset_message(
        to="a@b.de",
        language="de",
        organization_name="Stadt Augsburg",
        display_name="Test",
        reset_url="https://app.clavis-rallye.de/reset-password?token=t",
        expires_in_minutes=60,
    )
    assert "Brause & <Rallye>" in message.text_body
    assert "Brause &amp; &lt;Rallye&gt;" in message.html_body
    assert "Brause & <Rallye>" not in message.html_body
