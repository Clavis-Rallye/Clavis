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

Legal texts written before this filter existed can hold ``h1``, ``pre`` and ``code``. ``nh3`` lets those
three pass, and they are mapped on the parsed tree right after it, so no word is lost and none ends up
outside a block: ``h1`` becomes ``h2``, a ``pre`` block becomes paragraphs (a blank line starts a new
paragraph, a single line break becomes ``br``) and ``code`` becomes plain text. The frontend does the same
in ``toEditorHtml``. No regular expression runs over the raw input: the time of every step grows with the
length of the text, and a text longer than ``RICH_TEXT_MAX_LENGTH`` is refused.

A table has at most three columns and does not stand inside a cell. Any other table becomes one
paragraph per row, the same rule as in ``src/frontend/src/utils/richTextTables.ts``, and
``src/frontend/src/utils/richTextTableCases.json`` pins both filters to the same output.

Plain text without a tag passes unchanged, so a text written before the editor
existed keeps its line breaks.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote

import nh3

# The longest rich text of one language. The city's longest legal text has 16,220 characters, and tables and
# figures add markup. At this length the filter, ``nh3`` included, stays near 0.1 s.
RICH_TEXT_MAX_LENGTH = 50_000
# Two fields have a smaller limit: the header of the station tips and the reference answer of a challenge.
TIPS_HEADER_MAX_LENGTH = 2_000
REFERENCE_ANSWER_MAX_LENGTH = 10_000


class RichTextTooLongError(ValueError):
    """A rich text is longer than its limit, as it came in or as it would be stored.

    Cleaning can make a text longer: ``&`` is stored as ``&amp;``, and a link gets its ``rel``. So the limit
    holds twice, for the text that came in and for the text that the write would store. ``loc`` names the
    field of the request, and the API answers 422 with it (``app/main.py``).
    """

    def __init__(self, max_length: int, loc: tuple[str, ...] = (), *, cleaned: bool = False) -> None:
        self.max_length = max_length
        self.loc = loc
        self.cleaned = cleaned
        super().__init__(f"Text must be at most {max_length} characters" + (" after cleaning" if cleaned else ""))

    def detail(self) -> dict[str, Any]:
        """The error as one entry of a 422 answer, in the shape of a validation error of the body."""
        message = f"String should have at most {self.max_length} characters"
        if self.cleaned:
            message += " after cleaning. Special characters and links take more room in the stored text"
        return {
            "type": "string_too_long",
            "loc": ["body", *self.loc],
            "msg": message,
            "ctx": {"max_length": self.max_length},
        }


# A table inside a table inside a table: the editor makes none, and every table inside a cell becomes
# paragraphs. Each table around another reads the content of the inner one again, so the work of the table
# rule grows with the depth times the size. This bound keeps it linear in the size.
TABLE_MAX_NESTING = 20


class RichTextTooNestedError(ValueError):
    """A rich text holds tables nested deeper than ``TABLE_MAX_NESTING``. The API answers 422 with the field."""

    def __init__(self, loc: tuple[str, ...] = ()) -> None:
        self.max_nesting = TABLE_MAX_NESTING
        self.loc = loc
        super().__init__(f"Tables must be nested at most {TABLE_MAX_NESTING} deep")

    def detail(self) -> dict[str, Any]:
        """The error as one entry of a 422 answer, in the shape of a validation error of the body."""
        return {
            "type": "rich_text_too_nested",
            "loc": ["body", *self.loc],
            "msg": f"Tables should be nested at most {self.max_nesting} deep",
            "ctx": {"max_nesting": self.max_nesting},
        }


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

# Tables stay up to this many columns. The cells of a row that became a paragraph are joined by the separator.
TABLE_MAX_COLUMNS = 3
TABLE_CELL_SEPARATOR = " · "

# Elements of texts written before the editor. ``nh3`` lets them pass without attributes, and ``map_legacy_elements``
# maps them right after.
_LEGACY_TAGS: set[str] = {"h1", "pre", "code"}

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

# Patterns of this module never scan from many start points to the end: a tag pattern stops at the next ``<``
# or ``>``, and nothing runs over raw input that has not passed ``nh3``.
_TAG_START = re.compile(r"<[a-z]", re.IGNORECASE)
_IMG_TAG = re.compile(r"<img\b[^<>]*>", re.IGNORECASE)
_TAG_OR_SPACE = re.compile(r"<[^<>]+>|&nbsp;|\s")


