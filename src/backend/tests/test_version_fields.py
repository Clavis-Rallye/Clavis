"""
One version for the whole App: the backend module, its package, the lock file,
the API description and the frontend package must name the same version, so a
release cannot ship two numbers.
"""

import json
import tomllib
from pathlib import Path

import pytest

from app import __version__
from app.main import app

BACKEND = Path(__file__).resolve().parent.parent
FRONTEND = BACKEND.parent / "frontend"


def test_backend_package_matches_module() -> None:
    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__


def test_api_description_takes_the_module_version() -> None:
    assert app.version == __version__
    assert app.openapi()["info"]["version"] == __version__


def test_uv_lock_agrees_with_backend_package() -> None:
    lock = tomllib.loads((BACKEND / "uv.lock").read_text(encoding="utf-8"))
    entry = next(p for p in lock["package"] if p["name"] == "clavis-backend")
    assert entry["version"] == __version__


@pytest.mark.skipif(
    not (FRONTEND / "package.json").is_file(),
    reason="frontend/package.json is absent: the API image holds the backend alone",
)
def test_frontend_package_matches_module() -> None:
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == __version__


@pytest.mark.skipif(
    not (FRONTEND / "package-lock.json").is_file(),
    reason="frontend/package-lock.json is absent: the API image holds the backend alone",
)
def test_frontend_lock_agrees_with_frontend_package() -> None:
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == __version__
    assert lock["packages"][""]["version"] == __version__
