"""
Admin endpoints — audit log viewer.

Provides read access to audit and application logs for org_admin users.
All endpoints enforce org_admin role and filter logs by the requesting
user's organization (tenant isolation).

Route prefix: /api/orgs/{slug}/admin
"""

import csv
import gzip
import heapq
import io
import json
import re
import zlib
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import IO, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import get_org_from_slug
from app.auth.rbac import require_role
from app.client_address import client_address
from app.config import get_settings
from app.database import get_db
from app.exceptions import CodedHTTPException
from app.models.organization import Organization
from app.models.user import User
from app.routers.schemas import (
    LogEntryResponse,
    LogListResponse,
    MailStatusResponse,
    MailTestRequest,
    MailTestResponse,
    MediaPurgeRequest,
    MediaPurgeResponse,
    MediaSummaryResponse,
    RetentionConfigResponse,
    RetentionConfigUpdate,
    RetentionPeriodInfo,
)
from app.services import mail
from app.services.audit import actor_from_user, record_audit_event
from app.services.backup_retention import effective_backup_retention
from app.services.gdpr import DeletionClass
from app.services.media_purge import count_org_media, purge_org_media
from app.services.retention_config import (
    CONFIGURABLE_CLASSES,
    RETENTION_MAX_DAYS,
    RetentionValidationError,
    apply_org_overrides,
    effective_retention_days,
    resolve_source,
    retention_min_days,
)

router = APIRouter()

_CATEGORY_FILES = {
    "audit": "audit.log",
    "app": "application.log",
    "error": "error.log",
    "access": "access.log",
}

_ALL_CATEGORIES = list(_CATEGORY_FILES.keys())


def get_log_dir() -> str:
    """Return the configured log directory path."""
    return get_settings().logging.log_dir


def _parse_log_line(line: str) -> dict[str, Any] | None:
    """Parse a single JSON log line; return None if malformed."""
    line = line.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except json.JSONDecodeError:
        return None