def media_prefix_for(org_slug: str) -> str:
    """The only ``src`` prefix an inline image may carry for this organisation."""
    return f"/api/orgs/{org_slug}/media/"


# A browser removes a tab and a newline from an address, reads a backslash as a slash and resolves ``.`` and
# ``..`` segments, also when they are percent-encoded. None of these may stand in a stored image address.
_ENCODED_PATH_CHARACTERS = ("%2e", "%2f", "%5c")


def _is_media_source(src: str, media_prefix: str) -> bool:
    """True when ``src`` is a clean address under the organisation's media route and cannot climb out of it.

    The strict form: no character below 0x20 or 0x7F, no white space, no backslash, no percent-encoded dot,
    slash or backslash, no query, no fragment, and no ``.`` or ``..`` segment of the path. Only then the
    prefix is compared. The paste cleaner and the renderer of the frontend (``isOwnMediaSrc``) refuse a
    query and a fragment too, so an address that is stored also shows.
    """
    if any(ord(char) < 0x20 or char == "\x7f" or char.isspace() for char in src) or "\\" in src:
        return False
    if "?" in src or "#" in src:
        return False
    lowered = src.lower()
    if any(escape in lowered for escape in _ENCODED_PATH_CHARACTERS):
        return False
    if any(segment in (".", "..") for segment in src.split("/")):
        return False
    return src.startswith(media_prefix) and len(src) > len(media_prefix)


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


