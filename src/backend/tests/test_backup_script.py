"""
deployment/standalone/backup.sh backs up the standalone server: the pg_dump of the
database, the object store and the log files.

The files hold personal data, so the test holds that every file the script writes has
mode 0600 and the backup directory 0700, whatever the umask of the caller is. A
stand-in docker writes the archives with mode 0644, as a root container with umask
0022 does, so the test proves the host side of the script, whatever the container
does. When a chmod fails, on a path the caller does not own, the script names the
path on stderr and still writes the backup.
"""

import os
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deployment" / "standalone" / "backup.sh"
DAY = 86400

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs sh")

DOCKER = """#!/bin/sh
# Stand-in for docker. It covers exactly the calls of backup.sh.
set -eu

if [ "$1" = compose ]; then
    # docker compose exec -T db pg_dump ... -> a few bytes
    # docker compose ps -q garage|api      -> an id
    # docker compose stop|start garage      -> exit 0
    case "${4:-}" in
        pg_dump) printf 'dump'; exit 0 ;;
        stop | start) exit 0 ;;
    esac
    printf 'cid123\\n'
    exit 0
fi

if [ "$1" = run ]; then
    # docker run --rm --volumes-from <id> -v <host dir>:/backup alpine:3 sh -c "<text>":
    # write the archive named in the text under the host dir, with mode 0644.
    # The text itself is not run, so the test proves the host side of the script.
    host=
    archive=
    prev=
    text=
    for arg in "$@"; do
        if [ "$prev" = -v ]; then
            host="${arg%%:/backup*}"
        elif [ "$prev" = sh ] && [ "$arg" = -c ]; then
            text=1
        elif [ "$text" = 1 ]; then
            archive="${arg#*/backup/}"
            archive="${archive%% *}"
            text=
        fi
        prev="$arg"
    done
    mkdir -p "$host"
    printf 'x' > "$host/$archive"
    chmod 644 "$host/$archive"
fi

exit 0
"""

CHMOD = """#!/bin/sh
# Stand-in for chmod: it runs the real chmod on every path except the one it is
# told to deny. On that one it prints a message with the path and exits 1.
real={real}
deny={deny}
for arg in "$@"; do
    if [ "$arg" = "$deny" ]; then
        echo "chmod: cannot change '$arg': Operation not permitted" >&2
        exit 1
    fi
done
exec "$real" "$@"
"""


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def run_backup(tmp_path: Path):
    """A stand-in docker in tmp_path/bin, and the runner for the script."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(DOCKER, encoding="utf-8")
    docker.chmod(0o755)

    def _run(backup_dir: Path, deny: Path | None = None) -> subprocess.CompletedProcess[str]:
        if deny is not None:
            _write_chmod(bin_dir, deny)
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "BACKUP_DIR": str(backup_dir),
        }
        return subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, umask=0o002)

    return _run


def _write_chmod(bin_dir: Path, deny: Path) -> None:
    real = shutil.which("chmod")
    assert real is not None
    path = bin_dir / "chmod"
    path.write_text(CHMOD.format(real=real, deny=deny), encoding="utf-8")
    path.chmod(0o755)


def test_the_files_of_a_run_get_mode_0600(tmp_path: Path, run_backup: Callable):
    """C1, C2: dump and archives are 0600, even when the helper container writes 0644."""
    backups = tmp_path / "backups"

    result = run_backup(backups)

    assert result.returncode == 0, result.stderr
    files = sorted(backups.glob("clavis-*"))
    assert len(files) == 3
    for path in files:
        assert path.stat().st_size > 0
        assert _mode(path) == 0o600


def test_the_backup_directory_gets_mode_0700(tmp_path: Path, run_backup: Callable):
    """C3: a new directory is created 0700, and a directory made 0775 is closed to 0700."""
    for make_first in (False, True):
        backups = tmp_path / ("backups-new" if not make_first else "backups-wide")
        if make_first:
            backups.mkdir()
            backups.chmod(0o775)

        result = run_backup(backups)

        assert result.returncode == 0, result.stderr
        assert _mode(backups) == 0o700


def test_files_of_earlier_runs_get_mode_0600(tmp_path: Path, run_backup: Callable):
    """C4: an earlier clavis file of mode 0644, changed today, is closed to 0600."""
    backups = tmp_path / "backups"
    backups.mkdir()
    backups.chmod(0o700)
    old = backups / "clavis-db-20260101T000000Z.dump"
    old.write_text("old", encoding="utf-8")
    old.chmod(0o644)

    result = run_backup(backups)

    assert result.returncode == 0, result.stderr
    assert _mode(old) == 0o600


def test_a_chmod_failure_on_the_directory_warns_and_keeps_the_backup(tmp_path: Path, run_backup: Callable):
    """C5: a directory the caller does not own is named on stderr, the run still succeeds."""
    backups = tmp_path / "backups"
    backups.mkdir()

    result = run_backup(backups, deny=backups)

    assert result.returncode == 0
    assert str(backups) in result.stderr
    assert len(list(backups.glob("clavis-*"))) == 3


def test_a_chmod_failure_on_an_earlier_file_warns_and_keeps_the_backup(tmp_path: Path, run_backup: Callable):
    """C5: an earlier file the caller does not own is named on stderr, the run still succeeds."""
    backups = tmp_path / "backups"
    backups.mkdir()
    backups.chmod(0o700)
    old = backups / "clavis-db-20260101T000000Z.dump"
    old.write_text("old", encoding="utf-8")
    old.chmod(0o644)

    result = run_backup(backups, deny=old)

    assert result.returncode == 0
    assert str(old) in result.stderr
    assert len(list(backups.glob("clavis-*"))) == 4


def test_the_output_and_the_rotation_stay_as_they_are(tmp_path: Path, run_backup: Callable):
    """C6: the four ==> lines stay, and a file older than BACKUP_KEEP_DAYS is deleted."""
    backups = tmp_path / "backups"
    backups.mkdir()
    backups.chmod(0o700)
    old = backups / "clavis-db-20260101T000000Z.dump"
    old.write_text("old", encoding="utf-8")
    stamp = time.time() - 40 * DAY
    os.utime(old, (stamp, stamp))

    result = run_backup(backups)

    assert result.returncode == 0, result.stderr
    for line in (
        "==> Database -> ",
        "==> Object store -> ",
        "==> Logs -> ",
        "==> Deleting backups older than",
    ):
        assert line in result.stdout
    assert not old.exists()
