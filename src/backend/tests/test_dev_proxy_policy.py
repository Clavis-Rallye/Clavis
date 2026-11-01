"""The development proxy lets the browser reach the hosts the product asks.

``Caddyfile.dev`` sets the Content-Security-Policy for the pages in the dev
overlay, because the Vite dev server sets none. The frontend's nginx sets the
policy of a built installation. When nginx allows a tile or search host in
``connect-src`` or ``img-src`` that the dev policy does not allow, the map
stays grey in development while it works in production.

The nginx file is a template: ``${MAP_TILE_ORIGIN}`` is the origin of
``MAP_TILE_URL``, which the Dockerfile defaults. The test fills it the same way
the entrypoint does.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CADDYFILE = ROOT / "Caddyfile.dev"
NGINX = ROOT / "src" / "frontend" / "nginx.conf"
DOCKERFILE = ROOT / "src" / "frontend" / "Dockerfile"

CHECKED = ("connect-src", "img-src")
# Sources that are no outside origin.
NOT_EXTERNAL = {"'self'", "'none'", "data:", "blob:", "ws:", "wss:"}


def _tile_origin() -> str:
    """The origin the nginx entrypoint derives from the default MAP_TILE_URL."""
    match = re.search(r"^ENV MAP_TILE_URL=(\S+)", DOCKERFILE.read_text(), re.M)
    assert match, "the Dockerfile sets no default MAP_TILE_URL"
    origin = re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/?#]+", match.group(1))
    assert origin, f"MAP_TILE_URL is no URL: {match.group(1)}"
    return re.sub(r"://\{s\}\.", "://*.", origin.group(0))


def _policy(text: str, marker: str) -> dict[str, list[str]]:
    """The directives of the policy that starts at the first ``marker`` in text."""
    start = text.index(marker)
    end = text.index('"', start)
    directives: dict[str, list[str]] = {}
    for part in text[start:end].split(";"):
        words = part.split()
        if words:
            directives[words[0]] = words[1:]
    return directives


def _nginx_policy() -> dict[str, list[str]]:
    text = NGINX.read_text().replace("${MAP_TILE_ORIGIN}", _tile_origin())
    return _policy(text, "default-src 'self'; script-src 'self';")


def _caddy_policy() -> dict[str, list[str]]:
    lines = [
        line
        for line in CADDYFILE.read_text().splitlines()
        if "Content-Security-Policy" in line and not line.lstrip().startswith("#")
    ]
    assert len(lines) == 1, "Caddyfile.dev must set one Content-Security-Policy"
    return _policy(lines[0], "default-src")


def _host(source: str) -> str:
    return re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", source).split("/")[0]


def _covers(allowed: list[str], source: str) -> bool:
    """Whether the source list of a directive allows the origin ``source``."""
    host = _host(source)
    for entry in allowed:
        if entry in ("*", "https:") and source.startswith("https://"):
            return True
        if entry == "http:" and source.startswith("http://"):
            return True
        if entry == source:
            return True
        entry_host = _host(entry)
        if "://" in entry and entry_host.startswith("*.") and host.endswith(entry_host[1:]):
            return True
        if "://" in entry and entry_host.startswith("*.") and host == entry_host:
            return True
    return False


def _external(sources: list[str]) -> list[str]:
    return [s for s in sources if s not in NOT_EXTERNAL and "://" in s and not re.match(r"wss?://\$host$", s)]


def test_nginx_names_a_tile_origin():
    """Guards the test itself: nginx has to name at least one external origin."""
    policy = _nginx_policy()
    assert _external(policy["img-src"]), policy["img-src"]
    assert _external(policy["connect-src"]), policy["connect-src"]


@pytest.mark.parametrize("directive", CHECKED)
def test_dev_policy_covers_every_external_origin_of_nginx(directive: str):
    nginx = _nginx_policy()
    caddy = _caddy_policy()
    missing = [
        origin for origin in _external(nginx.get(directive, [])) if not _covers(caddy.get(directive, []), origin)
    ]
    assert not missing, f"nginx allows {missing} in {directive}, but Caddyfile.dev allows only {caddy.get(directive)}"


def test_cover_rules():
    assert _covers(["https:"], "https://tile.openstreetmap.de")
    assert _covers(["*"], "https://tile.openstreetmap.de")
    assert _covers(["https://*.tile.openstreetmap.org"], "https://a.tile.openstreetmap.org")
    assert not _covers(["https://*.tile.openstreetmap.org"], "https://tile.openstreetmap.de")
    assert not _covers(["'self'", "ws:"], "https://tile.openstreetmap.de")
