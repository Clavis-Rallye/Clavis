#!/usr/bin/env python3
"""
Audit log integrity verification tool.

Verifies the HMAC-SHA256 chain of the JSON Lines audit log. Each entry's
``integrity_hash`` chains to the one before, starting from ``GENESIS_HASH``.
The argument is a file, verified as one chain, or a directory: the rotated
``audit.log.YYYY-MM-DD`` files in date order, then the active ``audit.log``,
as one chain. Each rotated file begins with a ``segment_start`` entry that
carries the last hash of the file before, and it is hashed like any other.

Writer and verifier both leave ``integrity_hash``, ``level``, ``logger``,
``service`` and ``category`` out of the hash. ``timestamp`` and ``request_id``
are hashed, so neither can be rewritten without breaking the chain.

Usage: ``python -m app.tools.verify_audit_log <log_file_or_dir>``. Exit code 0
for a valid chain, 2 for a wrong number of arguments, and 1 for anything else:
a broken chain, a missing path or an unexpected error.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

from app.services.audit import GENESIS_HASH, compute_integrity_hash

# The same strip set as ``app.audit_handler._META_KEYS``. It is copied, not
# imported, so that this CLI runs where the logging stack is not initialized.
# Keep both in sync by hand.
STRUCTLOG_FIELDS: frozenset[str] = frozenset({"integrity_hash", "level", "logger", "service", "category"})

# TimedRotatingFileHandler appends ``.YYYY-MM-DD``, and a numeric suffix when a
# rotation collides.
_ROTATED_SUFFIX_RE = re.compile(r"^audit\.log\.\d{4}-\d{2}-\d{2}(?:\.\d+)?$")


def parse_audit_entry(line: str, line_number: int, source: str) -> dict[str, Any] | None:
    try:
        return json.loads(line)  # type: ignore[no-any-return]
    except json.JSONDecodeError as e:
        print(f"ERROR in {source} at line {line_number}: Invalid JSON - {e}", file=sys.stderr)
        return None


def extract_entry_data(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop the writer's transport meta-fields so the hash input matches the writer."""
    return {k: v for k, v in entry.items() if k not in STRUCTLOG_FIELDS}


def _resolve_chain_files(path: Path) -> list[Path]:
    """Return the files to verify, in chain order."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []

    rotated = sorted(p for p in path.iterdir() if p.is_file() and _ROTATED_SUFFIX_RE.match(p.name))
    active = path / "audit.log"
    chain = list(rotated)
    if active.exists():
        chain.append(active)
    return chain


def _verify_file(
    file_path: Path,
    starting_hash: str,
) -> tuple[bool, str, int]:
    """Verify one file of the chain and return ``(ok, last_hash, entries_verified)``.

    On failure ``last_hash`` is the last hash that verified.
    """
    previous_hash = starting_hash
    entries_verified = 0
    line_number = 0

    try:
        with open(file_path, encoding="utf-8") as f:
            for raw in f:
                line_number += 1
                line = raw.strip()
                if not line:
                    continue

                entry = parse_audit_entry(line, line_number, str(file_path))
                if entry is None:
                    return False, previous_hash, entries_verified

                if "integrity_hash" not in entry:
                    print(
                        f"WARNING in {file_path} at line {line_number}: Entry missing 'integrity_hash' field, skipping",
                        file=sys.stderr,
                    )
                    continue

                stored_hash = entry["integrity_hash"]
                entry_data = extract_entry_data(entry)
                expected_hash = compute_integrity_hash(entry_data, previous_hash)

                if expected_hash != stored_hash:
                    print("\n" + "=" * 80, file=sys.stderr)
                    print("INTEGRITY VIOLATION DETECTED", file=sys.stderr)
                    print("=" * 80, file=sys.stderr)
                    print(f"File:          {file_path}", file=sys.stderr)
                    print(f"Line number:   {line_number}", file=sys.stderr)
                    print(f"Event:         {entry.get('event', 'unknown')}", file=sys.stderr)
                    print(f"Timestamp:     {entry.get('timestamp', 'unknown')}", file=sys.stderr)
                    print(f"\nExpected hash: {expected_hash}", file=sys.stderr)
                    print(f"Stored hash:   {stored_hash}", file=sys.stderr)
                    print(f"Previous hash: {previous_hash}", file=sys.stderr)
                    print("\nThe audit log has been tampered with or corrupted.", file=sys.stderr)
                    print("=" * 80 + "\n", file=sys.stderr)
                    return False, previous_hash, entries_verified

                previous_hash = stored_hash
                entries_verified += 1
    except OSError as e:
        print(f"\nERROR reading {file_path}: {e}", file=sys.stderr)
        return False, previous_hash, entries_verified

    return True, previous_hash, entries_verified


def verify_audit_log(log_path: Path) -> bool:
    """Verify a single file or a whole directory of rotated audit logs."""
    if not log_path.exists():
        print(f"ERROR: File not found: {log_path}", file=sys.stderr)
        return False

    files = _resolve_chain_files(log_path)
    if not files:
        print(f"ERROR: No audit log files at {log_path}", file=sys.stderr)
        return False

    if log_path.is_dir():
        print(f"Verifying audit log directory: {log_path}")
        print(f"Files (chain order): {[f.name for f in files]}")
    else:
        print(f"Verifying audit log: {log_path}")
    print(f"Genesis hash: {GENESIS_HASH}\n")

    previous_hash = GENESIS_HASH
    total_entries = 0
    for file_path in files:
        ok, previous_hash, count = _verify_file(file_path, previous_hash)
        total_entries += count
        if not ok:
            return False

    print(f"\n{'=' * 80}")
    print("CHAIN VALID")
    print("=" * 80)
    print(f"Total entries verified: {total_entries}")
    print(f"Final integrity hash:   {previous_hash}")
    print("=" * 80 + "\n")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m app.tools.verify_audit_log <log_file_or_dir>", file=sys.stderr)
        print("\nExample:", file=sys.stderr)
        print("  python -m app.tools.verify_audit_log /var/log/clavis", file=sys.stderr)
        return 2

    log_path = Path(sys.argv[1])
    try:
        return 0 if verify_audit_log(log_path) else 1
    except Exception as e:
        print(f"\nUNEXPECTED ERROR: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
