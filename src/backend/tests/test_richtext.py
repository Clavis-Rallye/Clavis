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
    TABLE_CELL_SEPARATOR,
    TABLE_MAX_COLUMNS,
    URL_SCHEMES,
    image_sources,
    is_html,
    media_prefix_for,
    sanitize_html,
    sanitize_multilingual,
    sanitize_optional_html,
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
    cleaned = sanitize_html(html, PREFIX)
    assert "tief" in cleaned and "Block" in cleaned and "<table" not in cleaned


def test_a_table_of_three_hundred_rows_with_twenty_cells_finishes_within_a_second() -> None:
    row = "<tr>" + "".join(f"<td>z{index}</td>" for index in range(20)) + "</tr>"
    html = "<table><tbody>" + row * 300 + "</tbody></table>"
    started = time.perf_counter()
    cleaned = sanitize_html(html, PREFIX)
    assert time.perf_counter() - started < 1
    assert cleaned.count("<p>") == 300 and "<table" not in cleaned
