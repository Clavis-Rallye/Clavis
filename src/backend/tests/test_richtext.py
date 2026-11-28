"""Server-side sanitising of rich text."""

import json
import re
import time
from pathlib import Path
from typing import Any

import nh3
import pytest

from app.services.richtext import (
    _NH3_ATTRIBUTE_VALUES,
    _NH3_ATTRIBUTES,
    ALLOWED_ATTRIBUTE_VALUES,
    ALLOWED_ATTRIBUTES,
    ALLOWED_TAGS,
    LINK_REL,
    REFERENCE_ANSWER_MAX_LENGTH,
    RICH_TEXT_MAX_LENGTH,
    TABLE_CELL_SEPARATOR,
    TABLE_MAX_COLUMNS,
    TIPS_HEADER_MAX_LENGTH,
    URL_SCHEMES,
    RichTextTooLongError,
    _flatten_tables_outside_contract,
    _is_media_source,
    image_object_keys,
    image_sources,
    is_html,
    map_legacy_elements,
    media_prefix_for,
    sanitize_html,
    sanitize_multilingual,
    sanitize_optional_html,
    sanitize_station_tips,
)

PREFIX = media_prefix_for("stadt-augsburg")


def test_plain_text_passes_through_unchanged() -> None:
    text = "Erste Zeile\nZweite Zeile & mehr"
    assert not is_html(text)
    assert sanitize_html(text, PREFIX) == text


def test_allowed_markup_is_preserved() -> None:
    html = (
        "<h2>Rathaus</h2><p>Der <strong>Perlach</strong> steht <em>daneben</em>, "
        "<u>unterstrichen</u>, <s>alt</s>.</p><ul><li>eins</li><li>zwei</li></ul>"
        '<ol><li>erstens</li></ol><p>Mehr auf <a href="https://augsburg.de">der Seite</a>.</p>'
    )
    cleaned = sanitize_html(html, PREFIX)
    for fragment in ("<h2>", "<strong>", "<em>", "<u>", "<s>", "<ul>", "<ol>", "<li>", 'href="https://augsburg.de"'):
        assert fragment in cleaned
    assert 'rel="noopener noreferrer"' in cleaned


def test_script_iframe_and_handlers_are_stripped() -> None:
    html = (
        '<p onclick="steal()">Text</p><script>alert(1)</script>'
        '<iframe src="https://evil.example"></iframe><p style="color:red">Rot</p>'
        '<a href="javascript:alert(1)">klick</a>'
    )
    cleaned = sanitize_html(html, PREFIX)
    assert "<script" not in cleaned
    assert "<iframe" not in cleaned
    assert "onclick" not in cleaned
    assert "style=" not in cleaned
    assert "javascript:" not in cleaned
    assert "Text" in cleaned and "Rot" in cleaned


def test_own_media_image_is_kept_and_foreign_image_is_dropped() -> None:
    own = f'<img src="{PREFIX}abc_bild.png" alt="Das Rathaus">'
    foreign = '<img src="https://tracker.example/pixel.gif" alt="">'
    relative_escape = f'<img src="{PREFIX}../../secret.png" alt="x">'
    other_org = '<img src="/api/orgs/andere-org/media/abc.png" alt="x">'
    cleaned = sanitize_html(f"<p>{own}{foreign}{relative_escape}{other_org}</p>", PREFIX)
    assert f'src="{PREFIX}abc_bild.png"' in cleaned
    assert 'alt="Das Rathaus"' in cleaned
    assert "tracker.example" not in cleaned
    assert "secret.png" not in cleaned
    assert "andere-org" not in cleaned


def test_h1_is_demoted_to_allowed_levels() -> None:
    cleaned = sanitize_html("<h1>Zu gross</h1><h4>Zu klein</h4>", PREFIX)
    assert "<h1>" not in cleaned and "<h4>" not in cleaned
    assert "Zu gross" in cleaned and "Zu klein" in cleaned


def test_multilingual_sanitises_every_locale_and_keeps_none() -> None:
    assert sanitize_multilingual(None, PREFIX) is None
    cleaned = sanitize_multilingual({"de": "<p>Hallo<script>x</script></p>", "en": "Plain"}, PREFIX)
    assert cleaned == {"de": "<p>Hallo</p>", "en": "Plain"}


# The reference answer of a challenge is one-language rich text (the editor works in all editorial fields).


def test_optional_html_sanitises_the_reference_answer() -> None:
    cleaned = sanitize_optional_html(
        '<p><strong>1620</strong></p><script>alert(1)</script><img src="https://tracker.example/p.gif">',
        PREFIX,
    )
    assert cleaned == "<p><strong>1620</strong></p>"


def test_optional_html_keeps_none_plain_text_and_own_images() -> None:
    own = f'<p><img src="{PREFIX}0000/media/abc_saal.png" alt="Der Saal"></p>'
    assert sanitize_optional_html(None, PREFIX) is None
    assert sanitize_optional_html("1620, Elias Holl", PREFIX) == "1620, Elias Holl"
    assert sanitize_optional_html(own, PREFIX) == own


def test_optional_html_turns_an_emptied_editor_into_none() -> None:
    for empty in ("", "   ", "<p></p>", "<p> </p>", "<p><br></p>", "<script>x</script>"):
        assert sanitize_optional_html(empty, PREFIX) is None, empty


def _load_corpus_091() -> dict[str, Any]:
    path = Path(__file__).parent / "richtext_091_corpus.json"
    corpus: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return corpus


CORPUS_091 = _load_corpus_091()


