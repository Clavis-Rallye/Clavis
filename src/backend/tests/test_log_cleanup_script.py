"""
deployment/scripts/log-cleanup.sh deletes rotated log files by age.

The retention periods come from the same variables as the App: AUDIT_LOG_RETENTION_DAYS and its
siblings. The script reads them from the environment, then from the deployment .env, then falls
back to 365 days for the audit log and 90 days for the rest.
"""

import gzip
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deployment" / "scripts" / "log-cleanup.sh"
DAY = 86400

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("gzip") is None, reason="needs bash and gzip"
)


def _aged(log_dir: Path, name: str, days: float) -> Path:
    path = log_dir / name
    path.write_text('{"event": "x"}\n', encoding="utf-8")
    stamp = time.time() - days * DAY
    os.utime(path, (stamp, stamp))
    return path


def _run(log_dir: Path, env_file: Path, **variables: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "LOG_DIR": str(log_dir), "ENV_FILE": str(env_file), **variables}
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=True)


def test_the_retention_variables_set_the_periods(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    _aged(logs, "audit.log.2026-01-01", 300)
    _aged(logs, "audit.log.2026-10-01", 50)
    _aged(logs, "application.log.2026-09-01", 50)
    _aged(logs, "application.log.2026-11-01", 20)
    _aged(logs, "error.log.2026-09-01", 50)
    _aged(logs, "access.log.2026-09-01", 50)
    _aged(logs, "access.log.2026-11-01", 20)

    result = _run(
        logs,
        tmp_path / "missing.env",
        AUDIT_LOG_RETENTION_DAYS="200",
        APP_LOG_RETENTION_DAYS="30",
        ERROR_LOG_RETENTION_DAYS="60",
        ACCESS_LOG_RETENTION_DAYS="30",
    )

    assert sorted(p.name for p in logs.iterdir()) == [
        "access.log.2026-11-01.gz",
        "application.log.2026-11-01.gz",
        "audit.log.2026-10-01.gz",
        "error.log.2026-09-01.gz",
    ]
    assert "audit 200, application 30, error 60, access 30" in result.stdout


def test_without_variables_the_old_periods_apply(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    _aged(logs, "audit.log.2025-10-01", 400)
    _aged(logs, "audit.log.2026-01-01", 300)
    _aged(logs, "application.log.2026-08-01", 100)
    _aged(logs, "application.log.2026-10-01", 50)
    _aged(logs, "audit.log.2026-11-17", 2)

    _run(logs, tmp_path / "missing.env")

    assert sorted(p.name for p in logs.iterdir()) == [
        "application.log.2026-10-01.gz",
        "audit.log.2026-01-01.gz",
        "audit.log.2026-11-17",
    ]


def test_the_deployment_env_file_is_read_and_the_environment_wins(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    _aged(logs, "audit.log.2026-06-01", 150)
    _aged(logs, "application.log.2026-10-01", 50)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HMAC_AUDIT_KEY=not-read\nAUDIT_LOG_RETENTION_DAYS=100\nAPP_LOG_RETENTION_DAYS='40'\n", encoding="utf-8"
    )

    _run(logs, env_file, APP_LOG_RETENTION_DAYS="60")

    assert sorted(p.name for p in logs.iterdir()) == ["application.log.2026-10-01.gz"]


def test_an_invalid_value_keeps_the_default(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    _aged(logs, "audit.log.2026-01-01", 300)

    result = _run(logs, tmp_path / "missing.env", AUDIT_LOG_RETENTION_DAYS="0")

    assert [p.name for p in logs.iterdir()] == ["audit.log.2026-01-01.gz"]
    assert "AUDIT_LOG_RETENTION_DAYS=0 is not a number of days" in result.stderr


def test_compressed_files_keep_their_age(tmp_path: Path):
    """gzip keeps the modification time, so a compressed file is deleted on time."""
    logs = tmp_path / "logs"
    logs.mkdir()
    old = _aged(logs, "audit.log.2026-01-01", 300)
    with gzip.open(str(old) + ".gz", "wt") as fh:
        fh.write('{"event": "x"}\n')
    stamp = time.time() - 300 * DAY
    os.utime(str(old) + ".gz", (stamp, stamp))
    old.unlink()

    _run(logs, tmp_path / "missing.env", AUDIT_LOG_RETENTION_DAYS="200")

    assert list(logs.iterdir()) == []