class _ImageSourceFinder(HTMLParser):
    """Collect the ``src`` of every ``img`` element of a document.

    The parser knows where an attribute starts and ends, and it decodes the value. Text that looks like a
    ``src`` inside another quoted attribute, in a comment or in plain text is no source.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "img":
            return
        for name, value in attrs:
            if name == "src":
                # A browser takes the first of two attributes with one name.
                if value:
                    self.sources.add(value)
                return


def image_sources(html: str | None) -> set[str]:
    """The ``src`` of every ``<img>`` element in a stored document, read from the parsed elements."""
    if not html or "<" not in html:
        return set()
    finder = _ImageSourceFinder()
    finder.feed(html)
    finder.close()
    return finder.sources


# What the renderer of the frontend removes with everything inside it (``FORBID_CONTENTS`` in
# ``src/frontend/src/utils/sanitizeRichText.ts``). A test compares the two lists.
RENDERER_DROPS_WITH_CONTENT: frozenset[str] = frozenset(
    {
        "annotation-xml",
        "audio",
        "colgroup",
        "desc",
        "foreignobject",
        "head",
        "iframe",
        "math",
        "mi",
        "mn",
        "mo",
        "ms",
        "mtext",
        "noembed",
        "noframes",
        "noscript",
        "plaintext",
        "script",
        "selectedcontent",
        "style",
        "svg",
        "template",
        "title",
        "video",
        "xmp",
    }
)

# A browser of today keeps an image inside ``select``, an older one drops it while it parses. No such image
# is public, so the answer does not depend on the browser.
_NOT_SHOWN_IN_EVERY_BROWSER: frozenset[str] = frozenset({"select"})

# A legal text of an older release can be longer than the limit. Beyond this length no image of it is
# public: the answer of an open route must not cost unbounded work.
_SHOWN_IMAGES_MAX_LENGTH = 4 * RICH_TEXT_MAX_LENGTH


@lru_cache(maxsize=64)
def image_object_keys(html: str | None, media_prefix: str) -> frozenset[str]:
    """The object keys of the images that a stored document shows from ``media_prefix``.

    The media route opens an object to anyone on this answer (``routers/media.py``), and a legal text of an
    older release is stored as it came in. So the answer is not "every ``img`` in the text" but "every image
    that the renderer shows": the text is cleaned with the allowlist of the write filter, and what the
    renderer removes with its content goes with its content, for example an image inside ``template`` or
    ``video``. ``nh3`` parses like a browser, so raw text, comments and attribute values are no elements.
    Then the ``src`` of each ``img`` that is left counts when it has the strict form of ``_is_media_source``.
    The key is the part after the prefix, percent-decoded, which is the form the media route receives.

    The answer for one text is kept: the route asks again for every image of a legal page.
    """
    if not html or "<" not in html or len(html) > _SHOWN_IMAGES_MAX_LENGTH:
        return frozenset()
    shown = nh3.clean(
        html,
        tags=ALLOWED_TAGS | _LEGACY_TAGS,
        clean_content_tags=set(RENDERER_DROPS_WITH_CONTENT | _NOT_SHOWN_IN_EVERY_BROWSER),
        attributes=_NH3_ATTRIBUTES,
        tag_attribute_values=_NH3_ATTRIBUTE_VALUES,
        url_schemes=URL_SCHEMES,
        link_rel=LINK_REL,
        strip_comments=True,
    )
    return frozenset(
        unquote(src[len(media_prefix) :]) for src in image_sources(shown) if _is_media_source(src, media_prefix)
    )


def is_html(text: str) -> bool:
    """True when the text carries at least one tag, the same test ``SafeHtml`` uses.

    That is a ``<`` followed by a letter and, somewhere after it, a ``>``. Two linear searches.
    """
    start = _TAG_START.search(text)
    return start is not None and text.find(">", start.end()) != -1


# Tables outside the contract. The clean document is parsed into a small tree, the rule of
# ``richTextTables.ts`` is applied step for step, and the tree is written back. Every walk keeps its own
# stack, so a deeply nested hostile document cannot raise ``RecursionError``.

# White space is exactly these characters, as in the frontend. ``str.strip()`` without them strips more.
_TABLE_SPACE = " \t\n\r\f\u00a0"
_VOID_TAGS = {"br", "img", "hr"}
_INLINE_TAGS = {"strong", "em", "u", "s", "a", "br", "code"}
_STRUCTURE_TAGS = {"tbody", "tr", "td", "th"}


class _Node:
    __slots__ = ("parent",)

    def __init__(self) -> None:
        self.parent: _Element | None = None


class _Text(_Node):
    __slots__ = ("data",)

    def __init__(self, data: str) -> None:
        super().__init__()
        self.data = data


class _Element(_Node):
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]] | None = None) -> None:
        super().__init__()
        self.tag = tag
        self.attrs: list[tuple[str, str | None]] = attrs or []
        self.children: list[_Node] = []


def _tag(node: _Node) -> str:
    return node.tag if isinstance(node, _Element) else ""


def _detach(node: _Node) -> None:
    if node.parent is not None:
        node.parent.children.remove(node)
        node.parent = None


def _append(parent: _Element, node: _Node) -> None:
    """Append ``node`` to ``parent`` and take it out of its old parent first, like ``appendChild``."""
    _detach(node)
    node.parent = parent
    parent.children.append(node)


def _adopt(parent: _Element, node: _Node) -> None:
    """Append ``node`` to ``parent`` without a search in its old parent.

    Only for a node whose old parent is thrown away with a flattened table, or that has none. ``_append``
    searches the old list for every node, and a cell with many nodes would cost the square of their number.
    """
    node.parent = parent
    parent.children.append(node)


class _TreeBuilder(HTMLParser):
    """Parse a clean document into a tree. Text comes decoded, so the serializer escapes it again."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("")
        self._stack: list[_Element] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        element = _Element(tag, attrs)
        _append(self._stack[-1], element)
        if tag not in _VOID_TAGS:
            self._stack.append(element)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        parent = self._stack[-1]
        last = parent.children[-1] if parent.children else None
        if isinstance(last, _Text):
            last.data += data
        else:
            _append(parent, _Text(data))


def _escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("\u00a0", "&nbsp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attribute(value: str) -> str:
    # The installed ``nh3`` writes ``<`` and ``>`` in a value as entities too, like in text.
    return _escape_text(value).replace('"', "&quot;")


def _serialize(root: _Element) -> str:
    """Write the tree the way ``nh3`` writes a document, so the parts outside the tables do not change."""
    parts: list[str] = []
    stack: list[_Node | str] = list(reversed(root.children))
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, _Text):
            parts.append(_escape_text(item.data))
        else:
            assert isinstance(item, _Element)
            parts.append(f"<{item.tag}")
            for name, value in item.attrs:
                parts.append(f' {name}="{_escape_attribute(value or "")}"')
            parts.append(">")
            if item.tag not in _VOID_TAGS:
                stack.append(f"</{item.tag}>")
                stack.extend(reversed(item.children))
    return "".join(parts)


