"""Third-party licence notices for every distribution in the running Python.

MIT, BSD, ISC and Apache-2.0 all ask for their notice in every copy, and the
installed site-packages of the API image are a copy. The Dockerfile runs this
module once in the final stage and writes /app/THIRD_PARTY_LICENSES.txt with,
per distribution: name, version, the licence expression or classifier from the
metadata, and the licence texts the distribution ships.

Kept free of app imports and stdlib-only so it runs standalone at image build
time (``python -m app.third_party_licenses``) and stays testable against any
interpreter, dev or image.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

# Licence-file names distributions ship (PEP 639 standardised on LICENSE, but
# plenty of wheels still carry COPYING or NOTICE), optionally inside the
# dist-info "licenses" directory or at the package root. Matched against the
# paths of the distribution's RECORD.
_LICENCE_FILE = re.compile(
    r"(?:^|/)(?:licenses?/)?(?:licen[cs]e|copying|notice|copyright)(?:[-._][^/]*)?$",
    re.IGNORECASE,
)

# The API itself is installed into the venv by ``uv sync``; it is our own code,
# not a third-party part, and carries no notice of its own here.
_OWN_DISTRIBUTIONS = frozenset({"clavis-backend"})


@dataclass(frozen=True)
class DistributionLicenses:
    """One installed distribution and the licence information it declares."""

    name: str
    version: str
    licence: str
    licence_texts: tuple[str, ...]


def _licence_identifier(dist: metadata.Distribution) -> str:
    """PEP 639 expression, else the classic License field, else classifiers."""
    expression = (dist.metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    classic = (dist.metadata.get("License") or "").strip()
    if classic:
        return classic
    classifiers = [
        part.strip()
        for classifier in dist.metadata.get_all("Classifier") or []
        if classifier.startswith("License ::")
        for part in [classifier.split("::")[-1]]
        if part.strip()
    ]
    if classifiers:
        return " OR ".join(classifiers)
    return "UNKNOWN"


def _licence_texts(dist: metadata.Distribution) -> tuple[str, ...]:
    """The licence files the distribution ships, read through the RECORD."""
    matches = sorted({str(path) for path in dist.files or [] if _LICENCE_FILE.search(str(path))})
    texts: list[str] = []
    for relative_path in matches:
        try:
            text = Path(str(dist.locate_file(relative_path))).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            texts.append(text.strip())
    return tuple(texts)


def collect_licenses() -> list[DistributionLicenses]:
    """Every distribution visible to the running interpreter, sorted by name."""
    by_name: dict[str, DistributionLicenses] = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name or name.lower() in _OWN_DISTRIBUTIONS:
            continue
        # The same distribution can be visible twice (system site-packages
        # next to the venv); the first sighting wins.
        by_name.setdefault(
            name,
            DistributionLicenses(
                name=name,
                version=dist.version,
                licence=_licence_identifier(dist),
                licence_texts=_licence_texts(dist),
            ),
        )
    return sorted(by_name.values(), key=lambda entry: entry.name.lower())


_HEADER = """\
Third-party licences of the Clavis API image
============================================

Every Python distribution installed in this image is listed with its name,
version, licence from its metadata, and the licence texts it ships. The
image and this file are one copy."""

_RULE = "-" * 79


def _render_entry(entry: DistributionLicenses) -> str:
    lines = [_RULE, f"{entry.name} {entry.version} — {entry.licence}", _RULE, ""]
    if entry.licence_texts:
        lines.extend(entry.licence_texts)
    else:
        lines.append("(no licence file shipped by this distribution)")
    return "\n".join(lines)


def render_licenses(entries: list[DistributionLicenses]) -> str:
    """The notice document for the given entries, in the given order."""
    return "\n\n".join([_HEADER, *(_render_entry(entry) for entry in entries)]) + "\n"


def build_license_text() -> str:
    """The full notice document, as the Dockerfile writes it."""
    return render_licenses(collect_licenses())


def main() -> None:
    print(build_license_text(), end="")


if __name__ == "__main__":
    main()
