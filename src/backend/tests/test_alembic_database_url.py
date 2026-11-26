"""A percent-encoded database password must reach the connection, not die in the config.

``DATABASE_URL`` percent-encodes reserved characters in the password (``@`` as ``%40``).
``alembic upgrade head`` must get such a URL past ``config.set_main_option`` — which
stores the value in a ``ConfigParser`` and rejects a bare ``%`` — and fail at the
connection instead. The connection failure must not leak the password, in neither
its encoded nor its decoded form.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent

# A password with characters that are reserved in a URL and must be percent-encoded.
PASSWORD = "pa@ss:w/rd"
ENCODED_PASSWORD = quote(PASSWORD, safe="")


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_alembic(url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"dbUrl={url}", "upgrade", "head"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.mark.parametrize("via_x_argument", [True, False], ids=["x-argument", "env-var"])
def test_percent_encoded_password_reaches_the_connection(via_x_argument: bool) -> None:
    url = f"postgresql+asyncpg://clavis:{ENCODED_PASSWORD}@127.0.0.1:{_unused_port()}/clavis"

    if via_x_argument:
        result = _run_alembic(url)
    else:
        env = dict(os.environ, DATABASE_URL=url)
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "invalid interpolation" not in output
    assert ENCODED_PASSWORD not in output
    assert PASSWORD not in output
    # The run must have got past set_main_option and failed at the connection.
    assert "ConnectionRefusedError" in output
