"""
Server-side sanitising of rich-text content.

Creators write long texts (rallye and station descriptions, challenge instructions,
the reference answer of a challenge, station tips headers, legal texts) in a TipTap
editor that emits HTML, and the frontend renders them through ``SafeHtml``
(DOMPurify). This module applies the same allowlist on the write path, so a stored
document never carries more than the editor can produce, whatever client sent it: no
script, no event handlers, no frames, no style, class or id, and no image from a
foreign origin. Run snapshots and exports inherit the clean document.

The allowlist is written once, in ``src/frontend/src/utils/richTextContract.json``.
The constants below follow that file, and the frontend renderer follows it too. A test
compares the constants with the file, so the two filters cannot drift apart.

Plain text without a tag passes unchanged, so a text written before the editor
existed keeps its line breaks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from html.parser import HTMLParser
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
    "blockquote",
    "hr",
    "a",
    "img",
    "figure",
    "figcaption",
    "table",
    "tbody",
    "tr",
    "th",
    "td",
}

# ``rel`` on anchors is not listed: ``nh3`` sets it itself through ``link_rel`` and refuses it in
# ``attributes``. The two ``data-`` attributes are listed here and checked against
# ``ALLOWED_ATTRIBUTE_VALUES``.
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "target"},
    "img": {"src", "alt", "data-width", "data-align"},
    "figure": {"data-width", "data-align"},
}

# Image size (percent of the text width) and position. Any other value removes the attribute and
# keeps the element.
ALLOWED_ATTRIBUTE_VALUES: dict[str, set[str]] = {
    "data-width": {"25", "50", "75", "100"},
    "data-align": {"left", "center", "right"},
}

# On anchors only http, https and mailto links and relative paths survive. ``nh3`` also
# drops ``javascript:`` and ``data:`` on its own through ``url_schemes``.
URL_SCHEMES: set[str] = {"http", "https", "mailto"}

LINK_REL = "noopener noreferrer"

# What ``nh3`` receives: free attributes in ``attributes``, value-checked ones in ``tag_attribute_values``.
_NH3_ATTRIBUTES: dict[str, set[str]] = {
    tag: free
    for tag, names in ALLOWED_ATTRIBUTES.items()
    if (free := {name for name in names if name not in ALLOWED_ATTRIBUTE_VALUES})
}
_NH3_ATTRIBUTE_VALUES: dict[str, dict[str, set[str]]] = {
    tag: {name: ALLOWED_ATTRIBUTE_VALUES[name] for name in names if name in ALLOWED_ATTRIBUTE_VALUES}
    for tag, names in ALLOWED_ATTRIBUTES.items()
    if any(name in ALLOWED_ATTRIBUTE_VALUES for name in names)
}

_HAS_TAG = re.compile(r"<[a-z][\s\S]*>", re.IGNORECASE)
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR = re.compile(r"""\ssrc\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)


def media_prefix_for(org_slug: str) -> str:
    """The only ``src`` prefix an inline image may carry for this organisation."""
    return f"/api/orgs/{org_slug}/media/"


def _is_media_source(src: str, media_prefix: str) -> bool:
    """True when ``src`` lies under the organisation's media route and cannot climb out of it."""
    lowered = src.lower()
    return src.startswith(media_prefix) and ".." not in src and "\\" not in src and "%2e" not in lowered


class _ForeignImageFinder(HTMLParser):
    """Collect the spans of a clean document to remove: foreign images and figures without an image.

    The input is the output of ``nh3``, so it is well formed. The parser decodes attribute values,
    which a regular expression over the raw text would not.
    """

    def __init__(self, html: str, media_prefix: str) -> None:
        super().__init__(convert_charrefs=False)
        self._html = html
        self._media_prefix = media_prefix
        self._line_starts = [0]
        for match in re.finditer("\n", html):
            self._line_starts.append(match.end())
        self._figures: list[list[int]] = []  # [start offset, 1 when the figure holds an image]
        self.spans: list[tuple[int, int]] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _drop_inexact_values(self, start: int, attrs: list[tuple[str, str | None]]) -> None:
        """``nh3`` compares the values of ``data-width`` and ``data-align`` without regard to case.

        The contract lists exact values, so ``LEFT`` goes too.
        """
        for name, value in attrs:
            allowed = ALLOWED_ATTRIBUTE_VALUES.get(name)
            if allowed is None or value in allowed:
                continue
            tag_text = self.get_starttag_text() or ""
            for match in re.finditer(rf"\s{re.escape(name)}(?:=\"[^\"]*\")?(?=[\s/>])", tag_text):
                self.spans.append((start + match.start(), start + match.end()))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self._offset()
        self._drop_inexact_values(start, attrs)
        if tag == "figure":
            self._figures.append([start, 0])
        elif tag == "img":
            src = dict(attrs).get("src") or ""
            if _is_media_source(src, self._media_prefix):
                if self._figures:
                    self._figures[-1][1] = 1
            else:
                self.spans.append((start, start + len(self.get_starttag_text() or "")))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag != "figure" or not self._figures:
            return
        start, has_image = self._figures.pop()
        if not has_image:
            self.spans.append((start, self._html.index(">", self._offset()) + 1))


def _strip_foreign_images(html: str, media_prefix: str) -> str:
    """Remove ``<img>`` elements whose ``src`` is not under the organisation's media route.

    ``nh3`` validates schemes, not paths, so it lets any relative ``src`` pass. Only our
    own media route is acceptable: anything else is a tracking pixel or a hotlink. A
    ``<figure>`` left without an image goes with its caption.
    """
    finder = _ForeignImageFinder(html, media_prefix)
    finder.feed(html)
    finder.close()
    if not finder.spans:
        return html
    merged: list[tuple[int, int]] = []
    for start, end in sorted(finder.spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        parts.append(html[cursor:start])
        cursor = end
    parts.append(html[cursor:])
    return "".join(parts)


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
        attributes=_NH3_ATTRIBUTES,
        tag_attribute_values=_NH3_ATTRIBUTE_VALUES,
        url_schemes=URL_SCHEMES,
        link_rel=LINK_REL,
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
