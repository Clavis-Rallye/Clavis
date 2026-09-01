"""
Query helpers shared by the station and the challenge library: the search, the display-title
sort key and the interface-language rule. Each repository's ``search_library`` composes them.
"""

from typing import Any

from sqlalchemy import ColumnElement, func, select


def normalize_query(q: str | None) -> str | None:
    """Trim `q`, cut it to 200 characters, and return None when nothing is left."""
    if q is None:
        return None
    trimmed = q.strip()[:200].strip()
    return trimmed or None


def base_lang(lang: str) -> str:
    """Lowercase `lang` and keep the part before its first '-' (e.g. 'en-GB' -> 'en')."""
    return lang.lower().split("-", 1)[0]


def title_matches(title_col: Any, q: str) -> ColumnElement[bool]:
    """EXISTS clause: does any language value of the JSONB title column contain `q`?

    It never casts the JSONB to text, because that would also match key names.
    """
    rows = func.jsonb_each_text(title_col).table_valued("key", "value")
    return select(1).select_from(rows).where(rows.c.value.icontains(q, autoescape=True)).exists()


def display_title_key(title_col: Any, lang: str) -> ColumnElement[str]:
    """The display title for the `title` sort and every tie-break.

    The `lang` value, else `de`, else `en`, else empty, as in the frontend's
    `resolveLocalized()`. Lowercased under the ICU root collation.
    """
    key = func.coalesce(
        func.nullif(title_col[lang].astext, ""),
        func.nullif(title_col["de"].astext, ""),
        func.nullif(title_col["en"].astext, ""),
        "",
    )
    return func.lower(key).collate("und-x-icu")
