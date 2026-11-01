"""Third-party licence notices of the installed distributions.

The API image writes /app/THIRD_PARTY_LICENSES.txt at build time; these tests
pin the collector against the dev interpreter, where fastapi is installed.
"""

import re
from pathlib import Path
from typing import Any

from app.third_party_licenses import (
    DistributionLicenses,
    _licence_texts,
    build_license_text,
    collect_licenses,
    render_licenses,
)


class _FakeDistribution:
    """A distribution whose files live in a plain directory."""

    def __init__(self, root: Path, files: list[str]) -> None:
        self._root = root
        self.files = files

    def locate_file(self, relative_path: str) -> Any:
        return self._root / relative_path


def test_names_fastapi_with_identifier_and_texts() -> None:
    entries = collect_licenses()
    fastapi = [entry for entry in entries if entry.name.lower() == "fastapi"]
    assert fastapi, "fastapi must be among the installed distributions"
    assert fastapi[0].version, "fastapi must carry a version"
    assert fastapi[0].licence not in ("", "UNKNOWN")
    # fastapi ships its licence file in dist-info/licenses. The collector
    # must read it through the RECORD.
    assert fastapi[0].licence_texts, "fastapi must carry its licence text"


def test_collect_lists_each_distribution_once_sorted() -> None:
    entries = collect_licenses()
    names = [entry.name for entry in entries]
    assert len(names) == len(set(names))
    assert names == sorted(names, key=str.lower)
    # The API's own distribution is not a third-party part.
    assert "clavis-backend" not in {name.lower() for name in names}


def test_build_text_names_fastapi_and_holds_a_licence_text() -> None:
    text = build_license_text()
    assert re.search(r"^fastapi \S+ — \S+", text, re.MULTILINE)
    # A real licence body is present, not only identifiers: fastapi's is MIT.
    assert "Permission is hereby granted, free of charge" in text


def test_collects_every_licence_file_without_a_cap(tmp_path: Path) -> None:
    # Distributions like cyclonedx-python-lib ship source files (license.py)
    # that sort before their LICENSE and NOTICE. A cap on the number of
    # collected files would silently drop the NOTICE.
    files = {
        "pkg/license.py": "source, not a notice",
        "pkg-1.0.0.dist-info/licenses/LICENSE": "licence text",
        "pkg-1.0.0.dist-info/licenses/NOTICE": "notice text",
        "pkg-1.0.0.dist-info/licenses/COPYRIGHT": "copyright text",
        "pkg-1.0.0.dist-info/licenses/EXTRA": "not a licence file",
    }
    for relative_path, content in files.items():
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    dist = _FakeDistribution(tmp_path, list(files))
    assert _licence_texts(dist) == ("copyright text", "licence text", "notice text", "source, not a notice")


def test_render_marks_entries_without_a_licence_file() -> None:
    text = render_licenses(
        [
            DistributionLicenses(name="with-text", version="1.0.0", licence="MIT", licence_texts=("MIT text",)),
            DistributionLicenses(name="no-text", version="2.0.0", licence="ISC", licence_texts=()),
        ]
    )
    assert "Third-party licences of the Clavis API image" in text
    assert "with-text 1.0.0 — MIT" in text
    assert "MIT text" in text
    assert "no-text 2.0.0 — ISC" in text
    assert "(no licence file shipped by this distribution)" in text
