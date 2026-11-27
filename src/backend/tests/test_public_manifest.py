"""GET /api/public/manifest.webmanifest: the web manifest, named after the brand.

An installed app takes its name from the manifest. The frontend build wrote a fixed name, so an
instance with its own `BRAND_NAME` still installed as `Clavis`. The API now builds the manifest.
The tests hold that the answer without `BRAND_NAME` equals the manifest of the 0.9.2 build field by
field, that a brand name replaces `name` and `short_name` and nothing else, that the name comes back
exactly even with a quote, a backslash or a non-ASCII character, and that the endpoint is open
without sign-in, cacheable and rate limited.
"""

import json

import pytest
from httpx import AsyncClient

from app.config import get_settings

MANIFEST_URL = "/api/public/manifest.webmanifest"

# The manifest the 0.9.2 build writes (src/frontend/vite.config.ts, the block `manifest`).
BUILD_MANIFEST = {
    "name": "Clavis",
    "short_name": "Clavis",
    "description": "Mobile-first city rallye platform for immigrants and newcomers",
    "theme_color": "#1D47A4",
    "background_color": "#FEF7FF",
    "display": "standalone",
    "start_url": "/",
    "scope": "/",
    "orientation": "portrait-primary",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}


@pytest.mark.asyncio
async def test_manifest_without_brand_name_equals_the_build_manifest(client: AsyncClient):
    resp = await client.get(MANIFEST_URL)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/manifest+json")
    assert resp.json() == BUILD_MANIFEST
    # No id: an installed app keeps its identity.
    assert "id" not in resp.json()


@pytest.mark.asyncio
async def test_manifest_carries_the_brand_name(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(get_settings(), "brand_name", "Stadtrallye Musterstadt")

    resp = await client.get(MANIFEST_URL)

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Stadtrallye Musterstadt"
    assert data["short_name"] == "Stadtrallye Musterstadt"
    rest = {key: value for key, value in data.items() if key not in ("name", "short_name")}
    expected_rest = {key: value for key, value in BUILD_MANIFEST.items() if key not in ("name", "short_name")}
    assert rest == expected_rest


@pytest.mark.asyncio
async def test_manifest_is_public_and_cacheable(client: AsyncClient):
    # The client carries no session cookie.
    assert not client.cookies

    resp = await client.get(MANIFEST_URL)

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=300"


@pytest.mark.asyncio
async def test_manifest_returns_a_difficult_brand_name_exactly(client: AsyncClient, monkeypatch):
    brand = 'Rallye "Süd" \\ 1'
    monkeypatch.setattr(get_settings(), "brand_name", brand)

    resp = await client.get(MANIFEST_URL)

    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["name"] == brand
    assert data["short_name"] == brand


@pytest.mark.asyncio
async def test_manifest_is_rate_limited_at_120_per_minute(client: AsyncClient):
    for i in range(120):
        resp = await client.get(MANIFEST_URL)
        assert resp.status_code == 200, f"Request {i + 1} failed with {resp.status_code}"

    resp = await client.get(MANIFEST_URL)
    assert resp.status_code == 429