def _has_text(node: _Node) -> bool:
    """True when the text of ``node`` holds more than white space."""
    stack: list[_Node] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, _Text):
            if current.data.strip(_TABLE_SPACE):
                return True
        elif isinstance(current, _Element):
            stack.extend(current.children)
    return False


def _cells_of(row: _Element) -> list[_Element]:
    return [child for child in row.children if isinstance(child, _Element) and child.tag in ("td", "th")]


def _own_rows(table: _Element) -> list[_Element]:
    """The ``tr`` children of the table and of its ``tbody``, in document order."""
    rows: list[_Element] = []
    for child in table.children:
        if _tag(child) == "tr":
            assert isinstance(child, _Element)
            rows.append(child)
        elif _tag(child) == "tbody":
            assert isinstance(child, _Element)
            rows.extend(row for row in child.children if isinstance(row, _Element) and row.tag == "tr")
    return rows


def _loose_runs(table: _Element) -> list[list[_Node]]:
    """Child nodes of the table, of its ``tbody`` and of a ``tr`` that are not ``tbody``, ``tr``, ``td`` or ``th``.

    Adjacent loose nodes of one parent are one run. Runs come in document order.
    """
    runs: list[list[_Node]] = []
    stack: list[tuple[Iterator[_Node], list[list[_Node]]]] = [(iter(list(table.children)), [[]])]
    while stack:
        children, current = stack[-1]
        descended = False
        for child in children:
            if _tag(child) not in _STRUCTURE_TAGS:
                current[0].append(child)
                continue
            if current[0]:
                runs.append(current[0])
                current[0] = []
            if _tag(child) in ("tbody", "tr"):
                assert isinstance(child, _Element)
                stack.append((iter(list(child.children)), [[]]))
                descended = True
                break
        if descended:
            continue
        if current[0]:
            runs.append(current[0])
        stack.pop()
    return runs


def _trim_edge(nodes: list[_Node], at_start: bool) -> None:
    """Remove white space and ``br`` from one edge of a run of nodes, going into elements at the edge.

    The nodes of a run still stand in the list of a cell or of a block that the flattened table throws
    away, so a removed node only leaves ``nodes``. An element at the edge loses its own children the same
    way, and it goes when none is left. Every list is cut once at the end, by the count of nodes that go:
    a removal from the front for each node would move the rest of the list each time.
    """
    lists: list[list[_Node]] = [nodes]
    owners: list[_Element | None] = [None]
    gone: list[int] = [0]

    while True:
        work, count = lists[-1], gone[-1]
        if count == len(work):
            owner = owners[-1]
            if owner is None:
                break
            # Nothing of this element is left, so it goes too.
            lists.pop()
            owners.pop()
            gone.pop()
            work.clear()
            owner.parent = None
            gone[-1] += 1
            continue
        node = work[count] if at_start else work[len(work) - 1 - count]
        if isinstance(node, _Text):
            node.data = node.data.lstrip(_TABLE_SPACE) if at_start else node.data.rstrip(_TABLE_SPACE)
            if node.data:
                break
        elif isinstance(node, _Element) and node.tag != "br":
            lists.append(node.children)
            owners.append(node)
            gone.append(0)
            continue
        node.parent = None
        gone[-1] += 1

    for work, count in zip(lists, gone, strict=True):
        if count:
            if at_start:
                del work[:count]
            else:
                del work[len(work) - count :]


def _take_images(parent: _Element, images: list[_Node]) -> tuple[bool, bool]:
    """Collect every ``img`` and ``figure`` below ``parent``, also deep inside inline elements.

    An inline element that held an image and holds no other text than white space goes away with it, so no
    empty link is left. The text of a figure, its caption, counts as text of the elements around it, as it
    does in the frontend. An image does not leave its place here: ``_flattened`` takes it out at the end.
    Returns whether it found an image, and whether ``parent`` holds such text.

    One walk: every element learns from its children whether it holds text, and its list of children is
    built once. A scan of the whole subtree for each element would cost the depth times the size.
    """
    # One frame for each open element: the element, the index of its next child, the children it keeps,
    # whether an image was found below it, whether text was found below it.
    frames: list[tuple[_Element, list[int], list[_Node], list[bool]]] = [(parent, [0], [], [False, False])]
    while True:
        element, position, kept, found = frames[-1]
        if position[0] < len(element.children):
            child = element.children[position[0]]
            position[0] += 1
            if _tag(child) in ("img", "figure"):
                # The image stays where it is until the table is replaced, as in the frontend: the
                # trimming of a line still sees it.
                images.append(child)
                kept.append(child)
                found[0] = True
                if _has_text(child):
                    found[1] = True
            elif isinstance(child, _Element):
                frames.append((child, [0], [], [False, False]))
            else:
                kept.append(child)
                if isinstance(child, _Text) and child.data.strip(_TABLE_SPACE):
                    found[1] = True
            continue
        frames.pop()
        element.children = kept
        took, has_text = found
        if not frames:
            return took, has_text
        above = frames[-1]
        if took:
            above[3][0] = True
            if not has_text:
                element.parent = None
                continue
        above[2].append(element)
        if has_text:
            above[3][1] = True


