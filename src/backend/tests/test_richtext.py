"""Server-side sanitising of rich text."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.richtext import (
    ALLOWED_ATTRIBUTE_VALUES,
    ALLOWED_ATTRIBUTES,
    ALLOWED_TAGS,
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


# Release 0.9.1 removed the blockquote tag and kept its text. The editor now has quotes, so the
# filter keeps the element. Every other part of the output is the recorded one.
CHANGED_BY_THE_NEW_EDITOR: dict[str, str] = {
    # The quote of this document is a blockquote now and stays one; the rest equals the 0.9.1 output.
    "legacyServerUnfiltered": (
        "Alter TitelZeile eins\nZeile zwei<p>Ein Aufruf wie ls -l im Text.</p><blockquote><p>Ein Zitat.</p></blockquote>"
    ),
}


@pytest.mark.parametrize("key", list(CORPUS_091["documents"]))
def test_release_091_corpus_is_sanitised_as_recorded(key: str) -> None:
    document = CORPUS_091["documents"][key]
    expected = CHANGED_BY_THE_NEW_EDITOR.get(key, document["sanitized"])
    assert sanitize_html(document["input"], CORPUS_091["media_prefix"]) == expected


def test_changed_pins_differ_from_the_recording_only_by_the_kept_blockquote() -> None:
    for key, expected in CHANGED_BY_THE_NEW_EDITOR.items():
        recorded = CORPUS_091["documents"][key]["sanitized"]
        assert expected != recorded
        assert expected.replace("<blockquote>", "").replace("</blockquote>", "") == recorded


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