# Release 0.9.1 removed the blockquote tag and kept its text, and it removed ``h1``, ``pre`` and
# ``code`` the same way, which left text outside any block. The editor now has quotes, and the
# filter maps ``h1`` to ``h2``, a ``pre`` block to paragraphs and ``code`` to plain text before it
# cleans. The output of the key below changes by design; every other key stays as recorded.
CHANGED_BY_THE_NEW_EDITOR: dict[str, str] = {
    # Same words as the 0.9.1 output, but in blocks: the title is an h2, the preformatted lines are
    # one paragraph with a line break, the code is plain text, and the quote stays a blockquote.
    "legacyServerUnfiltered": (
        "<h2>Alter Titel</h2><p>Zeile eins<br>Zeile zwei</p>"
        "<p>Ein Aufruf wie ls -l im Text.</p><blockquote><p>Ein Zitat.</p></blockquote>"
    ),
}


@pytest.mark.parametrize("key", list(CORPUS_091["documents"]))
def test_release_091_corpus_is_sanitised_as_recorded(key: str) -> None:
    document = CORPUS_091["documents"][key]
    expected = CHANGED_BY_THE_NEW_EDITOR.get(key, document["sanitized"])
    assert sanitize_html(document["input"], CORPUS_091["media_prefix"]) == expected


def _visible_text(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html.replace("<br>", "\n"))


def test_changed_pins_keep_every_word_of_the_recording() -> None:
    for key, expected in CHANGED_BY_THE_NEW_EDITOR.items():
        recorded = CORPUS_091["documents"][key]["sanitized"]
        assert expected != recorded
        assert _visible_text(expected) == _visible_text(recorded)


def test_release_091_corpus_is_complete() -> None:
    keys = set(CORPUS_091["documents"])
    assert len(keys) >= 10
    assert {"emptyString", "legacyPlainText", "legacyServerUnfiltered"} <= keys
    assert CORPUS_091["media_prefix"] == "/api/orgs/stadt/media/"


# The contract file: one list of what the editor produces, shared with the frontend renderer.

CONTRACT = json.loads(
    (Path(__file__).parent / ".." / ".." / "frontend" / "src" / "utils" / "richTextContract.json").read_text(
        encoding="utf-8"
    )
)


def test_contract_file_equals_the_module_constants() -> None:
    elements = {tag: set(names) for tag, names in CONTRACT["elements"].items()}
    assert set(elements) == ALLOWED_TAGS
    # ``nh3`` sets ``rel`` on anchors itself through ``link_rel`` and refuses it in ``attributes``,
    # so the contract's ``rel`` on ``a`` counts as kept by ``link_rel``.
    expected = {tag: names | ({"rel"} if tag == "a" else set()) for tag, names in ALLOWED_ATTRIBUTES.items()}
    for tag in ALLOWED_TAGS:
        assert elements[tag] == expected.get(tag, set()), tag
    assert {name: set(values) for name, values in CONTRACT["attributeValues"].items()} == ALLOWED_ATTRIBUTE_VALUES
    assert set(CONTRACT["urlSchemes"]) == URL_SCHEMES
    for names in (*CONTRACT["elements"].values(), *CONTRACT["attributeValues"].values(), CONTRACT["urlSchemes"]):
        assert names == sorted(names)


HOSTILE_CASES: list[tuple[str, str, str]] = [
    ("script", "<p>a</p><script>alert(1)</script>", "<script"),
    ("style element", "<style>p{color:red}</style><p>a</p>", "<style"),
    ("style attribute", '<p style="color:red">a</p>', "style="),
    ("onerror", f'<img src="{PREFIX}a.png" onerror="alert(1)">', "onerror"),
    ("onclick", '<p onclick="alert(1)">a</p>', "onclick"),
    ("onload", '<table onload="alert(1)"><tbody><tr><td>a</td></tr></tbody></table>', "onload"),
    ("iframe", '<iframe src="https://evil.example"></iframe>', "<iframe"),
    ("object", '<object data="x.swf"></object>', "<object"),
    ("embed", '<embed src="x.swf">', "<embed"),
    ("svg", '<svg onload="alert(1)"><circle></circle></svg>', "<svg"),
    ("math", '<math><mi xlink:href="javascript:alert(1)">x</mi></math>', "<math"),
    ("form", '<form action="https://evil.example"><button>go</button></form>', "<form"),
    ("input", '<p>a</p><input name="x" value="y">', "<input"),
    ("template", "<template><p>a</p></template>", "<template"),
    ("class", '<p class="x">a</p>', "class="),
    ("id", '<p id="x">a</p>', "id="),
    ("name", '<a name="x" href="/a">a</a>', "name="),
    ("unknown data attribute", '<p data-evil="1">a</p><img src="' + PREFIX + 'a.png" data-x="1">', "data-evil"),
    ("unknown data attribute on a figure", '<figure data-x="1"><img src="' + PREFIX + 'a.png"></figure>', "data-x"),
    ("javascript url", '<a href="javascript:alert(1)">a</a>', "javascript:"),
    ("javascript url with a line break", '<a href="java&#10;script:alert(1)">a</a>', "script:"),
    ("data url", '<a href="data:text/html,<script>alert(1)</script>">a</a>', "data:"),
    ("vbscript url", '<a href="vbscript:msgbox(1)">a</a>', "vbscript:"),
    ("data url as image source", '<img src="data:image/png;base64,AAAA">', "data:"),
    ("colspan", "<table><tbody><tr><td colspan=2>a</td></tr></tbody></table>", "colspan"),
    ("rowspan", "<table><tbody><tr><td rowspan=2>a</td></tr></tbody></table>", "rowspan"),
    ("colwidth", '<table><tbody><tr><th colwidth="100">a</th></tr></tbody></table>', "colwidth"),
    ("cell align", '<table><tbody><tr><td align="center">a</td></tr></tbody></table>', "align="),
]


@pytest.mark.parametrize(("label", "html", "forbidden"), HOSTILE_CASES, ids=[case[0] for case in HOSTILE_CASES])
def test_hostile_markup_is_not_kept(label: str, html: str, forbidden: str) -> None:
    assert forbidden not in sanitize_html(html, PREFIX).lower(), label