def _lines_of(nodes: list[_Node], images: list[_Node]) -> list[list[_Node]]:
    """The lines of a run of nodes: the content of a cell, or loose content of a table.

    Images and figures leave into ``images``. A block ends the line before and after its own lines, ``hr``
    only ends the line. Unknown elements, ``h1`` and ``pre`` count as blocks, ``code`` as inline. A line
    without text other than white space is left out: each line learns that while it is built.
    """
    lines: list[list[_Node]] = []
    current: list[_Node] = []
    current_has_text = False
    # A ``None`` on the stack ends the line, it closes a block.
    stack: list[_Node | None] = list(reversed(nodes))
    while stack:
        child = stack.pop()
        ends_line = child is None
        if isinstance(child, _Text):
            current.append(child)
            if child.data.strip(_TABLE_SPACE):
                current_has_text = True
        elif isinstance(child, _Element):
            if child.tag in ("img", "figure"):
                images.append(child)
            elif child.tag in _INLINE_TAGS:
                took, has_text = _take_images(child, images)
                if took and not has_text:
                    continue
                current.append(child)
                if has_text:
                    current_has_text = True
            elif child.tag == "hr":
                ends_line = True
            else:
                ends_line = True
                stack.append(None)
                stack.extend(reversed(child.children))
        if ends_line:
            if current and current_has_text:
                lines.append(current)
            current = []
            current_has_text = False
    if current and current_has_text:
        lines.append(current)
    for line in lines:
        _trim_edge(line, True)
        _trim_edge(line, False)
    return lines


def _join_lines(lines: list[list[_Node]]) -> list[_Node]:
    content: list[_Node] = []
    for index, line in enumerate(lines):
        if index > 0:
            content.append(_Element("br"))
        content.extend(line)
    return content


def _flattened(table: _Element, inside_cell: bool) -> list[_Node] | None:
    """The paragraphs and images that replace a table outside the contract, or ``None`` for a table that stays."""
    rows = _own_rows(table)
    columns = max((len(_cells_of(row)) for row in rows), default=0)
    if columns <= TABLE_MAX_COLUMNS and not inside_cell:
        return None

    out: list[_Node] = []
    loose_images: list[_Node] = []
    for run in _loose_runs(table):
        lines = _lines_of(run, loose_images)
        if not lines:
            continue
        paragraph = _Element("p")
        for node in _join_lines(lines):
            _adopt(paragraph, node)
        out.append(paragraph)
    out.extend(loose_images)
    for row in rows:
        cells = _cells_of(row)
        images: list[_Node] = []
        contents: list[list[_Node]] = []
        for cell in cells:
            lines = _lines_of(list(cell.children), images)
            if lines:
                contents.append(_join_lines(lines))
        if contents:
            paragraph = _Element("p")
            holder = paragraph
            if all(cell.tag == "th" for cell in cells):
                holder = _Element("strong")
                _adopt(paragraph, holder)
            for index, content in enumerate(contents):
                if index > 0:
                    _adopt(holder, _Text(TABLE_CELL_SEPARATOR))
                for node in content:
                    _adopt(holder, node)
            out.append(paragraph)
        out.extend(images)
    # An image deep inside an inline element still stands in the list of that element. Each such list is
    # built once without the images that left it: a search for each image would cost the square.
    moved = [node for node in out if node.parent is not None]
    if moved:
        gone = set(moved)
        for parent in {node.parent for node in moved if node.parent is not None}:
            parent.children = [child for child in parent.children if child not in gone]
        for node in moved:
            node.parent = None
    return out


