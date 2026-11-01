"""
The security headers of the API's own responses.

The Content Security Policy of the API matters for the pages the API serves
itself, /api/docs and /api/redoc. It must name no foreign host: the product
promises that a browser contacts no third party except the services the
privacy text names, and FastAPI's stock pages ask for a Google style sheet
and an icon from fastapi.tiangolo.com.
"""

import re

import pytest
from httpx import AsyncClient
from starlette.requests import Request
from starlette.responses import Response

from app.main import add_security_headers


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/health", "/api/docs", "/api/redoc"])
async def test_the_api_policy_names_no_foreign_host(client: AsyncClient, path: str):
    response = await client.get(path)

    policy = response.headers["Content-Security-Policy"]
    assert re.findall(r"https?://[^\s;]+", policy) == []
    # A bare scheme would allow every host of that scheme.
    for directive in policy.split(";"):
        name, *sources = directive.split()
        if name not in ("connect-src",):
            assert "https:" not in sources and "http:" not in sources and "*" not in sources, directive


@pytest.mark.asyncio
async def test_the_middleware_keeps_a_policy_the_route_set():
    """A route's own Content-Security-Policy stays, so the file endpoint keeps its no-script policy for uploads."""

    async def route_answer(_request: Request) -> Response:
        return Response("ok", headers={"Content-Security-Policy": "default-src 'none'"})

    response = await add_security_headers(Request({"type": "http"}), route_answer)

    assert response.headers["Content-Security-Policy"] == "default-src 'none'"
    # The other hardening headers are still applied.
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_the_middleware_sets_its_policy_when_the_route_set_none():
    async def route_answer(_request: Request) -> Response:
        return Response("ok")

    response = await add_security_headers(Request({"type": "http"}), route_answer)

    assert response.headers["Content-Security-Policy"].startswith("default-src 'self'")