@pytest.mark.parametrize("tag", ["img", "figure"])
@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("data-width", "50;background:url(x)"),
        ("data-width", "33"),
        ("data-width", " 50"),
        ("data-align", "javascript:alert(1)"),
        ("data-align", "justify"),
        ("data-align", "LEFT"),
    ],
)
def test_a_bad_value_removes_the_attribute_and_keeps_the_element(tag: str, attribute: str, value: str) -> None:
    image = f'<img src="{PREFIX}a.png"{f' {attribute}="{value}"' if tag == "img" else ""}>'
    html = image if tag == "img" else f'<figure {attribute}="{value}">{image}<figcaption>c</figcaption></figure>'
    cleaned = sanitize_html(html, PREFIX)
    assert attribute not in cleaned
    assert f"<{tag}" in cleaned
    assert f'src="{PREFIX}a.png"' in cleaned


def test_size_and_position_are_kept_on_the_image_and_on_the_figure() -> None:
    bare = f'<img src="{PREFIX}a.png" alt="x" data-width="25" data-align="left">'
    figure = f'<figure data-width="100" data-align="center"><img src="{PREFIX}a.png" alt="x"><figcaption>c</figcaption></figure>'
    assert sanitize_html(bare, PREFIX) == bare
    assert sanitize_html(figure, PREFIX) == figure
    for width in ("25", "50", "75", "100"):
        assert f'data-width="{width}"' in sanitize_html(f'<img src="{PREFIX}a.png" data-width="{width}">', PREFIX)
    for align in ("left", "center", "right"):
        assert f'data-align="{align}"' in sanitize_html(f'<img src="{PREFIX}a.png" data-align="{align}">', PREFIX)


FOREIGN = "https://tracker.example/pixel.gif"


def test_a_foreign_image_is_removed_also_inside_a_figure_and_a_table_cell() -> None:
    assert sanitize_html(f'<p>a<img src="{FOREIGN}">b</p>', PREFIX) == "<p>ab</p>"
    in_cell = f'<table><tbody><tr><td><img src="{FOREIGN}">x</td></tr></tbody></table>'
    assert sanitize_html(in_cell, PREFIX) == "<table><tbody><tr><td>x</td></tr></tbody></table>"
    in_figure = (
        f'<p>a</p><figure data-width="50"><img src="{FOREIGN}"><figcaption>Bildtext</figcaption></figure><p>b</p>'
    )
    assert sanitize_html(in_figure, PREFIX) == "<p>a</p><p>b</p>"


def test_a_figure_without_an_image_is_removed_with_its_caption() -> None:
    assert sanitize_html("<p>a</p><figure><figcaption>Bildtext</figcaption></figure>", PREFIX) == "<p>a</p>"
    assert sanitize_html("<figure></figure><p>a</p>", PREFIX) == "<p>a</p>"
    outside = f'<figure><img src="/api/orgs/andere-org/media/a.png"><figcaption>x</figcaption></figure><figure><img src="{PREFIX}b.png"><figcaption>y</figcaption></figure>'
    assert sanitize_html(outside, PREFIX) == f'<figure><img src="{PREFIX}b.png"><figcaption>y</figcaption></figure>'


def test_a_figure_inside_a_figure_cannot_borrow_the_image_of_the_other() -> None:
    html = f'<figure><figure><figcaption>x</figcaption></figure><img src="{PREFIX}a.png"></figure>'
    cleaned = sanitize_html(html, PREFIX)
    assert cleaned == f'<figure><img src="{PREFIX}a.png"></figure>'


def test_an_image_with_a_caption_keeps_its_figure() -> None:
    html = f'<figure data-width="50" data-align="right"><img src="{PREFIX}a.png" alt="Rathaus"><figcaption>Das <strong>Rathaus</strong></figcaption></figure>'
    assert sanitize_html(html, PREFIX) == html


def test_a_hostile_alt_text_cannot_hide_a_foreign_source() -> None:
    html = f'<img alt="x>y" src="{FOREIGN}"><img alt="a>b" src="{PREFIX}ok.png">'
    cleaned = sanitize_html(html, PREFIX)
    assert FOREIGN not in cleaned
    assert image_sources(cleaned) == {f"{PREFIX}ok.png"}


@pytest.mark.parametrize(
    "src",
    [
        f"{PREFIX}%2e%2e/secret.png",
        f"{PREFIX}%2E%2E/secret.png",
        f"{PREFIX}&#46;&#46;/secret.png",
        f"{PREFIX}..\\secret.png",
        "//tracker.example/pixel.gif",
    ],
)
def test_a_media_source_cannot_climb_out_of_the_route(src: str) -> None:
    assert "<img" not in sanitize_html(f'<img src="{src}">', PREFIX)


def test_quote_and_divider_are_kept() -> None:
    html = "<blockquote><p>Ein Zitat.</p></blockquote><hr><p>Danach</p>"
    assert sanitize_html(html, PREFIX) == html


def test_a_table_of_the_contract_is_kept_as_is() -> None:
    html = (
        "<table><tbody><tr><th>Ort</th><th>Jahr</th></tr>"
        "<tr><td>Rathaus</td><td><strong>1620</strong></td></tr></tbody></table>"
    )
    assert sanitize_html(html, PREFIX) == html


def test_table_attributes_outside_the_contract_are_removed_and_the_cells_stay() -> None:
    html = (
        '<table class="x"><tbody><tr><td colspan="2" rowspan="2" style="x" align="center">a</td></tr></tbody></table>'
    )
    assert sanitize_html(html, PREFIX) == "<table><tbody><tr><td>a</td></tr></tbody></table>"


# Old legal texts: ``h1``, ``pre`` and ``code`` become what the editor has. The frontend does the same
# in ``toEditorHtml``; the two literals below are the same pair as in ``richtext.test.ts``.