def _flatten_tables_outside_contract(html: str) -> str:
    """Turn every table with more than ``TABLE_MAX_COLUMNS`` columns, or inside a cell, into paragraphs.

    One paragraph for each run of loose content of the table, then one for each row: the cells joined by
    ``TABLE_CELL_SEPARATOR``, the blocks of a cell as lines joined by ``br``, empty cells left out, a row of
    header cells in ``strong``. Images and figures of a row follow its paragraph. Tables are done from the
    innermost out. ``richTextTableCases.json`` pins this function and the frontend renderer to one output.
    A document without such a table comes back as it is.

    One walk over the tree. An element is done after everything below it, so an inner table is paragraphs
    before the table around it is read. The walk carries whether a cell stands above an element, and the
    list of children of an element is built once, with the paragraphs of its flattened tables in place.

    A table around another reads the content of the inner one again. So tables nested deeper than
    ``TABLE_MAX_NESTING`` raise ``RichTextTooNestedError``, and the work stays linear in the size.
    """
    if "<table" not in html:
        return html
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    replacements: dict[_Element, list[_Node]] = {}
    with_replaced_child: set[_Element] = set()
    # (element, a cell stands above it, the count of tables above it, everything below it is done)
    stack: list[tuple[_Element, bool, int, bool]] = [(builder.root, False, 0, False)]
    while stack:
        element, inside_cell, tables_above, below_done = stack.pop()
        if not below_done:
            if element.tag == "table":
                if tables_above >= TABLE_MAX_NESTING:
                    raise RichTextTooNestedError
                tables_above += 1
            stack.append((element, inside_cell, tables_above, True))
            child_inside_cell = inside_cell or element.tag in ("td", "th")
            stack.extend(
                (child, child_inside_cell, tables_above, False)
                for child in reversed(element.children)
                if isinstance(child, _Element)
            )
            continue
        if element in with_replaced_child:
            children: list[_Node] = []
            for child in element.children:
                replacement = replacements.get(child) if isinstance(child, _Element) else None
                if replacement is None:
                    children.append(child)
                    continue
                child.parent = None
                for node in replacement:
                    node.parent = element
                children.extend(replacement)
            element.children = children
        if element.tag == "table" and element.parent is not None:
            flattened = _flattened(element, inside_cell)
            if flattened is not None:
                replacements[element] = flattened
                with_replaced_child.add(element.parent)
    return _serialize(builder.root) if replacements else html


# Texts from before the editor. The clean document is parsed into the same tree as for the tables.


def _pre_text(pre: _Element) -> str:
    """The text of a ``pre`` block: its text nodes in order, a ``br`` as a line break, other elements dropped."""
    parts: list[str] = []
    stack: list[_Node] = list(reversed(pre.children))
    while stack:
        node = stack.pop()
        if isinstance(node, _Text):
            parts.append(node.data)
        elif isinstance(node, _Element):
            if node.tag == "br":
                parts.append("\n")
            else:
                stack.extend(reversed(node.children))
    return "".join(parts).replace("\r\n", "\n").replace("\r", "\n")


def _pre_paragraphs(pre: _Element) -> list[_Node]:
    """A ``pre`` block as paragraphs: a blank line starts a new one, a line break inside a part is ``br``.

    A blank line is a line of spaces and tabs between two line breaks. A part of only white space gives no
    paragraph, and line breaks at its start and end go.
    """
    lines = _pre_text(pre).split("\n")
    parts: list[list[str]] = [[]]
    for index, line in enumerate(lines):
        if 0 < index < len(lines) - 1 and not line.strip(" \t"):
            parts.append([])
        else:
            parts[-1].append(line)
    paragraphs: list[_Node] = []
    for part in parts:
        start, end = 0, len(part)
        while start < end and not part[start]:
            start += 1
        while end > start and not part[end - 1]:
            end -= 1
        part = part[start:end]
        if not any(line.strip() for line in part):
            continue
        paragraph = _Element("p")
        for index, line in enumerate(part):
            if index > 0:
                _append(paragraph, _Element("br"))
            if line:
                _append(paragraph, _Text(line))
        paragraphs.append(paragraph)
    return paragraphs


