"""
Audit log file handler with a persistent HMAC integrity chain.

The handler is the only writer of the chain: ``record_audit_event`` does not touch chain state.
It does every step of an entry under one lock, so no race breaks the chain across workers,
restarts or rotations:

- render the event with structlog's formatter,
- compute the chain hash over the finalized event dict and inject it into the rendered line,
- write, flush and ``fsync`` the file,
- overwrite the sidecar (``audit.last-hash``) in place and ``fsync`` it,
- on rotation, run stdlib's ``doRollover``, ``fsync`` the parent dir and write a ``segment_start`` entry.

Two processes each run a handler on the same directory: the API and a server command such as
``python -m app.cli demo-content load``. Neither can trust its in-memory chain, so every entry takes
its predecessor hash from the sidecar, read under the ``flock`` that ``emit`` holds. The in-memory
value is only the fallback for an empty sidecar.

Only one process may be the ``rotation_owner`` (default ``True``). The other sets it ``False`` right
after setup. If both rotated, each would keep its own stream, one would write into the archived file
with a predecessor in the new one, and verification in directory order would fail. So a non-owner
never rolls over and opens the current file by path for every write. The owner checks under the lock
that its stream still points at the file on disk (device and inode) and reopens it if not.

Locks nest in this order, and no path inverts it, so no deadlock is possible:

1. ``Handler.lock``, an RLock that ``Handler.handle`` takes before ``emit``,
2. ``_audit_lock``, a non-reentrant ``threading.Lock``,
3. ``fcntl.flock`` on the sidecar fd.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import threading
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from app.services.audit import GENESIS_HASH, compute_integrity_hash

# Transport fields of the logger, not part of the event. Writer and verifier both strip them
# before hashing, so the chain matches byte for byte. Timestamp and request_id stay in the hash,
# so tampering with either must break verification.
_META_KEYS: frozenset[str] = frozenset({"integrity_hash", "level", "logger", "service", "category"})

SIDECAR_FILENAME = "audit.last-hash"


def _utc_iso_timestamp() -> str:
    """Match structlog's ``TimeStamper(fmt="iso", utc=True)`` output shape."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class AuditLogHandler(TimedRotatingFileHandler):
    """Audit-aware rotating handler that owns the HMAC chain."""

    def __init__(
        self,
        filename: str,
        when: str = "midnight",
        interval: int = 1,
        backupCount: int = 0,
        encoding: str | None = None,
        delay: bool = False,
        utc: bool = False,
    ) -> None:
        super().__init__(
            filename,
            when=when,
            interval=interval,
            backupCount=backupCount,
            encoding=encoding,
            delay=delay,
            utc=utc,
        )
        # Non-reentrant: every write path acquires it once and never re-enters.
        self._audit_lock = threading.Lock()
        self._chain_state: str = GENESIS_HASH
        # True: this handler may rotate the shared file and must keep its
        # stream pointed at the current one. False: it must never rotate and
        # never trust a cached stream across calls.
        self.rotation_owner: bool = True

        log_dir = Path(filename).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        self._sidecar_path = log_dir / SIDECAR_FILENAME
        # Touch + open so we can fcntl.flock the same fd across rotations.
        self._sidecar_path.touch(exist_ok=True)
        self._sidecar_fd: int | None = os.open(str(self._sidecar_path), os.O_RDWR)

    # -- chain recovery ------------------------------------------------------

    def init_chain_state(self) -> None:
        """Recover ``_chain_state`` on startup. Sidecar → tail-scan → genesis."""
        recovered = self._read_sidecar() or self._tail_scan_last_hash()
        self._chain_state = recovered or GENESIS_HASH

    def _read_sidecar(self) -> str | None:
        try:
            data = self._sidecar_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if len(data) == 64 and all(c in "0123456789abcdef" for c in data):
            return data
        return None

    def _tail_scan_last_hash(self) -> str | None:
        """Scan the tail of the active log for the last ``integrity_hash``."""
        try:
            with open(self.baseFilename, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 65536))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            return None

        last_hash: str | None = None
        for raw in tail.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            candidate = entry.get("integrity_hash")
            if isinstance(candidate, str) and len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate):
                last_hash = candidate
        return last_hash

    # -- emit / rollover -----------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        """
        Rollover if due, then hash, write and update the sidecar, all under one lock.

        Any I/O or rendering error re-raises, so the request returns 500. A silently dropped
        audit entry is worse than a visible failure of a security-critical action.
        """
        sidecar_fd = self._sidecar_fd
        if sidecar_fd is None:
            raise RuntimeError("AuditLogHandler used after close()")
        with self._audit_lock:
            fcntl.flock(sidecar_fd, fcntl.LOCK_EX)
            try:
                if self.shouldRollover(record):
                    self._do_rollover_locked()
                self._write_chained_locked(record)
            finally:
                fcntl.flock(sidecar_fd, fcntl.LOCK_UN)

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        """A non-owner never rotates the shared file."""
        if not self.rotation_owner:
            return False
        return bool(super().shouldRollover(record))

    def _do_rollover_locked(self) -> None:
        """
        Run stdlib rollover, ``fsync`` the parent dir, then write ``segment_start``,
        so a verifier that walks the directory can stitch the chain across rotated files.
        """
        # Delegate to stdlib so we keep its computed suffix
        # (``rolloverAt - interval``), the skip-if-target-exists guard, the
        # ``self.rotate()`` hook and ``getFilesToDelete`` retention sweep.
        super().doRollover()
        self._fsync_parent_dir()
        self._write_segment_start_locked()

    def _write_chained_locked(self, record: logging.LogRecord) -> None:
        rendered = self.format(record)  # JSON via structlog ProcessorFormatter
        try:
            parsed: dict[str, Any] = json.loads(rendered)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AuditLogHandler.format produced non-JSON output") from exc

        stripped = {k: v for k, v in parsed.items() if k not in _META_KEYS}
        # Two processes write this file. The sidecar, read under the flock of emit(), is the only
        # predecessor both can trust. _chain_state is only the fallback for an empty sidecar.
        previous_hash = self._read_sidecar() or self._chain_state
        integrity_hash = compute_integrity_hash(stripped, previous_hash)
        parsed["integrity_hash"] = integrity_hash
        line = json.dumps(parsed) + "\n"
        self._append_line_durable(line)
        self._atomic_write_sidecar(integrity_hash)
        self._chain_state = integrity_hash

    def _write_segment_start_locked(self) -> None:
        # Same predecessor source as _write_chained_locked: the sidecar read
        # under the lock, with the in-memory chain only as the fallback.
        last_hash = self._read_sidecar() or self._chain_state
        entry_data: dict[str, Any] = {
            "event": "segment_start",
            "previous_file_last_hash": last_hash,
            "timestamp": _utc_iso_timestamp(),
        }
        integrity_hash = compute_integrity_hash(entry_data, last_hash)
        full_entry: dict[str, Any] = {
            **entry_data,
            "integrity_hash": integrity_hash,
            "level": "info",
            "logger": "audit",
            "service": "clavis-backend",
            "category": "audit",
        }
        line = json.dumps(full_entry) + "\n"
        self._append_line_durable(line)
        self._atomic_write_sidecar(integrity_hash)
        self._chain_state = integrity_hash

    # -- low-level i/o -------------------------------------------------------

    def _append_line_durable(self, line: str) -> None:
        if not self.rotation_owner:
            # Never keep a stream across calls: the owner may have rotated the file
            # since the last write, and the line must land in the current file.
            with self._open() as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            return
        self._ensure_stream_current_locked()
        assert self.stream is not None
        self.stream.write(line)
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def _ensure_stream_current_locked(self) -> None:
        """Reopen the stream if it no longer points at ``baseFilename``.

        Only the owner calls this. It guards against the stream and the file on disk
        drifting apart in some way other than its own rollover.
        """
        if self.stream is None:
            self.stream = self._open()
            return
        stream_stat: os.stat_result | None
        path_stat: os.stat_result | None
        try:
            stream_stat = os.fstat(self.stream.fileno())
            path_stat = os.stat(self.baseFilename)
        except OSError:
            stream_stat = None
            path_stat = None
        if (
            stream_stat is None
            or path_stat is None
            or (stream_stat.st_dev, stream_stat.st_ino)
            != (
                path_stat.st_dev,
                path_stat.st_ino,
            )
        ):
            with contextlib.suppress(OSError):
                self.stream.close()
            self.stream = self._open()

    def _atomic_write_sidecar(self, integrity_hash: str) -> None:
        # Written in place, so the inode and the flock of emit() stay on the live file. os.replace()
        # would leave _sidecar_fd on an unlinked inode after the first write and silently break the
        # exclusion between processes. _audit_lock and the flock already make this the only writer,
        # so no atomic rename is needed.
        assert self._sidecar_fd is not None
        encoded = integrity_hash.encode("utf-8")
        os.lseek(self._sidecar_fd, 0, os.SEEK_SET)
        os.write(self._sidecar_fd, encoded)
        os.ftruncate(self._sidecar_fd, len(encoded))
        os.fsync(self._sidecar_fd)

    def _fsync_parent_dir(self) -> None:
        parent = Path(self.baseFilename).parent
        dir_fd = os.open(str(parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        try:
            if self._sidecar_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self._sidecar_fd)
                self._sidecar_fd = None
        finally:
            super().close()

    # -- test hook -----------------------------------------------------------

    def reset_chain_state(self) -> None:
        """Reset the in-memory chain to genesis. Test-only."""
        with self._audit_lock:
            self._chain_state = GENESIS_HASH


# Module-level handle so ``main.py`` can call ``init_audit_chain`` on lifespan
# startup without coupling to ``logging.py`` internals.
_handler_instance: AuditLogHandler | None = None


def register_handler(handler: AuditLogHandler) -> None:
    global _handler_instance
    _handler_instance = handler


def get_handler() -> AuditLogHandler | None:
    return _handler_instance


def init_audit_chain() -> None:
    """Recover the audit chain on startup. Call from FastAPI lifespan."""
    if _handler_instance is not None:
        _handler_instance.init_chain_state()