LEGACY_LEGAL_DOCUMENT = (
    "<h1>Impressum</h1><pre><code>Musterstraße 1\n86150 Augsburg\n\nTelefon 0821 0000</code></pre>"
    "<p>Ein Aufruf wie <code>ls -l</code> im Text.</p><blockquote><p>Ein Zitat.</p></blockquote>"
)
LEGACY_LEGAL_MAPPED = (
    "<h2>Impressum</h2><p>Musterstraße 1<br>86150 Augsburg</p><p>Telefon 0821 0000</p>"
    "<p>Ein Aufruf wie ls -l im Text.</p><blockquote><p>Ein Zitat.</p></blockquote>"
)


def test_legacy_legal_document_is_mapped_to_the_editor_blocks() -> None:
    assert sanitize_html(LEGACY_LEGAL_DOCUMENT, PREFIX) == LEGACY_LEGAL_MAPPED


def test_h1_keeps_its_inline_markup_as_h2() -> None:
    assert sanitize_html("<h1>Neu <em>hier</em></h1>", PREFIX) == "<h2>Neu <em>hier</em></h2>"


def test_pre_keeps_only_its_text() -> None:
    assert sanitize_html("<pre>Eins <strong>zwei</strong>\ndrei</pre>", PREFIX) == "<p>Eins zwei<br>drei</p>"


def test_pre_keeps_special_characters_as_escaped_text() -> None:
    assert sanitize_html("<pre>a &lt; b &amp;&amp; c &gt; d</pre>", PREFIX) == "<p>a &lt; b &amp;&amp; c &gt; d</p>"
    assert sanitize_html("<pre>&lt;script&gt;alert(1)&lt;/script&gt;</pre>", PREFIX) == (
        "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"
    )


def test_pre_blank_lines_with_spaces_start_a_new_paragraph() -> None:
    assert sanitize_html("<pre>Eins\n  \nZwei\r\nDrei</pre>", PREFIX) == "<p>Eins</p><p>Zwei<br>Drei</p>"


def test_empty_pre_leaves_no_block() -> None:
    assert sanitize_html("<p>Davor</p><pre>\n</pre><p>Danach</p>", PREFIX) == "<p>Davor</p><p>Danach</p>"


def test_code_is_plain_text() -> None:
    assert sanitize_html("<p>Nutze <code>ls -l</code> hier.</p>", PREFIX) == "<p>Nutze ls -l hier.</p>"


def test_document_without_the_three_tags_is_not_changed_by_the_mapping() -> None:
    document = "<h2>Titel</h2><p>Text<br>Zeile</p><blockquote><p>Zitat</p></blockquote>"
    assert sanitize_html(document, PREFIX) == document


# Tables outside the contract: more than three columns, or a table inside a cell. The frontend renderer
# follows the same rule and the same fixture, ``richTextTableCases.json``.

