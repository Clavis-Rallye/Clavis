"""
Server-side sanitising of rich-text content.

Creators write long texts (rallye and station descriptions, challenge instructions,
the reference answer of a challenge, station tips headers, legal texts) in a TipTap
editor that emits HTML, and the frontend renders them through ``SafeHtml``
(DOMPurify). This module applies the same allowlist on the write path, so a stored
document never carries more than the editor can produce, whatever client sent it: no
script, no event handlers, no frames, and no image from a foreign origin. Run
snapshots and exports inherit the clean document.

Plain text without a tag passes unchanged, so a text written before the editor
existed keeps its line breaks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import nh3

ALLOWED_TAGS: set[str] = {
    "p",
    "br",
    "strong",
    "em",
    "u",
    "s",
    "h2",
    "h3",
    "ul",
    "ol",
    "li",
    "a",
    "img",
}

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "target"},
    "img": {"src", "alt"},
}

# On anchors only http, https and mailto links and relative paths survive. ``nh3`` also
# drops ``javascript:`` and ``data:`` on its own through ``url_schemes``.
URL_SCHEMES: set[str] = {"http", "https", "mailto"}

_HAS_TAG = re.compile(r"<[a-z][\s\S]*>", re.IGNORECASE)
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR = re.compile(r"""\ssrc\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)


def media_prefix_for(org_slug: str) -> str:
    """The only ``src`` prefix an inline image may carry for this organisation."""
    return f"/api/orgs/{org_slug}/media/"


def _strip_foreign_images(html: str, media_prefix: str) -> str:
    """Remove ``<img>`` elements whose ``src`` is not under the organisation's media route.

    ``nh3`` validates schemes, not paths, so it lets any relative ``src`` pass. Only our
    own media route is acceptable: anything else is a tracking pixel or a hotlink.
    """

    def keep_or_drop(match: re.Match[str]) -> str:
        tag = match.group(0)
        src_match = _SRC_ATTR.search(tag)
        src = (src_match.group(1) or src_match.group(2) or "") if src_match else ""
        if src.startswith(media_prefix) and ".." not in src:
            return tag
        return ""

    return _IMG_TAG.sub(keep_or_drop, html)


def image_sources(html: str | None) -> set[str]:
    """The ``src`` of every ``<img>`` in a stored document."""
    if not html:
        return set()
    sources: set[str] = set()
    for tag in _IMG_TAG.findall(html):
        match = _SRC_ATTR.search(tag)
        if match:
            sources.add(match.group(1) or match.group(2) or "")
    return sources


def is_html(text: str) -> bool:
    """True when the text carries at least one tag, the same test ``SafeHtml`` uses."""
    return bool(_HAS_TAG.search(text))


def sanitize_html(html: str, media_prefix: str) -> str:
    """Return ``html`` reduced to the editor's allowlist, with images only under ``media_prefix``."""
    if not html or not is_html(html):
        return html
    cleaned = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=URL_SCHEMES,
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
    return _strip_foreign_images(cleaned, media_prefix)


def sanitize_optional_html(value: str | None, media_prefix: str) -> str | None:
    """Sanitise a one-language rich text that may be absent, such as the reference answer.

    A document without visible text and without an image is stored as ``None``, so an
    emptied editor (``<p></p>``) clears the field.

    The reference answer may hold an inline image under the organisation's media route.
    A sweep that deletes unreferenced media files must read
    ``challenges.suggested_solution`` and ``run_challenges.suggested_solution`` too, or it
    deletes an image that only a reference answer shows.
    """
    if value is None:
        return None
    cleaned = sanitize_html(value, media_prefix).strip()
    if not cleaned:
        return None
    if is_html(cleaned) and not _IMG_TAG.search(cleaned) and not re.sub(r"<[^>]+>|&nbsp;|\s", "", cleaned):
        return None
    return cleaned


def sanitize_multilingual(value: Mapping[str, str] | None, media_prefix: str) -> dict[str, str] | None:
    """Sanitise every language of a multilingual text. ``None`` stays ``None``."""
    if value is None:
        return None
    return {
        locale: sanitize_html(text, media_prefix) if isinstance(text, str) else text for locale, text in value.items()
    }


def sanitize_station_tips(tips: dict[str, Any] | None, org_slug: str) -> dict[str, Any] | None:
    """
    Sanitise the rich-text header of a station's tips block and leave the rest untouched.

    Both routes that create a station use it, in the library and inside a rallye.
    """
    if not tips:
        return tips
    header = tips.get("header")
    if isinstance(header, dict):
        tips = {**tips, "header": sanitize_multilingual(header, media_prefix_for(org_slug))}
    return tips