def _parse_timestamp(raw: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp string to an aware datetime. Returns None on failure."""
    if raw is None:
        return None
    try:
        ts_str = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def _to_aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (assume UTC if naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# Daily rotation (``when="midnight"`` in ``app/logging.py``) renames the active
# file to ``<name>.YYYY-MM-DD``, and ``deployment/scripts/log-cleanup.sh`` later
# compresses it to ``<name>.YYYY-MM-DD.gz``. Only these two suffixes count as a
# rotated sibling, so a stray ``audit.log.bak`` or ``audit.last-hash`` never mixes in.
_ROTATED_SUFFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})(\.gz)?$")


def _category_files(
    log_path: Path, file_name: str, since: datetime | None, until: datetime | None = None
) -> list[Path]:
    """The rotated siblings of one category file, oldest first, then the active file.

    A rotated file is plain or gzip-compressed. A file named for a day well
    before ``since`` or well after ``until`` cannot hold a matching entry and
    is skipped. One day of slack on each side covers entries written around
    midnight UTC, before the first write of the day triggered the rotation.

    While ``log-cleanup.sh`` compresses a day, both files exist for a moment:
    gzip writes the ``.gz`` first and removes the plain file last. The plain
    file is then the complete one, so it wins.
    """
    since_day = _to_aware(since).astimezone(UTC).date() - timedelta(days=1) if since else None
    until_day = _to_aware(until).astimezone(UTC).date() + timedelta(days=1) if until else None
    by_day: dict[date, Path] = {}
    for candidate in log_path.glob(f"{file_name}.*"):
        match = _ROTATED_SUFFIX.match(candidate.name[len(file_name) + 1 :])
        if match is None:
            continue
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if since_day is not None and day < since_day:
            continue
        if until_day is not None and day > until_day:
            continue
        if match.group(2) is None or day not in by_day:
            by_day[day] = candidate
    return [*(by_day[day] for day in sorted(by_day)), log_path / file_name]


def _open_log_file(path: Path) -> IO[str]:
    """Open a log file as text, decompressing a ``.gz`` file on the fly."""
    if path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open(encoding="utf-8", errors="replace")


def _iter_category_entries(
    log_path: Path, category: str, since: datetime | None, until: datetime | None
) -> Iterator[dict[str, Any]]:
    """Yield the parsed entries of one category, oldest file first, line by line.

    Nothing is held beyond the current line, so a year of compressed files
    costs no more memory than one entry. A missing or unreadable file is
    skipped. A truncated ``.gz`` (compression still running) yields what it
    holds up to the break.
    """
    file_name = _CATEGORY_FILES.get(category)
    if file_name is None:
        return
    for candidate in _category_files(log_path, file_name, since, until):
        file_path = candidate.resolve()
        # A symlink must not lead out of log_dir.
        if not file_path.is_relative_to(log_path):
            continue
        if not file_path.exists():
            continue
        try:
            with _open_log_file(file_path) as fh:
                for line in fh:
                    parsed = _parse_log_line(line)
                    if parsed is not None:
                        if "category" not in parsed:
                            parsed["category"] = category
                        yield parsed
        except (OSError, EOFError, zlib.error):
            continue


def _iter_log_entries(
    log_dir: str, categories: list[str], since: datetime | None = None, until: datetime | None = None
) -> Iterator[dict[str, Any]]:
    """Yield the entries of every requested category, one category after the other."""
    log_path = Path(log_dir).resolve()
    for category in categories:
        yield from _iter_category_entries(log_path, category, since, until)


def _read_log_entries(log_dir: str, categories: list[str], since: datetime | None = None) -> list[dict[str, Any]]:
    """
    Read and parse all log entries from the requested category files.

    Each category is its active file plus every rotated ``.YYYY-MM-DD`` and
    ``.YYYY-MM-DD.gz`` sibling. Reading only the active file would lose every
    event before the last midnight rotation, although the files keep 365 days.
    Skipping the compressed files would lose every event older than the
    compression threshold of ``log-cleanup.sh``. ``since`` lets a caller with a
    date filter skip older files.

    The organization log streams through ``_iter_log_entries`` instead. This
    list form serves the operator log (``routers/instance.py``).

    The log_dir is the value returned by get_log_dir() (server configuration),
    not user-supplied. Missing or unreadable files are silently skipped.
    """
    return list(_iter_log_entries(log_dir, categories, since=since))


def _entry_matches(
    entry: dict[str, Any],
    organization_id: str,
    from_aware: datetime | None,
    to_aware: datetime | None,
    search_lower: str | None,
    event_type: str | None,
    search_events: frozenset[str] = frozenset(),
) -> bool:
    """One entry against tenant isolation and the query filters."""
    # Tenant isolation: only include entries for this organization. Entries with no
    # organization_id are excluded — application/error/access logs without org context
    # would otherwise leak across tenants. Only org-stamped entries are returned.
    entry_org = entry.get("organization_id")
    if entry_org is None or str(entry_org) != organization_id:
        return False

    if from_aware is not None or to_aware is not None:
        entry_dt = _parse_timestamp(entry.get("timestamp"))
        if entry_dt is not None:
            if from_aware is not None and entry_dt < from_aware:
                return False
            if to_aware is not None and entry_dt > to_aware:
                return False

    if event_type is not None and entry.get("event") != event_type:
        return False

    # Full-text search across event, actor, target and request id. The log
    # view sends its search field here, so the view and its CSV export match
    # the same entries. The event labels exist only in the interface, so the
    # view also sends the keys of the events whose label matches
    # (search_events): "Bereinig" finds org.media_purged.
    if search_lower:
        haystacks = (
            entry.get("event", ""),
            entry.get("actor", ""),
            entry.get("target", ""),
            entry.get("request_id", ""),
        )
        if entry.get("event") not in search_events and not any(
            search_lower in str(value).lower() for value in haystacks
        ):
            return False

    return True


def _iter_filtered(
    entries: Iterable[dict[str, Any]],
    organization_id: str,
    from_date: datetime | None,
    to_date: datetime | None,
    search: str | None,
    event_type: str | None,
    search_events: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Apply tenant isolation and query filters lazily, one entry at a time."""
    # The bounds are normalized once, before the loop.
    from_aware = _to_aware(from_date) if from_date is not None else None
    to_aware = _to_aware(to_date) if to_date is not None else None
    search_lower = search.lower() if search else None
    event_keys = frozenset(search_events or ())
    for entry in entries:
        if _entry_matches(entry, organization_id, from_aware, to_aware, search_lower, event_type, event_keys):
            yield entry


def _sort_key(e: dict[str, Any]) -> datetime:
    """Parse the entry timestamp for sorting; fall back to epoch on failure."""
    dt = _parse_timestamp(e.get("timestamp"))
    return dt if dt is not None else datetime.fromtimestamp(0, tz=UTC)


# The audit entry of a media purge holds the kind of media and the count as its
# own keys, beside actor and target (see ``purge_media``). Only that event
# passes them on, so a key of the same name in another log line stays inside.
_MEDIA_PURGE_EVENT = "org.media_purged"


def _entry_to_response(entry: dict[str, Any]) -> LogEntryResponse:
    """Convert a raw log dict to a LogEntryResponse."""
    media_type = purged = None
    if entry.get("event") == _MEDIA_PURGE_EVENT:
        media_type = entry.get("media_type")
        purged = entry.get("purged")
    return LogEntryResponse(
        timestamp=_parse_timestamp(entry.get("timestamp")),
        category=entry.get("category"),
        event=entry.get("event"),
        actor=entry.get("actor") if isinstance(entry.get("actor"), dict) else None,
        target=entry.get("target") if isinstance(entry.get("target"), dict) else None,
        changes=entry.get("changes") if isinstance(entry.get("changes"), dict) else None,
        media_type=media_type if isinstance(media_type, str) else None,
        # bool is an int in Python, and "true files" is no count.
        purged=purged if isinstance(purged, int) and not isinstance(purged, bool) else None,
        request_id=entry.get("request_id"),
        integrity_hash=entry.get("integrity_hash"),
        organization_id=entry.get("organization_id"),
    )


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> str:
    """Coerce a value to a CSV-safe string and neutralize formula-injection prefixes.

    Excel/LibreOffice/Sheets interpret cells starting with =, +, -, @, TAB, or CR as
    formulas, which can exfiltrate data or run shell commands when an admin opens the
    exported log. Prefix such cells with a single quote so the spreadsheet treats them
    as text.
    """
    if value is None or value == "":
        return ""
    s = str(value)
    if s and s[0] in _CSV_FORMULA_PREFIXES:
        return "'" + s
    return s


_CSV_FIELDS = [
    "timestamp",
    "category",
    "event",
    "actor",
    "target",
    "changes",
    "request_id",
    "integrity_hash",
    "organization_id",
]

# Rows per chunk of the streamed export. Small enough to start the download at
# once, large enough to keep the per-chunk overhead low.
_CSV_ROWS_PER_CHUNK = 500


def _csv_row(entry: dict[str, Any]) -> dict[str, str]:
    return {
        "timestamp": _csv_safe(entry.get("timestamp")),
        "category": _csv_safe(entry.get("category")),
        "event": _csv_safe(entry.get("event")),
        "actor": _csv_safe(json.dumps(entry.get("actor"))) if entry.get("actor") is not None else "",
        "target": _csv_safe(json.dumps(entry.get("target"))) if entry.get("target") is not None else "",
        "changes": _csv_safe(json.dumps(entry.get("changes"))) if entry.get("changes") is not None else "",
        "request_id": _csv_safe(entry.get("request_id")),
        "integrity_hash": _csv_safe(entry.get("integrity_hash")),
        "organization_id": _csv_safe(entry.get("organization_id")),
    }


def _iter_csv(entries: Iterable[dict[str, Any]]) -> Iterator[str]:
    """Serialize log entries to CSV, the header first, then chunks of rows."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for rows, entry in enumerate(entries, start=1):
        writer.writerow(_csv_row(entry))
        if rows % _CSV_ROWS_PER_CHUNK == 0:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
    yield buf.getvalue()


def _iter_export(
    log_dir: str,
    categories: list[str],
    organization_id: str,
    from_date: datetime | None,
    to_date: datetime | None,
    search: str | None,
    event_type: str | None,
    search_events: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Every matching entry of the period, oldest first, across the categories.

    Each category file is written in time order, so a lazy merge of the
    categories gives one time line without loading a file into memory. Oldest
    first also keeps the audit entries in the order of their integrity chain.
    """
    per_category = [
        _iter_filtered(
            _iter_log_entries(log_dir, [category], since=from_date, until=to_date),
            organization_id,
            from_date,
            to_date,
            search,
            event_type,
            search_events,
        )
        for category in categories
    ]
    return heapq.merge(*per_category, key=_sort_key)


def _newest_page(
    log_dir: str,
    categories: list[str],
    organization_id: str,
    from_date: datetime | None,
    to_date: datetime | None,
    search: str | None,
    event_type: str | None,
    skip: int,
    limit: int,
    order: Literal["desc", "asc"],
    search_events: list[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """One page of matching entries and the number of all matches.

    The scan streams over every file of the period and keeps only the
    ``skip + limit`` entries the page needs, not the whole period.
    """
    total = 0

    def counted(entries: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        nonlocal total
        for entry in entries:
            total += 1
            yield entry

    matches = counted(
        _iter_filtered(
            _iter_log_entries(log_dir, categories, since=from_date, until=to_date),
            organization_id,
            from_date,
            to_date,
            search,
            event_type,
            search_events,
        )
    )
    pick = heapq.nlargest if order == "desc" else heapq.nsmallest
    window = pick(skip + limit, matches, key=_sort_key)
    return window[skip : skip + limit], total


@router.get(
    "/logs",
    response_model=LogListResponse,
    responses={
        200: {
            "description": (
                "Paginated log entries as JSON, or with format=csv every matching entry "
                "of the period as a streamed CSV file, oldest first"
            ),
            "content": {"text/csv": {}},
        },
        401: {"description": "Not authenticated"},
        403: {"description": "Requires org_admin role"},
    },
)
async def list_logs(
    category: list[str] | None = Query(None, description="Log categories to include (audit, app, error, access)"),
    from_date: datetime | None = Query(None, description="Filter entries after this ISO 8601 datetime"),
    to_date: datetime | None = Query(None, description="Filter entries before this ISO 8601 datetime"),
    search: str | None = Query(
        None, description="Case-insensitive text search across event, actor, target and request id"
    ),
    search_event: list[str] | None = Query(
        None,
        description=(
            "Event keys whose label matches the search, sent by the log view. "
            "An entry with one of these events matches the search as well"
        ),
    ),
    event_type: str | None = Query(None, description="Filter by exact event type (e.g., user_login)"),
    skip: int = Query(0, ge=0, description="Pagination offset (JSON only)"),
    limit: int = Query(50, ge=1, le=200, description="Maximum entries to return (JSON only)"),
    order: Literal["desc", "asc"] = Query("desc", description="Sort by timestamp, newest first by default (JSON only)"),
    format: str = Query("json", pattern="^(json|csv)$", description="Response format: json or csv"),
    _db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("org_admin")),
    log_dir: str = Depends(get_log_dir),
) -> Response | LogListResponse:
    """Search the audit and application logs of this organization.

    Open to organization admins. Only entries stamped with the organization are
    returned, so no log line of another organization and none without an
    organization appears. Filters: category, date range, event type and free
    text.

    JSON returns one page sorted by timestamp. With `format=csv` the response
    is a streamed file with every matching entry of the period, oldest first,
    and `skip`, `limit` and `order` are ignored. A CSV cell that starts with a
    formula character gets a leading single quote.
    """
    categories = category if category else _ALL_CATEGORIES
    organization_id = str(org.id)

    if format == "csv":
        entries = _iter_export(
            log_dir, categories, organization_id, from_date, to_date, search, event_type, search_event
        )
        # A sync iterator: Starlette pulls it in a worker thread, so reading
        # and decompressing the files never blocks the event loop.
        return StreamingResponse(
            _iter_csv(entries),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename=logs-{org.slug}.csv"},
        )

    page, total = await run_in_threadpool(
        _newest_page,
        log_dir,
        categories,
        organization_id,
        from_date,
        to_date,
        search,
        event_type,
        skip,
        limit,
        order,
        search_event,
    )

    return LogListResponse(
        items=[_entry_to_response(e) for e in page],
        total=total,
        skip=skip,
        limit=limit,
    )


# German descriptions for the configurable Löschklassen (org-admin facing, Sie-Form
# not needed — these are noun labels). Kept here alongside the endpoint they serve.
_RETENTION_DESCRIPTIONS = {
    DeletionClass.LK_A: "Gastkonten & Gast-Anzeigenamen (nach Lauf-Ende)",
    DeletionClass.LK_B: "Chat-Nachrichten & hochgeladene Medien (nach Lauf-Ende)",
    DeletionClass.LK_C: "Teamnamen & Anzeigenamen in den Teams (nach Lauf-Ende)",
    DeletionClass.LK_H: "Text- und Quiz-Antworten, Kommentare & Rückmeldungen (nach Lauf-Ende)",
}


def _refuse_unreadable_settings(org: Organization) -> None:
    """Answer 409 for an organization whose stored settings cannot be read.

    Such settings have no effective period: the sweep leaves the organization
    out. The page must not show the defaults as if they applied, and a save
    would merge into the marker and write it back.
    """
    if org.settings_unreadable:
        raise CodedHTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The organization's stored settings cannot be read and must be replaced whole "
            "(PUT /api/orgs/{slug} with settings). No retention period applies until then.",
            error_code="ORG_SETTINGS_UNREADABLE",
        )


async def _build_retention_response(org: Organization, db: AsyncSession) -> RetentionConfigResponse:
    _refuse_unreadable_settings(org)
    backup_days, backup_source = await effective_backup_retention(db)
    log_settings = get_settings().logging
    periods = [
        RetentionPeriodInfo(
            deletion_class=cast(Literal["LK_A", "LK_B", "LK_C", "LK_H"], dc.value),
            days=effective_retention_days(dc, org.readable_settings),
            source=resolve_source(org.readable_settings, dc),
            description=_RETENTION_DESCRIPTIONS[dc],
        )
        for dc in CONFIGURABLE_CLASSES
    ]
    return RetentionConfigResponse(
        periods=periods,
        min_days=retention_min_days(),
        max_days=RETENTION_MAX_DAYS,
        backup_retention_days=backup_days,
        backup_retention_source=backup_source,
        technical_log_days=max(
            log_settings.app_log_retention_days,
            log_settings.error_log_retention_days,
            log_settings.access_log_retention_days,
        ),
        audit_log_days=log_settings.audit_log_retention_days,
        abandoned_run_days=get_settings().retention.abandoned_run_days,
        result_link_days=get_settings().retention.result_link_days,
    )


@router.get(
    "/retention",
    response_model=RetentionConfigResponse,
    responses={
        200: {"description": "Effective retention periods for this organization"},
        401: {"description": "Not authenticated"},
        403: {"description": "Requires org_admin role"},
        409: {"description": "The organization's stored settings cannot be read (ORG_SETTINGS_UNREADABLE)"},
    },
)
async def get_retention_config(
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("org_admin")),
) -> RetentionConfigResponse:
    """Return the effective retention periods of this organization and their bounds.

    Open to organization admins. Each period of the classes LK_A, LK_B, LK_C
    and LK_H is the override of the organization or else the default, with its
    source. A period may not be shorter than the backup rotation window, and
    not longer than 3650 days. The response also carries the backup and log
    retention of the instance.
    """
    return await _build_retention_response(org, db)


@router.put(
    "/retention",
    response_model=RetentionConfigResponse,
    responses={
        200: {"description": "Updated retention periods"},
        401: {"description": "Not authenticated"},
        403: {"description": "Requires org_admin role"},
        409: {"description": "The organization's stored settings cannot be read (ORG_SETTINGS_UNREADABLE)"},
        422: {"description": "A period is out of bounds (below backup rotation or above the ceiling)"},
    },
)
async def update_retention_config(
    request: Request,
    body: RetentionConfigUpdate,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> RetentionConfigResponse:
    """Set the retention overrides of this organization.

    Open to organization admins. `lk_a_days`, `lk_b_days` and `lk_c_days` are
    required. Each is the override in days, or `null` to return that class to
    the default. `lk_h_days` works the same, and an omitted `lk_h_days` stays
    as it was.

    A period must not be shorter than the backup rotation window, because
    deleted data would stay recoverable from backups, and not longer than 3650
    days. Otherwise the answer is 422 and nothing changes.

    Emits the audit event `retention_config_changed` with the old and new days
    of each changed class, only when a period changed.
    """
    overrides = {
        DeletionClass.LK_A: body.lk_a_days,
        DeletionClass.LK_B: body.lk_b_days,
        DeletionClass.LK_C: body.lk_c_days,
    }
    if "lk_h_days" in body.model_fields_set:
        overrides[DeletionClass.LK_H] = body.lk_h_days

    _refuse_unreadable_settings(org)

    # The before-values for the audit diff.
    before = {dc.value: effective_retention_days(dc, org.readable_settings) for dc in CONFIGURABLE_CLASSES}

    try:
        new_settings = apply_org_overrides(org.readable_settings, overrides)
    except RetentionValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    # Reassign (not mutate) so SQLAlchemy marks the JSONB column dirty.
    org.settings = new_settings
    await db.commit()
    await db.refresh(org)

    after = {dc.value: effective_retention_days(dc, org.readable_settings) for dc in CONFIGURABLE_CLASSES}
    changes = {cls: {"from": before[cls], "to": after[cls]} for cls in before if before[cls] != after[cls]}
    if changes:
        record_audit_event(
            "retention_config_changed",
            actor=actor_from_user(current_user, client_address(request)),
            organization_id=org.id,
            changes=changes,
        )

    return await _build_retention_response(org, db)


@router.get(
    "/mail/status",
    response_model=MailStatusResponse,
    responses={
        200: {"description": "Whether this deployment can send mail"},
        403: {"description": "Insufficient permissions (requires org_admin)"},
    },
)
async def mail_status(
    _: User = Depends(require_role("org_admin")),
) -> MailStatusResponse:
    """Report whether this deployment can send mail.

    Open to organization admins. A client uses it to say that mail is not set up
    instead of offering an invitation that nobody receives. When mail is on, the
    response also names the sender address and the reply-to address.
    """
    settings = get_settings().mail
    if not settings.enabled:
        return MailStatusResponse(enabled=False)
    return MailStatusResponse(
        enabled=True,
        from_address=settings.mail_from_address,
        reply_to=settings.mail_reply_to or None,
    )


@router.post(
    "/mail/test",
    response_model=MailTestResponse,
    responses={
        200: {"description": "Probe finished — check `success` for the outcome"},
        403: {"description": "Insufficient permissions (requires org_admin)"},
    },
)
async def mail_test(
    request: Request,
    body: MailTestRequest,
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> MailTestResponse:
    """Send a test mail to one address and report the outcome.

    Open to organization admins. Unlike every other mail of the application,
    this one is sent before the response, and the error of the mail server is
    returned, so an admin can find a wrong mail setup.

    A failure is still HTTP 200 with `success: false`, and `error` holds the
    cause. The same holds when mail is not configured on the deployment.

    Emits the audit event `mail_test_sent` with the recipient and the result
    `sent` or `failed`. A deployment without mail emits none.
    """
    if not mail.is_enabled():
        return MailTestResponse(
            success=False,
            message="Email delivery is not configured on this deployment.",
            error="SMTP_HOST is empty",
        )

    message = mail.compose_test(
        to=body.email,
        language=body.language,
        organization_name=org.name,
        triggered_by=current_user.display_name,
    )

    try:
        await mail.send_now(message)
    except mail.MailTransportError as exc:
        record_audit_event(
            event="mail_test_sent",
            actor=actor_from_user(current_user, client_address(request)),
            target={"entity": "mail", "recipient": body.email},
            changes={"result": {"from": None, "to": "failed"}},
            organization_id=org.id,
        )
        return MailTestResponse(
            success=False,
            message="The mail server refused the message.",
            error=str(exc),
        )

    record_audit_event(
        event="mail_test_sent",
        actor=actor_from_user(current_user, client_address(request)),
        target={"entity": "mail", "recipient": body.email},
        changes={"result": {"from": None, "to": "sent"}},
        organization_id=org.id,
    )
    return MailTestResponse(success=True, message=f"Test message sent to {body.email}.")


@router.get(
    "/media/summary",
    response_model=MediaSummaryResponse,
    responses={
        200: {"description": "Stored participant media counts per type"},
        401: {"description": "Not authenticated"},
        403: {"description": "Requires org_admin role"},
    },
)
async def media_summary(
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    _current_user: User = Depends(require_role("org_admin")),
) -> MediaSummaryResponse:
    """Count the stored participant photos, videos and audio files of this organization.

    Open to organization admins. Each number is what a purge of that type would
    delete: the distinct media files that the submissions of the organization
    reference.
    """
    counts = await count_org_media(db, org.id)
    return MediaSummaryResponse(**counts)


@router.post(
    "/media/purge",
    response_model=MediaPurgeResponse,
    responses={
        200: {"description": "Every medium of the type is deleted; rows and scores stay"},
        401: {"description": "Not authenticated"},
        403: {"description": "Requires org_admin role"},
        422: {
            "description": "confirm_slug does not match the organization's slug (error_code CONFIRMATION_MISMATCH)",
        },
    },
)
async def purge_media(
    request: Request,
    body: MediaPurgeRequest,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(get_org_from_slug),
    current_user: User = Depends(require_role("org_admin")),
) -> MediaPurgeResponse:
    """Delete every participant medium of one type in this organization.

    Open to organization admins. `confirm_slug` must be the slug of the
    organization. Otherwise the answer is 422 `CONFIRMATION_MISMATCH` and
    nothing is deleted.

    The purge deletes each file of the type, the WebP variants of a photo
    included. It removes the reference from every submission that carried the
    file and marks the submission as `media_removed`, so the views show "Medium
    entfernt". Submissions and scores stay. The response gives the number of
    media deleted, without their WebP variants.

    Emits the audit event `org.media_purged` with the type and the count.
    """
    if body.confirm_slug != org.slug:
        raise CodedHTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Confirmation slug does not match the organization's slug",
            error_code="CONFIRMATION_MISMATCH",
        )

    purged = await purge_org_media(db, org.id, body.type)
    await db.commit()

    record_audit_event(
        event=_MEDIA_PURGE_EVENT,
        actor=actor_from_user(current_user, client_address(request)),
        target={"entity": "organization", "id": str(org.id), "slug": org.slug},
        organization_id=org.id,
        media_type=body.type,
        purged=purged,
    )
    return MediaPurgeResponse(type=body.type, purged=purged)