TABLE_CASES: list[dict[str, str]] = json.loads(
    (Path(__file__).parent / ".." / ".." / "frontend" / "src" / "utils" / "richTextTableCases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


def test_table_constants_equal_the_contract_file() -> None:
    assert CONTRACT["tables"]["maxColumns"] == TABLE_MAX_COLUMNS
    assert CONTRACT["tables"]["cellSeparator"] == TABLE_CELL_SEPARATOR


def test_the_length_limit_follows_the_contract_file() -> None:
    assert CONTRACT["text"]["maxLength"] == RICH_TEXT_MAX_LENGTH


def test_table_fixture_holds_every_case() -> None:
    assert len(TABLE_CASES) >= 20
    assert len({case["name"] for case in TABLE_CASES}) == len(TABLE_CASES)


@pytest.mark.parametrize("case", TABLE_CASES, ids=[case["name"] for case in TABLE_CASES])
def test_table_cases_of_the_shared_fixture(case: dict[str, str]) -> None:
    assert sanitize_html(case["input"], "/api/orgs/demo/media/") == case["output"]


def test_a_table_of_three_columns_comes_back_as_nh3_gives_it() -> None:
    html = (
        '<p>Vor &amp; nach\u00a0dem "Tisch"</p>'
        '<table><tbody><tr><td><a href="https://example.org/?a=1&b=2" target="_blank" rel="x">Link</a></td>'
        '<td>A &amp; B\u00a0C</td><td>"D" &lt; E</td></tr></tbody></table>'
        '<p><a href="/x?a=<b>" target="_blank">z</a></p>'
    )
    expected = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=_NH3_ATTRIBUTES,
        tag_attribute_values=_NH3_ATTRIBUTE_VALUES,
        url_schemes=URL_SCHEMES,
        link_rel=LINK_REL,
        strip_comments=True,
    )
    assert 'rel="noopener noreferrer"' in expected and "&nbsp;" in expected and "&amp;" in expected
    assert sanitize_html(html, PREFIX) == expected


def test_parts_outside_a_flattened_table_are_written_as_nh3_writes_them() -> None:
    html = (
        '<p><a href="/x?a=1&b=2&c=<d>" target="_blank">A &amp; B\u00a0C "q"</a></p>'
        "<table><tbody><tr><td>1</td><td>2</td><td>3</td><td>4</td></tr></tbody></table>"
        '<p>&lt;tag&gt;</p><img src="' + PREFIX + 'a.png" alt="x &quot;y&quot; &amp; \u00a0">'
    )
    cleaned = sanitize_html(html, PREFIX)
    assert cleaned.startswith(
        '<p><a href="/x?a=1&amp;b=2&amp;c=&lt;d&gt;" target="_blank" rel="noopener noreferrer">'
        'A &amp; B&nbsp;C "q"</a></p><p>1 · 2 · 3 · 4</p><p>&lt;tag&gt;</p>'
    )
    assert cleaned.endswith('alt="x &quot;y&quot; &amp; &nbsp;">')


def test_an_image_of_a_wide_table_follows_the_paragraph_of_its_row() -> None:
    html = (
        f'<table><tbody><tr><td><p>a</p><img src="{PREFIX}one.png" alt="eins"></td><td>b</td><td>c</td>'
        f'<td><a href="https://example.org/">d<img src="{PREFIX}two.png"></a></td></tr>'
        "<tr><td>e</td><td>f</td><td>g</td><td>h</td></tr></tbody></table>"
    )
    assert sanitize_html(html, PREFIX) == (
        f'<p>a · b · c · <a href="https://example.org/" rel="noopener noreferrer">d</a></p>'
        f'<img src="{PREFIX}one.png" alt="eins"><img src="{PREFIX}two.png"><p>e · f · g · h</p>'
    )


def test_a_figure_with_a_caption_moves_whole_out_of_a_wide_table() -> None:
    figure = f'<figure data-width="50"><img src="{PREFIX}a.png"><figcaption>Bildtext</figcaption></figure>'
    html = f"<table><tbody><tr><td>a{figure}</td><td>b</td><td>c</td><td>d</td></tr></tbody></table>"
    assert sanitize_html(html, PREFIX) == f"<p>a · b · c · d</p>{figure}"


def test_a_foreign_image_in_a_wide_table_goes_and_the_words_of_its_row_stay() -> None:
    html = (
        '<table><tbody><tr><td>eins<img src="https://evil.example/p.png"></td><td>zwei</td><td>drei</td>'
        '<td>vier<figure><img src="https://evil.example/q.png"><figcaption>fünf</figcaption></figure></td></tr>'
        "</tbody></table>"
    )
    cleaned = sanitize_html(html, PREFIX)
    assert "<img" not in cleaned and "evil.example" not in cleaned
    assert cleaned == "<p>eins · zwei · drei · vier</p>"


def test_a_table_nested_two_hundred_deep_loses_no_word() -> None:
    html = "<p>kern</p>"
    for level in range(200):
        html = f"<table><tbody><tr><td><p>w{level}</p>{html}</td><td>x</td></tr></tbody></table>"
    cleaned = sanitize_html(html, PREFIX)
    assert "kern" in cleaned
    for level in range(200):
        assert f"w{level}" in cleaned
    assert cleaned.count("<table") == 1


def test_deeply_nested_inline_and_block_markup_in_a_wide_table_does_not_raise() -> None:
    depth = 3000
    inline = "<strong>" * depth + "tief" + "</strong>" * depth
    block = "<blockquote>" * depth + "<p>Block</p>" + "</blockquote>" * depth
    html = f"<table><tbody><tr><td>{inline}</td><td>{block}</td><td>c</td><td>d</td></tr></tbody></table>"
    # This input is longer than the length limit, so it calls the table stage directly, on clean HTML.
    cleaned = _flatten_tables_outside_contract(html)
    assert "tief" in cleaned and "Block" in cleaned and "<table" not in cleaned


def test_a_table_of_three_hundred_rows_with_twenty_cells_finishes_within_a_second() -> None:
    row = "<tr>" + "".join(f"<td>z{index}</td>" for index in range(20)) + "</tr>"
    html = "<table><tbody>" + row * 300 + "</tbody></table>"
    started = time.perf_counter()
    # This input is longer than the length limit, so it calls the table stage directly, on clean HTML.
    cleaned = _flatten_tables_outside_contract(html)
    assert time.perf_counter() - started < 1
    assert cleaned.count("<p>") == 300 and "<table" not in cleaned


# Time. Every step of our own code takes time in proportion to the length of the text, whatever its shape.


def _shape(unit: str, length: int, prefix: str = "") -> str:
    return prefix + unit * ((length - len(prefix)) // len(unit))


LONG_SHAPES: dict[str, tuple[str, str]] = {
    "shape-1": ("<pre ", "<p>x</p>"),
    "shape-2": ("<code ", ""),
    "shape-3": ("<br ", "<pre>"),
    "shape-4": ("<a", ""),
    "shape-5": ("<h1 ", ""),
}


def _seconds(call: Any, text: str) -> float:
    started = time.perf_counter()
    call(text)
    return time.perf_counter() - started


@pytest.mark.parametrize("length", [400_000, 2_000_000], ids=["length-1", "length-2"])
@pytest.mark.parametrize("shape", list(LONG_SHAPES), ids=[f"shape-{n}" for n in range(1, len(LONG_SHAPES) + 1)])
def test_html_test_and_legacy_mapping_take_linear_time_on_long_texts(shape: str, length: int) -> None:
    unit, prefix = LONG_SHAPES[shape]
    text = _shape(unit, length, prefix)
    assert len(text) <= length
    assert _seconds(is_html, text) < 0.5
    assert _seconds(map_legacy_elements, text) < 0.5


@pytest.mark.parametrize("shape", list(LONG_SHAPES), ids=[f"shape-{n}" for n in range(1, len(LONG_SHAPES) + 1)])
def test_sanitize_html_of_the_longest_text_ends_within_a_second(shape: str) -> None:
    unit, prefix = LONG_SHAPES[shape]
    text = _shape(unit, RICH_TEXT_MAX_LENGTH, prefix)
    assert _seconds(lambda value: sanitize_html(value, PREFIX), text) < 1


def test_sanitize_html_of_the_longest_text_of_nested_or_attribute_heavy_markup_ends_within_a_second() -> None:
    nested = _shape("<div>", RICH_TEXT_MAX_LENGTH)
    attributes = "<p " + " ".join(f"a{index}=1" for index in range(RICH_TEXT_MAX_LENGTH // 9)) + ">"
    attributes = attributes[:RICH_TEXT_MAX_LENGTH]
    for text in (nested, attributes):
        assert len(text) <= RICH_TEXT_MAX_LENGTH
        assert _seconds(lambda value: sanitize_html(value, PREFIX), text) < 1


def test_sanitize_html_of_a_long_pre_with_many_lines_is_fast() -> None:
    # Each line of three characters becomes a paragraph of eight, so the cleaned text is over the limit and
    # the filter refuses it. It must still end fast.
    text = "<pre>" + "a\n\n" * (RICH_TEXT_MAX_LENGTH // 4 - 3) + "</pre>"
    assert len(text) <= RICH_TEXT_MAX_LENGTH

    def refused(value: str) -> None:
        with pytest.raises(RichTextTooLongError):
            sanitize_html(value, PREFIX)

    assert _seconds(refused, text) < 1
    shorter = "<pre>" + "a\n\n" * 6000 + "</pre>"
    assert _seconds(lambda value: sanitize_html(value, PREFIX), shorter) < 1


def test_one_character_over_the_limit_raises_at_once() -> None:
    assert RICH_TEXT_MAX_LENGTH == 50_000
    started = time.perf_counter()
    with pytest.raises(ValueError, match="50000"):
        sanitize_html("<p>" + "a" * RICH_TEXT_MAX_LENGTH, PREFIX)
    assert time.perf_counter() - started < 0.05
    with pytest.raises(ValueError):
        sanitize_html("a" * (RICH_TEXT_MAX_LENGTH + 1), PREFIX)
    assert sanitize_html("a" * RICH_TEXT_MAX_LENGTH, PREFIX) == "a" * RICH_TEXT_MAX_LENGTH


def test_is_html_keeps_its_meaning() -> None:
    assert is_html("<p>x</p>")
    assert is_html("a <B> b")
    assert is_html("x < y <a b>")
    assert not is_html("a < b")
    assert not is_html("a > b <")
    assert not is_html("<p unclosed")
    assert not is_html("> <p")
    assert not is_html("<3 and >")
    assert not is_html("")


# The legacy mapping works on the parsed tree of the ``nh3`` output.


def test_legacy_mapping_leaves_a_document_without_the_three_tags_byte_for_byte() -> None:
    document = "<p>a &amp; b\u00a0c<br>  \n d</p>"
    assert map_legacy_elements(document) == document


@pytest.mark.parametrize(
    ("legacy", "mapped"),
    [
        ("<h1>Titel</h1>", "<h2>Titel</h2>"),
        ("<p>a <code>b <em>c</em></code> d</p>", "<p>a b <em>c</em> d</p>"),
        ("<pre>Eins\nZwei</pre>", "<p>Eins<br>Zwei</p>"),
        ("<pre>Eins<br>Zwei</pre>", "<p>Eins<br>Zwei</p>"),
        ("<pre>Eins\r\nZwei\rDrei</pre>", "<p>Eins<br>Zwei<br>Drei</p>"),
        ("<pre>Eins\n\n\nZwei</pre>", "<p>Eins</p><p>Zwei</p>"),
        ("<pre>Eins\n \t\nZwei</pre>", "<p>Eins</p><p>Zwei</p>"),
        ("<pre>\n\nEins\n\n</pre>", "<p>Eins</p>"),
        ("<pre>   \n \n  </pre>", ""),
        ('<pre><em>a</em> <a href="/x">b</a><img src="/y">c</pre>', "<p>a bc</p>"),
        ("<pre><code>a\nb</code></pre><h1>x</h1>", "<p>a<br>b</p><h2>x</h2>"),
        ("<code><h1>x</h1></code>", "<h2>x</h2>"),
        ("<pre>a &lt; b &amp; c</pre>", "<p>a &lt; b &amp; c</p>"),
        ("<blockquote><pre>q</pre></blockquote>", "<blockquote><p>q</p></blockquote>"),
    ],
    ids=[f"case-{n}" for n in range(1, 15)],
)
def test_legacy_mapping_on_the_tree(legacy: str, mapped: str) -> None:
    assert map_legacy_elements(legacy) == mapped


def test_a_deeply_nested_legacy_document_does_not_raise() -> None:
    depth = 5000
    html = (
        "<code>" * depth
        + "<h1>x</h1>"
        + "</code>" * depth
        + "<pre>"
        + "<em>" * depth
        + "y"
        + "</em>" * depth
        + "</pre>"
    )
    assert map_legacy_elements(html) == "<h2>x</h2><p>y</p>"


# The strict image address.

MEDIA = "/api/orgs/demo/media/"


@pytest.mark.parametrize(
    "src",
    [
        f"{MEDIA}.\t./x.jpg",
        f"{MEDIA}..\n/x.jpg",
        f"{MEDIA}.\r./x.jpg",
        f"{MEDIA}x\u00a0.jpg",
        f"{MEDIA}x .jpg",
        f"{MEDIA}x\u2003.jpg",
        f"{MEDIA}x\x00.jpg",
        f"{MEDIA}x\x7f.jpg",
        f"{MEDIA}.",
        f"{MEDIA}..",
        f"{MEDIA}./x.jpg",
        f"{MEDIA}../x.jpg",
        f"{MEDIA}a/./b.jpg",
        f"{MEDIA}a/../b.jpg",
        f"{MEDIA}..?x=1",
        f"{MEDIA}a/..#top",
        f"{MEDIA}%2e%2e/x.jpg",
        f"{MEDIA}%2E/x.jpg",
        f"{MEDIA}a%2fb.jpg",
        f"{MEDIA}a%2Fb.jpg",
        f"{MEDIA}a%5Cb.jpg",
        f"{MEDIA}a%5cb.jpg",
        f"{MEDIA}a\\b.jpg",
    ],
    ids=[f"address-{n}" for n in range(1, 24)],
)
def test_an_unclean_media_address_is_refused(src: str) -> None:
    assert not _is_media_source(src, MEDIA)


@pytest.mark.parametrize(
    "src",
    [
        f"{MEDIA}abc_bild.png",
        f"{MEDIA}0000/media/abc.saal.png",
        f"{MEDIA}a..b.png",
        f"{MEDIA}.hidden",
        f"{MEDIA}a%20b.png",
    ],
    ids=[f"address-{n}" for n in range(1, 6)],
)
def test_a_clean_media_address_of_the_organization_passes(src: str) -> None:
    assert _is_media_source(src, MEDIA)
    assert not _is_media_source(src, "/api/orgs/other/media/")


def test_an_image_with_an_unclean_source_goes() -> None:
    cleaned = sanitize_html(f'<p>a<img src="{MEDIA}.&Tab;./x.jpg">b</p>', MEDIA)
    assert cleaned == "<p>ab</p>"
    raw_tab = sanitize_html(f'<p>a<img src="{MEDIA}.\t./x.jpg">b</p>', MEDIA)
    assert raw_tab == "<p>ab</p>"


def test_a_figure_around_an_image_with_an_unclean_source_goes_with_its_caption() -> None:
    html = f'<p>a</p><figure><img src="{MEDIA}.\n./x.jpg"><figcaption>Bildtext</figcaption></figure><p>b</p>'
    assert sanitize_html(html, MEDIA) == "<p>a</p><p>b</p>"


def test_a_clean_image_of_the_organization_stays_in_both_places() -> None:
    html = f'<figure><img src="{MEDIA}a.png"><figcaption>c</figcaption></figure>'
    assert sanitize_html(html, MEDIA) == html


# ---------------------------------------------------------------------------
# The limit holds for the text that is stored. Cleaning can make a text longer than it came in.
# ---------------------------------------------------------------------------


def test_a_text_that_cleaning_makes_too_long_is_refused_and_never_cut() -> None:
    # 50,000 characters come in. Each ampersand is stored as five characters, so the stored text
    # would have 249,972.
    text = "<p>" + "&" * (RICH_TEXT_MAX_LENGTH - 7) + "</p>"
    assert len(text) == RICH_TEXT_MAX_LENGTH
    with pytest.raises(RichTextTooLongError) as caught:
        sanitize_html(text, PREFIX, loc=("description", "de"))
    assert caught.value.max_length == RICH_TEXT_MAX_LENGTH
    assert caught.value.cleaned is True
    assert caught.value.loc == ("description", "de")
    assert "after cleaning" in str(caught.value)


def test_a_cleaned_text_of_exactly_the_limit_passes_and_one_more_is_refused() -> None:
    # "<p>" and "</p>" are 7 characters, each ampersand is stored as 5: 7 + 5 * 9998 + 3 = 50,000.
    exact = "<p>" + "&" * 9998 + "abc</p>"
    assert len(sanitize_html(exact, PREFIX)) == RICH_TEXT_MAX_LENGTH
    with pytest.raises(RichTextTooLongError):
        sanitize_html("<p>" + "&" * 9998 + "abcd</p>", PREFIX)


def test_a_stored_text_can_be_saved_again_unchanged() -> None:
    stored = sanitize_html("<p>" + "&" * 9998 + "abc</p>", PREFIX)
    assert sanitize_html(stored, PREFIX) == stored


def test_a_link_counts_with_the_rel_that_cleaning_adds() -> None:
    link = '<a href="https://example.org">x</a>'
    stored = sanitize_html(f"<p>{link}</p>", PREFIX)
    assert 'rel="noopener noreferrer"' in stored
    many = "<p>" + link * (RICH_TEXT_MAX_LENGTH // len(link) - 1) + "</p>"
    assert len(many) <= RICH_TEXT_MAX_LENGTH
    with pytest.raises(RichTextTooLongError) as caught:
        sanitize_html(many, PREFIX)
    assert caught.value.cleaned is True


def test_the_text_that_comes_in_keeps_its_own_limit_and_is_not_cleaned_first() -> None:
    with pytest.raises(RichTextTooLongError) as caught:
        sanitize_html("<p>" + "a" * RICH_TEXT_MAX_LENGTH, PREFIX, loc=("legal_imprint",))
    assert caught.value.cleaned is False
    assert caught.value.loc == ("legal_imprint",)


def test_the_reference_answer_keeps_its_smaller_limit_after_cleaning() -> None:
    text = "<p>" + "&" * 2500 + "</p>"
    assert len(text) <= REFERENCE_ANSWER_MAX_LENGTH
    with pytest.raises(RichTextTooLongError) as caught:
        sanitize_optional_html(text, PREFIX, max_length=REFERENCE_ANSWER_MAX_LENGTH, loc=("suggested_solution",))
    assert caught.value.max_length == REFERENCE_ANSWER_MAX_LENGTH
    assert caught.value.loc == ("suggested_solution",)
    assert sanitize_optional_html("<p>" + "&" * 1000 + "</p>", PREFIX, max_length=REFERENCE_ANSWER_MAX_LENGTH)


def test_the_tips_header_keeps_its_smaller_limit_after_cleaning_and_names_the_language() -> None:
    header = "<p>" + "&" * 500 + "</p>"
    assert len(header) <= TIPS_HEADER_MAX_LENGTH
    with pytest.raises(RichTextTooLongError) as caught:
        sanitize_station_tips({"header": {"de": "<p>kurz</p>", "en": header}, "links": []}, "stadt-augsburg")
    assert caught.value.max_length == TIPS_HEADER_MAX_LENGTH
    assert caught.value.loc == ("tips", "header", "en")


def test_the_language_of_a_multilingual_text_is_named() -> None:
    with pytest.raises(RichTextTooLongError) as caught:
        sanitize_multilingual({"de": "<p>ok</p>", "uk": "<p>" + "&" * 20000 + "</p>"}, PREFIX, loc=("description",))
    assert caught.value.loc == ("description", "uk")


def test_plain_text_without_a_tag_is_measured_as_it_is() -> None:
    plain = "a & b " * 300
    assert sanitize_html(plain, PREFIX, max_length=TIPS_HEADER_MAX_LENGTH) == plain
    with pytest.raises(RichTextTooLongError):
        sanitize_html("a" * (TIPS_HEADER_MAX_LENGTH + 1), PREFIX, max_length=TIPS_HEADER_MAX_LENGTH)


# ---------------------------------------------------------------------------
# Image sources come from parsed img elements. The media route opens an object to anyone on this answer.
# ---------------------------------------------------------------------------

REAL = f"{PREFIX}11111111-1111-1111-1111-111111111111/media/real.png"
DECOY = f"{PREFIX}11111111-1111-1111-1111-111111111111/media/decoy.png"


@pytest.mark.parametrize(
    "html",
    [
        # A source-like text inside another quoted attribute of the image, before the real source.
        f"""<img alt=" src='{DECOY}' " src="{REAL}">""",
        f"""<img alt=' src="{DECOY}" ' src="{REAL}">""",
        # Inside an attribute of another element, with a whole image tag in the value.
        f"""<a href="https://example.org/?q=<img src='{DECOY}'>">x</a><img src="{REAL}">""",
        # In a comment and in plain text.
        f"""<!-- <img src="{DECOY}"> --><img src="{REAL}">""",
        f"""<p>&lt;img src="{DECOY}"&gt;</p><img src="{REAL}">""",
        # A browser takes the first of two src attributes.
        f"""<img src="{REAL}" src="{DECOY}">""",
        # Upper case and no quotes are still a real source.
        f"""<IMG SRC={REAL}><p>src="{DECOY}"</p>""",
    ],
    ids=["alt-single", "alt-double", "other-attribute", "comment", "escaped-text", "two-src", "upper-case"],
)
def test_only_the_src_of_a_real_image_element_is_an_image_source(html: str) -> None:
    assert image_sources(html) == {REAL}
    assert image_object_keys(html, PREFIX) == {"11111111-1111-1111-1111-111111111111/media/real.png"}


def test_an_image_source_is_decoded_like_a_browser_reads_it() -> None:
    assert image_sources('<img src="/a&amp;b.png">') == {"/a&b.png"}
    assert image_sources(None) == set()
    assert image_sources("no tag at all") == set()


def test_an_object_key_counts_only_for_a_strict_source_under_the_prefix() -> None:
    key = "11111111-1111-1111-1111-111111111111/media/a b.png"
    assert image_object_keys(f'<img src="{PREFIX}11111111-1111-1111-1111-111111111111/media/a%20b.png">', PREFIX) == {
        key
    }
    refused = [
        "/api/orgs/other-org/media/11111111-1111-1111-1111-111111111111/media/a.png",
        f"https://example.org{PREFIX}x/media/a.png",
        f"{PREFIX}x/media/a.png?download=1",
        f"{PREFIX}x/media/a.png#top",
        f"{PREFIX}x/media/../../secret.png",
        f"{PREFIX}x/media/%2e%2e/secret.png",
        PREFIX,
    ]
    for src in refused:
        assert image_object_keys(f'<img src="{src}">', PREFIX) == set(), src


def test_an_image_address_with_a_query_or_a_fragment_is_not_stored() -> None:
    # The paste cleaner and the renderer of the frontend refuse both, so the write filter does too.
    assert sanitize_html(f'<p>a</p><img src="{PREFIX}a.png?variant=thumb">', PREFIX) == "<p>a</p>"
    assert sanitize_html(f'<p>a</p><img src="{PREFIX}a.png#x">', PREFIX) == "<p>a</p>"
    assert sanitize_html(f'<p>a</p><img src="{PREFIX}">', PREFIX) == "<p>a</p>"


# ---------------------------------------------------------------------------
# The table rule does linear work. The limits are wide on purpose: at this size work that grows with the
# square takes many seconds, and linear work a few hundredths of one.
# ---------------------------------------------------------------------------


def _wide_table(cell: str) -> str:
    return f"<table><tbody><tr><td>{cell}</td><td>b</td><td>c</td><td>d</td></tr></tbody></table>"


@pytest.mark.parametrize(
    "cell",
    [
        "<br>" * 12000 + "A",
        "A" + "<br>" * 12000,
        "<strong>" + "<br>" * 12000 + "A</strong>",
        "x<br>" * 9000,
        f'<a href="https://example.org">{"<img src=" + chr(34) + PREFIX + "a.png" + chr(34) + ">"}</a>' * 600,
    ],
    ids=["leading-breaks", "trailing-breaks", "breaks-in-an-inline", "many-lines", "many-images"],
)
def test_a_wide_table_with_a_heavy_cell_is_flattened_in_linear_time(cell: str) -> None:
    text = _wide_table(cell)
    assert len(text) <= RICH_TEXT_MAX_LENGTH
    assert _seconds(lambda value: sanitize_html(value, PREFIX), text) < 2


def test_images_deep_inside_inline_elements_are_taken_in_linear_time() -> None:
    depth = 3600
    cell = "<strong><em>" * (depth // 2) + f'<img src="{PREFIX}a.png">text' + "</em></strong>" * (depth // 2)
    text = _wide_table(cell)
    assert len(text) <= RICH_TEXT_MAX_LENGTH
    assert _seconds(lambda value: sanitize_html(value, PREFIX), text) < 2


def test_many_small_wide_tables_and_deeply_nested_tables_are_flattened_in_linear_time() -> None:
    many = "<table><tr><td>a</td><td>b</td><td>c</td><td>d</td></tr></table>" * 700
    assert len(many) <= RICH_TEXT_MAX_LENGTH
    cleaned = sanitize_html(many, PREFIX)
    assert cleaned == "<p>a · b · c · d</p>" * 700
    nested = "<table><tr><td>" * 1500 + "x" + "</td></tr></table>" * 1500
    assert len(nested) <= RICH_TEXT_MAX_LENGTH
    assert _seconds(lambda value: sanitize_html(value, PREFIX), many) < 2
    assert _seconds(lambda value: sanitize_html(value, PREFIX), nested) < 2


def test_leading_and_trailing_breaks_of_a_cell_leave_no_trace() -> None:
    cell = "<br><br> <strong><br>A</strong> <br>"
    assert sanitize_html(_wide_table(cell), PREFIX) == "<p><strong>A</strong> · b · c · d</p>"