def map_legacy_elements(html: str) -> str:
    """Map ``h1``, ``pre`` and ``code`` of an old document to ``h2``, paragraphs and plain text.

    ``html`` is the output of ``nh3``. A document without the three elements comes back as it is.
    """
    if not any(f"<{tag}" in html for tag in _LEGACY_TAGS):
        return html
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    todo: list[_Element] = [builder.root]
    while todo:
        element = todo.pop()
        work: list[_Node] = list(reversed(element.children))
        children: list[_Node] = []
        while work:
            child = work.pop()
            if isinstance(child, _Element):
                if child.tag == "code":
                    work.extend(reversed(child.children))
                    continue
                if child.tag == "pre":
                    children.extend(_pre_paragraphs(child))
                    continue
                if child.tag == "h1":
                    child.tag = "h2"
                todo.append(child)
            children.append(child)
        for child in children:
            child.parent = element
        element.children = children
    return _serialize(builder.root)


def sanitize_html(
    html: str,
    media_prefix: str,
    *,
    max_length: int = RICH_TEXT_MAX_LENGTH,
    loc: tuple[str, ...] = (),
) -> str:
    """Return ``html`` reduced to the editor's allowlist, with images only under ``media_prefix``.

    A text longer than ``RICH_TEXT_MAX_LENGTH`` raises ``RichTextTooLongError`` before any other work, so the
    filter never works on more. The cleaned text is what the write stores, and it can be longer than the
    text that came in. So it is measured again, against ``max_length`` of the field, and a text that is too
    long is refused, never cut. Tables nested deeper than ``TABLE_MAX_NESTING`` raise
    ``RichTextTooNestedError``. ``loc`` names the field in both errors.
    """
    if len(html) > RICH_TEXT_MAX_LENGTH:
        raise RichTextTooLongError(RICH_TEXT_MAX_LENGTH, loc)
    if not html or not is_html(html):
        if len(html) > max_length:
            raise RichTextTooLongError(max_length, loc)
        return html
    cleaned = nh3.clean(
        html,
        tags=ALLOWED_TAGS | _LEGACY_TAGS,
        attributes=_NH3_ATTRIBUTES,
        tag_attribute_values=_NH3_ATTRIBUTE_VALUES,
        url_schemes=URL_SCHEMES,
        link_rel=LINK_REL,
        strip_comments=True,
    )
    mapped = map_legacy_elements(cleaned)
    try:
        result = _flatten_tables_outside_contract(_strip_foreign_images(mapped, media_prefix))
    except RichTextTooNestedError:
        raise RichTextTooNestedError(loc) from None
    if len(result) > max_length:
        raise RichTextTooLongError(max_length, loc, cleaned=True)
    return result


def sanitize_optional_html(
    value: str | None,
    media_prefix: str,
    *,
    max_length: int = RICH_TEXT_MAX_LENGTH,
    loc: tuple[str, ...] = (),
) -> str | None:
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
    cleaned = sanitize_html(value, media_prefix, max_length=max_length, loc=loc).strip()
    if not cleaned:
        return None
    if is_html(cleaned) and not _IMG_TAG.search(cleaned) and not _TAG_OR_SPACE.sub("", cleaned):
        return None
    return cleaned


def sanitize_multilingual(
    value: Mapping[str, str] | None,
    media_prefix: str,
    *,
    max_length: int = RICH_TEXT_MAX_LENGTH,
    loc: tuple[str, ...] = (),
) -> dict[str, str] | None:
    """Sanitise every language of a multilingual text. ``None`` stays ``None``.

    The error of a language that is too long names the field and the language: ``loc`` plus the language.
    """
    if value is None:
        return None
    return {
        locale: sanitize_html(text, media_prefix, max_length=max_length, loc=(*loc, locale))
        if isinstance(text, str)
        else text
        for locale, text in value.items()
    }


def sanitize_station_tips(tips: dict[str, Any] | None, org_slug: str) -> dict[str, Any] | None:
    """
    Sanitise the rich-text header of a station's tips block and leave the rest untouched.

    Both routes that create a station use it, in the library and inside a rallye. The header keeps its own
    limit, ``TIPS_HEADER_MAX_LENGTH``, also for the cleaned text.
    """
    if not tips:
        return tips
    header = tips.get("header")
    if isinstance(header, dict):
        tips = {
            **tips,
            "header": sanitize_multilingual(
                header, media_prefix_for(org_slug), max_length=TIPS_HEADER_MAX_LENGTH, loc=("tips", "header")
            ),
        }
    return tips
