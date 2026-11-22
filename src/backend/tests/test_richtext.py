"""Server-side sanitising of rich text."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.richtext import (
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


@pytest.mark.parametrize("key", list(CORPUS_091["documents"]))
def test_release_091_corpus_is_sanitised_as_recorded(key: str) -> None:
    document = CORPUS_091["documents"][key]
    assert sanitize_html(document["input"], CORPUS_091["media_prefix"]) == document["sanitized"]


def test_release_091_corpus_is_complete() -> None:
    keys = set(CORPUS_091["documents"])
    assert len(keys) >= 10
    assert {"emptyString", "legacyPlainText", "legacyServerUnfiltered"} <= keys
    assert CORPUS_091["media_prefix"] == "/api/orgs/stadt/media/"
