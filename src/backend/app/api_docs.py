"""
The API documentation pages, served from the API's own server.

FastAPI's stock pages ``/api/docs`` (Swagger UI) and ``/api/redoc`` (ReDoc)
load their files from cdn.jsdelivr.net, fonts.googleapis.com and
fastapi.tiangolo.com. The API's content policy allows scripts from its own
server only, so both pages would stay empty. Clavis also promises that a
browser makes no request to a third party at runtime.

These pages load every file from ``/api/docs-assets/``. The files and their
licences live in ``app/static/api-docs/``. ``scripts/update-api-docs-assets.sh``
fetches them from the npm registry and records the versions in
``VERSIONS.txt``. The pages need no foreign host and no inline script, so the
content policy in ``app/main.py`` stays as it is.

ReDoc runs without its search box (``disable-search``): its search runs in a
worker built from a ``blob:`` address, which the policy does not allow. The
empty ``data:`` icon keeps the browser from asking for ``/favicon.ico``, which
the API does not serve.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

ASSETS_DIR = Path(__file__).resolve().parent / "static" / "api-docs"
ASSETS_URL = "/api/docs-assets"


def _swagger_ui_html(title: str, openapi_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} - Swagger UI</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="{ASSETS_URL}/swagger-ui/swagger-ui.css">
</head>
<body>
<div id="swagger-ui" data-openapi-url="{escape(openapi_url)}"></div>
<script src="{ASSETS_URL}/swagger-ui/swagger-ui-bundle.js"></script>
<script src="{ASSETS_URL}/swagger-init.js"></script>
</body>
</html>
"""


def _redoc_html(title: str, openapi_url: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} - ReDoc</title>
<link rel="icon" href="data:,">
</head>
<body>
<redoc spec-url="{escape(openapi_url)}" disable-search="true"></redoc>
<script src="{ASSETS_URL}/redoc/redoc.standalone.js"></script>
</body>
</html>
"""


def install_api_docs(app: FastAPI, *, docs_url: str, redoc_url: str) -> None:
    """Serve Swagger UI at ``docs_url`` and ReDoc at ``redoc_url`` from local files.

    Create the app with ``docs_url=None`` and ``redoc_url=None``, so FastAPI
    does not register its own pages, then call this.
    """
    openapi_url = app.openapi_url
    if openapi_url is None:
        return
    swagger_page = _swagger_ui_html(app.title, openapi_url)
    redoc_page = _redoc_html(app.title, openapi_url)

    app.mount(ASSETS_URL, StaticFiles(directory=ASSETS_DIR), name="api-docs-assets")

    @app.get(docs_url, include_in_schema=False)
    async def swagger_ui() -> HTMLResponse:
        return HTMLResponse(swagger_page)

    @app.get(redoc_url, include_in_schema=False)
    async def redoc() -> HTMLResponse:
        return HTMLResponse(redoc_page)
