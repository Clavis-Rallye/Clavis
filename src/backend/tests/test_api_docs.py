"""
/api/docs and /api/redoc load every file from the API's own server.

The API's content policy allows scripts from its own server only. FastAPI's
stock pages load their script from cdn.jsdelivr.net and would stay empty
under that policy. These pages reference /api/docs-assets/ only.
"""

import re
from pathlib import Path

import pytest
from httpx import AsyncClient

ASSETS = Path(__file__).resolve().parents[1] / "app" / "static" / "api-docs"

_REFERENCE = re.compile(r"""(?:src|href|spec-url|data-openapi-url)\s*=\s*"([^"]*)\"""")
_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.DOTALL)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "/api/docs",
            [
                "data:,",
                "/api/docs-assets/swagger-ui/swagger-ui.css",
                "/api/openapi.json",
                "/api/docs-assets/swagger-ui/swagger-ui-bundle.js",
                "/api/docs-assets/swagger-init.js",
            ],
        ),
        ("/api/redoc", ["data:,", "/api/openapi.json", "/api/docs-assets/redoc/redoc.standalone.js"]),
    ],
)
async def test_the_page_references_only_its_own_server(client: AsyncClient, path: str, expected: list[str]):
    response = await client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert _REFERENCE.findall(response.text) == expected
    assert "http://" not in response.text and "https://" not in response.text
    # No inline script: every script element has a src and an empty body.
    for attributes, body in _SCRIPT.findall(response.text):
        assert "src=" in attributes and body.strip() == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "content_type", "min_size"),
    [
        ("/api/docs-assets/swagger-ui/swagger-ui-bundle.js", "javascript", 1_000_000),
        ("/api/docs-assets/swagger-ui/swagger-ui.css", "text/css", 100_000),
        ("/api/docs-assets/swagger-init.js", "javascript", 200),
        ("/api/docs-assets/redoc/redoc.standalone.js", "javascript", 1_000_000),
    ],
)
async def test_the_page_files_are_served(client: AsyncClient, path: str, content_type: str, min_size: int):
    response = await client.get(path)

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert len(response.content) >= min_size


@pytest.mark.asyncio
async def test_the_pages_keep_the_api_policy(client: AsyncClient):
    """The pages and the asset route carry the same content policy, and it names no CDN."""
    page = await client.get("/api/docs")
    asset = await client.get("/api/docs-assets/swagger-init.js")

    assert page.headers["Content-Security-Policy"] == asset.headers["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" not in page.headers["Content-Security-Policy"]


@pytest.mark.asyncio
async def test_an_unknown_asset_is_404(client: AsyncClient):
    response = await client.get("/api/docs-assets/../main.py")

    assert response.status_code == 404


def test_the_swagger_start_file_switches_the_online_validator_off():
    """The default validator badge sends the address of the specification to validator.swagger.io."""
    assert "validatorUrl: null" in (ASSETS / "swagger-init.js").read_text(encoding="utf-8")


def test_the_redoc_bundle_asks_no_cdn_for_its_logo():
    bundle = (ASSETS / "redoc" / "redoc.standalone.js").read_text(encoding="utf-8")

    assert "cdn.redoc.ly" not in bundle
    assert 'alt:"redocly logo",onError:()=>t(!1),src:""' in bundle


@pytest.mark.parametrize(
    "licence",
    [
        "swagger-ui/LICENSE",
        "swagger-ui/NOTICE",
        "swagger-ui/swagger-ui-bundle.js.LICENSE.txt",
        "redoc/LICENSE",
        "redoc/redoc.standalone.js.LICENSE.txt",
    ],
)
def test_the_licences_travel_with_the_files(licence: str):
    assert (ASSETS / licence).stat().st_size > 0


@pytest.mark.asyncio
async def test_redoc_runs_without_its_search(client: AsyncClient):
    """ReDoc searches in a blob: worker, which the API's policy blocks, so the page turns its search off."""
    response = await client.get("/api/redoc")

    assert 'disable-search="true"' in response.text
