"""The retention tasks: each deletes or anonymizes one kind of data once its period is over.

The deletion classes are defined in ``app/services/gdpr.py``. The daily
``RetentionSweeper`` calls the tasks through ``retention_sweep_once``. Each task:

- takes ``(session, reference_date)``. ``reference_date`` defaults to now and
  lets a test set the date.
- writes in the caller's transaction and does not commit.
- is idempotent: a second pass over the same state changes nothing.
- tolerates a missing storage object, and one object's failure never aborts
  the sweep.
- returns a count and emits a ``retention_swept`` audit event when it acted.

The sweeps that start at the end of a run (LK_A, LK_B, LK_C, LK_H) resolve the
period per organization in ``ended_due_run_ids``: the organization's override,
then the instance setting, then the ``DeletionClass`` constant
(``retention_config``). The only other period is
``settings.retention.abandoned_run_days``.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from minio.error import S3Error
from sqlalchemy import (
    Boolean,
    ColumnElement,
    Text,
    and_,
    cast,
    delete,
    false,
    func,
    literal_column,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.chat_message import ChatMessage
from app.models.feed_event import FeedEvent, FeedEventType
from app.models.feedback import Feedback
from app.models.organization import Organization
from app.models.run import Run, RunState
from app.models.run_result_link import RunResultLink
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.station import Station
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from app.models.user_invitation import UserInvitation
from app.repositories.feed_event import FeedEventRepository
from app.repositories.run import lock_run_ids
from app.repositories.run_result_link import RunResultLinkRepository
from app.repositories.team import id_chunks, lock_team_ids
from app.routers.chat import help_request_feed_content
from app.services.audit import audit_retention_swept
from app.services.gdpr import (
    ANONYMIZED_EMAIL_PREFIX,
    ANONYMIZED_EMAIL_SUFFIX,
    ANONYMIZED_MEMBER_NAME,
    SYSTEM_RETENTION_ACTOR,
    DeletionClass,
)
from app.services.guest_cleanup import blank_guest_member_names, delete_run_guests
from app.services.retention_config import (
    RETENTION_MAX_DAYS,
    instance_default_days,
    override_in_bounds,
    retention_min_days,
    settings_unreadable,
    stored_override,
)
from app.storage import delete_file, list_all_files

logger = structlog.get_logger(__name__)

# The (organization, class) pairs warned about in this sweep, so that an
# out-of-range override warns once per sweep and not once per read (a sweep
# reads LK_B twice, for media and chat). ``retention_sweep_once`` opens the
# scope with ``override_warning_scope``. Outside a scope every read warns.
# Unreadable settings share the set, with ``_SETTINGS_UNREADABLE`` as the class.
_warned_overrides: ContextVar[set[tuple[str, str]] | None] = ContextVar("retention_warned_overrides", default=None)
# Unreadable settings concern every class, and no class has this value.
_SETTINGS_UNREADABLE = "settings_unreadable"


@contextmanager
def override_warning_scope() -> Iterator[set[tuple[str, str]]]:
    """Open the per-sweep set of already warned (organization, class) pairs."""
    warned: set[tuple[str, str]] = set()
    token = _warned_overrides.set(warned)
    try:
        yield warned
    finally:
        _warned_overrides.reset(token)


def _warn_override_out_of_range(org_id: str, deletion_class: DeletionClass, value: int) -> None:
    """Log a stored out-of-range override once per sweep, not once per read."""
    warned = _warned_overrides.get()
    if warned is not None:
        key = (org_id, deletion_class.value)
        if key in warned:
            return
        warned.add(key)
    logger.warning(
        "retention_override_out_of_range",
        org_id=org_id,
        deletion_class=deletion_class.value,
        value=value,
        min_days=retention_min_days(),
        max_days=RETENTION_MAX_DAYS,
    )


def _warn_settings_unreadable(org_id: str, error: str) -> None:
    """Log an organization's unreadable settings once per sweep, not once per read."""
    warned = _warned_overrides.get()
    if warned is not None:
        key = (org_id, _SETTINGS_UNREADABLE)
        if key in warned:
            return
        warned.add(key)
    logger.warning("retention_settings_unreadable", org_id=org_id, error=error)


# The submission content fields that hold storage keys, one key or a list. Every
# caller that asks which keys a submission references reads them here. No
# validator writes ``media_urls``, but the submission model documents it for a
# creative answer, so it counts.
SUBMISSION_MEDIA_SCALAR_FIELDS = ("photo_url", "audio_url", "video_url")
SUBMISSION_MEDIA_LIST_FIELDS = ("media_urls",)

# Content fields that are neither media nor an answer: markers a view reads.
# ``media_removed`` is set by the org-admin purge (``media_purge.MEDIA_REMOVED_FLAG``)
# and makes the views say "Medium entfernt". It names no person, so the LK_H
# sweep keeps it. A test holds the two spellings together.
SUBMISSION_MARKER_FIELDS = ("media_removed",)


def submission_media_keys(content: dict[str, Any]) -> list[str]:
    """Return every non-empty storage key in a submission's content.

    The order (scalars, then list entries) is stable, but no caller relies on it.
    """
    keys: list[str] = []
    for field in SUBMISSION_MEDIA_SCALAR_FIELDS:
        value = content.get(field)
        if isinstance(value, str) and value.strip():
            keys.append(value)
    for field in SUBMISSION_MEDIA_LIST_FIELDS:
        value = content.get(field)
        if isinstance(value, list):
            keys.extend(v for v in value if isinstance(v, str) and v.strip())
    return keys


def clear_submission_media_keys(content: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``content`` without the media fields.

    The answer fields stay, so an anonymized submission keeps the shape of its answer.
    """
    drop = set(SUBMISSION_MEDIA_SCALAR_FIELDS) | set(SUBMISSION_MEDIA_LIST_FIELDS)
    return {k: v for k, v in content.items() if k not in drop}


def clear_submission_answer_keys(content: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``content`` with only the media and marker fields, for the LK_H sweep.

    The answer goes (``text``, ``selected_option``, a scanned code, any field a
    later answer type adds). The media keys must stay: an organization may keep
    media longer than answer text, and without its key the orphan sweep would
    delete the object seven days later, before its own period ends.
    """
    keep = set(SUBMISSION_MEDIA_SCALAR_FIELDS) | set(SUBMISSION_MEDIA_LIST_FIELDS) | set(SUBMISSION_MARKER_FIELDS)
    return {k: v for k, v in content.items() if k in keep}


# The WebP variants that the upload route writes beside every decodable image:
# ``<key>.thumb.webp`` (400 px) and ``<key>.display.webp`` (1600 px), served
# with ``?variant=thumb|display``. The serve endpoint, the LK_B media sweep,
# the replace cleanup and the orphan sweep all derive the keys from here.
PHOTO_VARIANT_SUFFIXES = (".thumb.webp", ".display.webp")


def variant_object_keys(object_key: str) -> tuple[str, ...]:
    """The variant keys stored beside a photo key, thumb first.

    A caller may derive them for any media key: for audio or video they do not
    exist, and ``delete_file`` tolerates a missing object.
    """
    return tuple(f"{object_key}{suffix}" for suffix in PHOTO_VARIANT_SUFFIXES)


def unreferenced_variant_keys(object_key: str, referenced: set[str]) -> tuple[str, ...]:
    """``variant_object_keys(object_key)``, minus any variant that is itself referenced.

    Validation only requires a media key to exist under the team's upload
    prefix, so a submission may reference a variant key (``<key>.thumb.webp``)
    instead of the photo. A caller that deletes a photo's variants must spare
    the ones this leaves out.
    """
    return tuple(key for key in variant_object_keys(object_key) if key not in referenced)


def variant_base_key(key: str) -> str | None:
    """The photo key a variant key was derived from, or None if not a variant."""
    for suffix in PHOTO_VARIANT_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return None


def run_id_from_object_key(key: str) -> UUID | None:
    """The run id a run-scoped upload key belongs to, or None for any other shape.

    ``app/routers/files.py`` writes participant uploads as
    ``{org_id}/{run_id}/{team_id}/…``. Only the second segment decides. It is
    never a UUID for station images (``{org}/stations/…``), organization media
    (``{org}/media/…``) and the legacy ``uploads/{date}/…`` keys, which keeps
    them out of the orphan sweep.
    """
    segments = key.split("/")
    if len(segments) < 3:
        return None
    try:
        return UUID(segments[1])
    except ValueError:
        return None


def reference_or_now(reference_date: datetime | None) -> datetime:
    """The reference date of a sweep, or the present moment when a sweep gets none."""
    return reference_date if reference_date is not None else datetime.now(UTC)


def is_old_enough(obj: dict[str, Any], cutoff: datetime) -> bool:
    """Whether a listed storage object was last modified at or before the cutoff."""
    last_modified = obj["last_modified"]
    if last_modified is None:
        return False  # no age information: keep rather than guess
    if last_modified.tzinfo is None:  # the S3 store returns UTC-aware datetimes; normalize defensively
        last_modified = last_modified.replace(tzinfo=UTC)
    return bool(last_modified <= cutoff)


async def ended_due_run_ids(
    session: AsyncSession,
    now: datetime,
    deletion_class: DeletionClass,
) -> list[UUID]:
    """Run ids whose ``ended_at`` is on or before the (per-org) window for the class.

    The runs of an organization whose settings cannot be read are never among
    them (``_ended_due``, which also says which organizations those are).
    """
    due, _unreadable = await _ended_due(session, now, deletion_class)
    return due


async def _ended_due(
    session: AsyncSession,
    now: datetime,
    deletion_class: DeletionClass,
) -> tuple[list[UUID], set[UUID]]:
    """The due run ids of ``ended_due_run_ids``, and the organizations left out as unreadable.

    A sweep that selects runs by a condition of its own (the abandoned runs of
    the guest sweep) leaves the same organizations out, so that one pass treats
    an organization one way.

    The period is the organization's override, then the instance setting, then
    the ``DeletionClass`` constant. Every sweep that starts at the end of a run
    reads it here, so they all agree. Without an override for the class this is
    ``ended_at <= now - default_days``. With one, the query fetches by the
    smallest period and each run is filtered by its organization's period.
    """
    default_days = instance_default_days(deletion_class)

    # The settings are selected as text and decoded one organization at a time.
    # Selected as JSONB, the driver would decode every row inside the fetch, and
    # one value it refuses (PostgreSQL stores a number of 5,000 digits that
    # ``json.loads`` does not read) would end the sweep for every organization.
    override_rows = (await session.execute(select(Organization.id, cast(Organization.settings, Text)))).all()
    overrides: dict[UUID, int] = {}
    # Organizations whose settings cannot be read. Nobody knows which period
    # such an organization wanted, so the default is no answer either: nothing
    # of it is due in this pass, on either path below.
    unreadable: set[UUID] = set()
    for org_id, settings_text in override_rows:
        # JSONB admits a ``settings`` value that is no object. One organization's
        # broken row must not stop the others.
        try:
            org_settings = json.loads(settings_text) if settings_text is not None else None
            value = stored_override(org_settings, deletion_class)
        except Exception as exc:  # noqa: BLE001 — the sweep must survive any one org
            unreadable.add(org_id)
            _warn_settings_unreadable(str(org_id), str(exc))
            continue
        if settings_unreadable(org_settings):
            # The marker of the engine's decoder (``tolerant_json_loads``), in
            # case a reader stored it: it is no settings object without an override.
            unreadable.add(org_id)
            _warn_settings_unreadable(str(org_id), "settings hold the marker for an unreadable value")
            continue
        if value is None:
            continue
        if override_in_bounds(value):
            overrides[org_id] = value
        else:
            # An override out of range, stored before the write refused it,
            # reads as the default and is logged once per sweep.
            _warn_override_out_of_range(str(org_id), deletion_class, value)

    if not overrides:
        cutoff = now - timedelta(days=default_days)
        single_cutoff = select(Run.id).where(Run.ended_at.isnot(None), Run.ended_at <= cutoff)
        if unreadable:
            single_cutoff = single_cutoff.where(Run.organization_id.notin_(unreadable))
        ids = (await session.execute(single_cutoff)).scalars().all()
        return list(ids), unreadable

    # Over-fetch by the smallest effective window, then filter per-org in Python.
    min_days = min(default_days, *overrides.values())
    broad_cutoff = now - timedelta(days=min_days)
    rows = (
        await session.execute(
            select(Run.id, Run.organization_id, Run.ended_at).where(
                Run.ended_at.isnot(None), Run.ended_at <= broad_cutoff
            )
        )
    ).all()
    due: list[UUID] = []
    for run_id, org_id, ended_at in rows:
        if ended_at is None:  # query already excludes NULL; narrows the type for the comparison
            continue
        if org_id in unreadable:
            continue
        days = overrides.get(org_id, default_days)
        try:
            if ended_at <= now - timedelta(days=days):
                due.append(run_id)
        except (OverflowError, ValueError) as exc:
            # One organization's broken settings never stop the sweep for the
            # others: the run is logged and skipped for this pass.
            logger.warning(
                "retention_eligibility_failed",
                org_id=str(org_id),
                run_id=str(run_id),
                days=days,
                error=str(exc),
            )
    return due, unreadable


async def sweep_run_media(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """LK_B: delete the media objects of the submissions of runs past the LK_B period.

    Each referenced object goes with its WebP variants. A submission loses its
    media keys only once every referenced object is gone. A failed delete is
    logged, and the submission keeps its keys for the next sweep. Returns the
    number of referenced objects deleted, without the variants.
    """
    due_run_ids = await ended_due_run_ids(session, reference_or_now(reference_date), DeletionClass.LK_B)
    if not due_run_ids:
        return 0

    stmt = select(Submission).where(Submission.run_id.in_(due_run_ids))
    submissions = (await session.execute(stmt)).scalars().all()

    deleted = 0
    for submission in submissions:
        keys = submission_media_keys(submission.content or {})
        if not keys:
            continue

        remaining_failed = False
        for key in keys:
            try:
                delete_file(key)  # tolerant of already-missing objects (NoSuchKey == success)
                deleted += 1
            except (S3Error, RuntimeError) as exc:
                remaining_failed = True
                logger.warning(
                    "retention_media_delete_failed",
                    object_key=key,
                    submission_id=str(submission.id),
                    error=str(exc),
                )
                continue
            # A variant that fails to delete is left to the orphan sweep, so it
            # does not keep the keys.
            for variant_key in variant_object_keys(key):
                try:
                    delete_file(variant_key)
                except (S3Error, RuntimeError) as exc:
                    logger.warning(
                        "retention_media_delete_failed",
                        object_key=variant_key,
                        submission_id=str(submission.id),
                        error=str(exc),
                    )

        # A live object must never lose its key. After a failed delete the
        # content stays as it is, and the next sweep retries.
        if not remaining_failed:
            submission.content = clear_submission_media_keys(submission.content or {})

    # Persist the cleared keys now, so a re-query within the same transaction
    # sees them. Autoflush is off in tests, and the caller commits once at the end.
    await session.flush()

    if deleted:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_B.value,
            swept=deleted,
            kind="submission_media",
        )
        logger.info("retention_swept_media", objects_deleted=deleted)
    return deleted


async def sweep_run_chat(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """LK_B: delete the chat messages of runs past the LK_B period, also of deleted teams.

    Returns the number of messages deleted.
    """
    due_run_ids = await ended_due_run_ids(session, reference_or_now(reference_date), DeletionClass.LK_B)
    if not due_run_ids:
        return 0

    expired_team_ids = select(Team.id).where(Team.run_id.in_(due_run_ids))

    result = await session.execute(delete(ChatMessage).where(ChatMessage.team_id.in_(expired_team_ids)))
    deleted = result.rowcount or 0  # type: ignore[attr-defined]

    if deleted:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_B.value,
            swept=deleted,
            kind="chat_messages",
        )
        logger.info("retention_swept_chat", messages_deleted=deleted)
    return deleted


async def sweep_run_guests(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """LK_A: delete the guest accounts of runs that ended past the LK_A period or were abandoned.

    A run is abandoned when it has not ended, and its start (or its creation,
    if it never started) is longer ago than ``settings.retention.abandoned_run_days``.
    The accounts go through ``delete_run_guests``. ``ON DELETE SET NULL`` keeps
    the run's history.

    The guest names in ``team_members`` go in the same step
    (``blank_guest_member_names``). The rows stay, so the team size and the
    guest count stay. This step also reaches guests whose account went earlier
    and left the name behind. Returns the number of guest accounts deleted.
    """
    now = reference_or_now(reference_date)
    # The LK_A ended branch is org-configurable. The abandoned branch uses the
    # separate ``abandoned_run_days`` knob, which is not a deletion class period.
    due_ended_ids, unreadable = await _ended_due(session, now, DeletionClass.LK_A)
    abandoned_cutoff = now - timedelta(days=settings.retention.abandoned_run_days)

    ended_condition = Run.id.in_(due_ended_ids) if due_ended_ids else false()
    abandoned_condition = and_(
        Run.state != RunState.ENDED,
        func.coalesce(Run.started_at, Run.created_at) <= abandoned_cutoff,
    )
    if unreadable:
        # An organization with unreadable settings is left out as a whole, its
        # abandoned runs too. A run loads its organization, so selecting such a
        # run would decode the settings again and raise, and the task would roll
        # back the cleanup of every organization.
        abandoned_condition = and_(abandoned_condition, Run.organization_id.notin_(unreadable))
    stmt = select(Run).where(or_(ended_condition, abandoned_condition))
    runs = (await session.execute(stmt)).scalars().all()

    # The runs due in this pass are the one place that says "due" for the guest
    # period; ``delete_run_guests`` keeps a guest only for a membership outside them.
    due_run_ids = {run.id for run in runs}
    deleted = 0
    for run in runs:
        deleted += await delete_run_guests(session, run.id, run.organization_id, due_run_ids)

    names_removed = await blank_guest_member_names(session, [run.id for run in runs])
    if names_removed:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_A.value,
            swept=names_removed,
            kind="guest_member_names",
        )
        logger.info("retention_swept_guest_member_names", names_removed=names_removed)

    if deleted:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_A.value,
            swept=deleted,
            kind="guest_accounts",
        )
        logger.info("retention_swept_guests", guests_deleted=deleted)
    return deleted


# Non-identifying placeholder for a participant-chosen team name removed by the
# LK_C anonymization sweep. Ordinal-suffixed for readability in retained stats.
ANONYMIZED_TEAM_NAME = "Team"


async def sweep_run_anonymize(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """LK_C: replace the names participants chose in runs past the LK_C period.

    The run, team, submission and score rows stay for statistics. Each named
    team gets a numbered label, and the run gets ``retention_anonymized_at``,
    which makes the renaming idempotent. Returns the number of runs anonymized.

    Two more parts look at the run, not at its marker, so they also reach a run
    that is marked already. Neither counts into the return value:

    - the names in ``team_members``, of every member, registered ones included.
      A row that already carries the label is skipped.
    - the copies of the team names, with the replacement the team got: the
      ranked entries of every result snapshot (live, revoked or expired link)
      and the SYSTEM feed entries that carry a ``team_name`` (the help
      request). Entries a person wrote are not touched.
    """
    now = reference_or_now(reference_date)
    due_run_ids = await ended_due_run_ids(session, now, DeletionClass.LK_C)
    if not due_run_ids:
        return 0

    # The run rows first, in one statement in ascending id order, as the lock
    # order of ``services/team_dissolution`` puts them. This pass writes the run
    # marker and rewrites stored results, so it takes the lock of a dissolving
    # act and the publish (``RunRepository.lock_row``) before it holds a team.
    # A marked run is locked too, because its snapshots are written below. The
    # lock is waited for: this transaction holds nothing else yet.
    await lock_run_ids(session, due_run_ids)

    # Then the team rows, in ascending id order, then their member rows. Only
    # the teams this pass writes in are locked: those of an unmarked run and
    # those that still hold a member name. The lock is waited for: every path
    # that takes a run and a team takes the run first.
    unmarked_run_ids = select(Run.id).where(Run.id.in_(due_run_ids), Run.retention_anonymized_at.is_(None))
    named_member_team_ids = select(TeamMember.team_id).where(TeamMember.display_name != ANONYMIZED_MEMBER_NAME)
    held_team_ids = await lock_team_ids(
        session,
        Team.run_id.in_(due_run_ids),
        or_(Team.run_id.in_(unmarked_run_ids), Team.id.in_(named_member_team_ids)),
    )

    names_removed = 0
    for team_ids in id_chunks(held_team_ids):
        names_result = await session.execute(
            update(TeamMember)
            .where(TeamMember.team_id.in_(team_ids), TeamMember.display_name != ANONYMIZED_MEMBER_NAME)
            .values(display_name=ANONYMIZED_MEMBER_NAME)
            .execution_options(synchronize_session=False)
        )
        names_removed += names_result.rowcount or 0  # type: ignore[attr-defined]
    if names_removed:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_C.value,
            swept=names_removed,
            kind="member_names",
        )
        logger.info("retention_swept_member_names", names_removed=names_removed)

    stmt = select(Run).where(
        Run.id.in_(due_run_ids),
        Run.retention_anonymized_at.is_(None),
    )
    runs = (await session.execute(stmt)).scalars().all()

    anonymized = 0
    renamed: dict[UUID, dict[str, str]] = {}
    for run in runs:
        renames = renamed.setdefault(run.id, {})
        teams = (
            (await session.execute(select(Team).where(Team.run_id == run.id).order_by(Team.created_at, Team.id)))
            .scalars()
            .all()
        )
        ordinal = 1
        for team in teams:
            # A team whose name is pending (NULL) has nothing to replace.
            if team.name is not None:
                new_name = f"{ANONYMIZED_TEAM_NAME} {ordinal}"
                renames[team.name] = new_name
                team.name = new_name
                ordinal += 1
        run.retention_anonymized_at = now
        anonymized += 1

    # Persist the marker and the renamed teams, so a re-query within the same
    # transaction sees the idempotency marker. Autoflush is off in tests.
    await session.flush()

    await _anonymize_team_name_copies(session, due_run_ids, renamed)

    if anonymized:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_C.value,
            swept=anonymized,
            kind="run_anonymized",
        )
        logger.info("retention_swept_anonymize", runs_anonymized=anonymized)
    return anonymized


async def _anonymize_team_name_copies(
    session: AsyncSession,
    run_ids: list[UUID],
    renamed: dict[UUID, dict[str, str]],
) -> None:
    """Replace the team names that result snapshots and feed entries keep as copies.

    ``renamed`` maps, per run renamed in this pass, each old team name to the
    new one. A run an earlier pass renamed has no entry there. Its teams carry
    the replacement already, so their current names map to themselves. A
    snapshot name that no team carries any more (a team renamed after the
    publish) becomes the bare label.
    """
    teams = (await session.execute(select(Team.id, Team.run_id, Team.name).where(Team.run_id.in_(run_ids)))).all()
    name_by_team = {team_id: name for team_id, _run_id, name in teams}
    mapping_by_run: dict[UUID, dict[str, str]] = {}
    for _team_id, run_id, name in teams:
        if run_id in renamed and renamed[run_id]:
            mapping_by_run[run_id] = renamed[run_id]
        elif name is not None:
            mapping_by_run.setdefault(run_id, {})[name] = name

    snapshots = 0
    links = (await session.execute(select(RunResultLink).where(RunResultLink.run_id.in_(run_ids)))).scalars().all()
    for link in links:
        mapping = mapping_by_run.get(link.run_id, {})
        entries = link.snapshot.get("entries") or []
        new_entries = [
            {**entry, "team_name": mapping.get(entry["team_name"], ANONYMIZED_TEAM_NAME)}
            if entry.get("team_name") is not None
            else entry
            for entry in entries
        ]
        if new_entries != entries:
            link.snapshot = {**link.snapshot, "entries": new_entries}
            snapshots += 1

    feed_entries = 0
    events = (
        (
            await session.execute(
                select(FeedEvent).where(
                    FeedEvent.run_id.in_(run_ids),
                    FeedEvent.event_type == FeedEventType.SYSTEM,
                    FeedEvent.content.has_key("team_name"),
                )
            )
        )
        .scalars()
        .all()
    )
    for event in events:
        content = event.content
        old_name = content.get("team_name")
        if old_name is None:
            continue  # a name-pending team: the sentences carry no name
        try:
            team_id: UUID | None = UUID(str(content.get("team_id")))
        except ValueError:
            team_id = None
        new_name = (name_by_team.get(team_id) if team_id else None) or ANONYMIZED_TEAM_NAME
        if content.get("message_key") == "help_request" and team_id is not None:
            new_content = help_request_feed_content(team_id, new_name)
        else:
            new_content = {
                key: value.replace(old_name, new_name) if key in ("de", "en") and isinstance(value, str) else value
                for key, value in content.items()
            }
            new_content["team_name"] = new_name
        if new_content != content:
            event.content = new_content
            feed_entries += 1

    await session.flush()

    for kind, swept in (("result_snapshots", snapshots), ("feed_entries", feed_entries)):
        if swept:
            audit_retention_swept(
                actor=dict(SYSTEM_RETENTION_ACTOR),
                deletion_class=DeletionClass.LK_C.value,
                swept=swept,
                kind=kind,
            )
            logger.info(f"retention_swept_{kind}", swept=swept)


async def sweep_anonymized_member_names(
    session: AsyncSession,
    reference_date: datetime | None = None,  # noqa: ARG001 — uniform task signature, no time window
) -> int:
    """Remove the roster names of accounts that are already anonymized.

    ``anonymize_user`` removes the roster names itself. This task catches up
    accounts anonymized without that step: the ``users`` row carries the
    anonymized address, the ``team_members`` rows still carry the name.
    Idempotent by its own condition. Returns the number of member rows changed.
    """
    anonymized_user_ids = select(User.id).where(
        User.email.startswith(ANONYMIZED_EMAIL_PREFIX, autoescape=True),
        User.email.endswith(ANONYMIZED_EMAIL_SUFFIX, autoescape=True),
    )
    named_rows = (
        TeamMember.user_id.in_(anonymized_user_ids),
        TeamMember.display_name != ANONYMIZED_MEMBER_NAME,
    )
    # The team rows first, then their member rows, in the lock order of
    # ``services/team_dissolution``. No account lock: the accounts are erased,
    # and no path writes a member row for an erased account.
    held_team_ids = await lock_team_ids(session, Team.id.in_(select(TeamMember.team_id).where(*named_rows)))
    removed = 0
    for team_ids in id_chunks(held_team_ids):
        result = await session.execute(
            update(TeamMember)
            .where(TeamMember.team_id.in_(team_ids), *named_rows)
            .values(display_name=ANONYMIZED_MEMBER_NAME)
            .execution_options(synchronize_session=False)
        )
        removed += result.rowcount or 0  # type: ignore[attr-defined]

    if removed:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_D.value,
            swept=removed,
            kind="anonymized_member_names",
        )
        logger.info("retention_swept_anonymized_member_names", names_removed=removed)
    return removed


# True for a submission whose content holds anything besides media keys and
# markers: ``content - ARRAY[those fields]`` is the answer part. The field
# names are the module constants above, never user input.
_SUBMISSION_HAS_ANSWER_PART: ColumnElement[bool] = literal_column(
    "(submissions.content - ARRAY["
    + ", ".join(
        f"'{field}'"
        for field in (*SUBMISSION_MEDIA_SCALAR_FIELDS, *SUBMISSION_MEDIA_LIST_FIELDS, *SUBMISSION_MARKER_FIELDS)
    )
    + "]::text[]) <> '{}'::jsonb",
    type_=Boolean,
)


async def sweep_run_answer_text(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """LK_H: remove the free text of runs past the LK_H period.

    The answer part of each submission goes (``clear_submission_answer_keys``:
    text, quiz choice, scanned code, while the media keys stay for LK_B), and
    so do the coordinator's comment on each score and the comment of each
    feedback entry. No row is deleted: submissions, scores with their points
    and feedback ratings stay, so every count, every sum and the published
    result stay. There is no run marker: each statement selects only rows that
    still hold text, so a second pass finds nothing. Returns the number of rows
    changed.
    """
    due_run_ids = await ended_due_run_ids(session, reference_or_now(reference_date), DeletionClass.LK_H)
    if not due_run_ids:
        return 0

    submissions = (
        (
            await session.execute(
                select(Submission).where(
                    Submission.run_id.in_(due_run_ids),
                    _SUBMISSION_HAS_ANSWER_PART,
                )
            )
        )
        .scalars()
        .all()
    )
    for submission in submissions:
        submission.content = clear_submission_answer_keys(submission.content or {})
    await session.flush()

    score_result = await session.execute(
        update(Score)
        .where(
            Score.submission_id.in_(select(Submission.id).where(Submission.run_id.in_(due_run_ids))),
            Score.comment.isnot(None),
        )
        .values(comment=None)
        .execution_options(synchronize_session=False)
    )
    feedback_result = await session.execute(
        update(Feedback)
        .where(Feedback.run_id.in_(due_run_ids), Feedback.comment.isnot(None))
        .values(comment=None)
        .execution_options(synchronize_session=False)
    )
    score_comments = score_result.rowcount or 0  # type: ignore[attr-defined]
    feedback_comments = feedback_result.rowcount or 0  # type: ignore[attr-defined]

    cleared = len(submissions) + score_comments + feedback_comments
    if cleared:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_H.value,
            swept=cleared,
            kind="answer_text",
        )
        logger.info(
            "retention_swept_answer_text",
            submissions_cleared=len(submissions),
            score_comments_cleared=score_comments,
            feedback_comments_cleared=feedback_comments,
        )
    return cleared


async def sweep_orphaned_station_media(
    session: AsyncSession,
    reference_date: datetime | None = None,  # noqa: ARG001 — uniform task signature, no time window
) -> int:
    """Delete the title images of deleted stations that no live station and no run references.

    A run's frozen copy of a station holds the key, so an image outlives its
    deleted station while a run still shows it. Returns the number of objects
    deleted.
    """
    candidate_keys = (
        (
            await session.execute(
                select(Station.title_image_key)
                .distinct()
                .where(
                    Station.is_deleted.is_(True),
                    Station.title_image_key.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )

    deleted = 0
    for key in candidate_keys:
        if key is None:  # query already excludes NULL; narrows the type for the deleter
            continue
        live_station = await session.scalar(
            select(Station.id).where(Station.title_image_key == key, Station.is_deleted.is_(False)).limit(1)
        )
        snapshot_ref = await session.scalar(select(RunStation.id).where(RunStation.title_image_key == key).limit(1))
        if live_station is not None or snapshot_ref is not None:
            continue

        try:
            delete_file(key)
        except (S3Error, RuntimeError) as exc:
            logger.warning("retention_station_media_delete_failed", object_key=key, error=str(exc))
            continue

        # Confirmed gone, so clear the key on every soft-deleted station that held it.
        await session.execute(update(Station).where(Station.title_image_key == key).values(title_image_key=None))
        deleted += 1

    if deleted:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_B.value,
            swept=deleted,
            kind="station_title_image",
        )
        logger.info("retention_swept_station_media", objects_deleted=deleted)
    return deleted


# A participant upload no submission references any more (an upload the team
# never submitted, or a replaced answer whose delete failed) is personal data
# without a remaining purpose. It is collected only after this grace period, so
# an upload between the file endpoint and the submission call is never caught.
ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS = 7


async def sweep_orphaned_submission_media(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """Delete the run-scoped participant uploads that no submission references.

    A candidate is an object whose key belongs to a run
    (``run_id_from_object_key``), never a station image or organization media.
    It goes when it is older than ``ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS``
    and no submission references its key. The reference check is global, not
    per run: validation at submit time only proves that an object exists, so an
    answer may carry another run's key (the replace cleanup guards against the
    same fact).

    The upload writes an original and its variants (``<key>.thumb.webp``,
    ``<key>.display.webp``) milliseconds apart. A separate age check per key
    could delete the original and keep a variant that is barely newer. So:

    - An eligible original, unreferenced and old enough, takes its variants
      with it, whatever their age. ``delete_file`` tolerates a missing variant.
    - A variant key that a submission references itself stays. Validation only
      requires a key under the team's upload prefix, so an answer may
      reference a variant instead of the original (``unreferenced_variant_keys``).
    - A variant whose original is still referenced stays. One whose original
      is unreferenced but not old enough waits for its original.
    - Only a leftover variant, whose original is gone and not referenced, is
      age-checked on its own.

    ``list_all_files`` lists the whole bucket, because ``storage.list_files``
    stops at 1000 objects. A failed delete is logged and skipped. Returns the
    number of objects deleted.
    """
    now = reference_or_now(reference_date)
    candidates = [obj for obj in list_all_files() if run_id_from_object_key(obj["key"]) is not None]
    if not candidates:
        return 0

    referenced: set[str] = set()
    contents: list[dict[str, Any] | None] = list((await session.execute(select(Submission.content))).scalars())
    for content in contents:
        if content:
            referenced.update(submission_media_keys(content))

    existing_keys = {obj["key"] for obj in candidates}
    cutoff = now - timedelta(days=ORPHANED_SUBMISSION_MEDIA_MIN_AGE_DAYS)

    def _delete(key: str) -> int:
        try:
            return 1 if delete_file(key) else 0
        except (S3Error, RuntimeError) as exc:
            logger.warning("retention_orphan_media_delete_failed", object_key=key, error=str(exc))
            return 0

    # A referenced key is never deleted. It is filtered out before the split
    # into originals and variants, so an original whose name ends like a
    # variant (``.thumb.webp``) is never taken for the orphaned variant of a
    # missing key while a submission references it.
    unreferenced = [obj for obj in candidates if obj["key"] not in referenced]
    originals = [obj for obj in unreferenced if variant_base_key(obj["key"]) is None]
    variants = [obj for obj in unreferenced if variant_base_key(obj["key"]) is not None]

    deleted = 0
    handled_variants: set[str] = set()
    for obj in originals:
        key = obj["key"]
        if not is_old_enough(obj, cutoff):
            continue
        deleted += _delete(key)
        for variant_key in variant_object_keys(key):
            handled_variants.add(variant_key)
        for variant_key in unreferenced_variant_keys(key, referenced):
            deleted += _delete(variant_key)

    for obj in variants:
        key = obj["key"]
        if key in handled_variants:
            continue
        base_key = variant_base_key(key)
        if base_key in referenced or base_key in existing_keys:
            continue  # still referenced, or its original is waiting its turn
        if not is_old_enough(obj, cutoff):
            continue  # a leftover variant is age-checked on its own
        deleted += _delete(key)

    if deleted:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_B.value,
            swept=deleted,
            kind="orphaned_submission_media",
        )
        logger.info("retention_swept_orphaned_submission_media", objects_deleted=deleted)
    return deleted


# Days a dead invitation (expired, revoked or accepted) is kept, so that an
# admin can still see in the days around an event that it was sent and lapsed.
DEAD_INVITATION_GRACE_DAYS = 30


async def sweep_dead_invitations(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """LK_D: hard-delete invitations that can no longer be used.

    An unaccepted invitation holds the email address of a person who never
    became a user, which is personal data. Once the link is expired or revoked,
    it has no purpose left, and it goes after ``DEAD_INVITATION_GRACE_DAYS``. An accepted
    invitation goes too: its account is the record, and the audit log holds the
    ``user_invitation_accepted`` event. Returns the number of rows deleted.
    """
    now = reference_or_now(reference_date)
    cutoff = now - timedelta(days=DEAD_INVITATION_GRACE_DAYS)

    stmt = delete(UserInvitation).where(
        or_(
            UserInvitation.accepted_at <= cutoff,
            UserInvitation.revoked_at <= cutoff,
            and_(
                UserInvitation.accepted_at.is_(None),
                UserInvitation.revoked_at.is_(None),
                UserInvitation.expires_at <= cutoff,
            ),
        )
    )
    result = await session.execute(stmt)
    deleted = result.rowcount or 0  # type: ignore[attr-defined]

    if deleted:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class=DeletionClass.LK_D.value,
            swept=deleted,
            kind="user_invitations",
        )
        logger.info("retention_swept_invitations", invitations_deleted=deleted)
    return deleted


async def sweep_expired_result_links(
    session: AsyncSession,
    reference_date: datetime | None = None,
) -> int:
    """Delete the run result links past their ``expires_at``, revoked ones included.

    A result link is a capability, not personal data, so it has no deletion
    class, but it must not stay forever. A revoke never shortens
    ``expires_at``, so a revoked link goes when it would have expired. The run
    and its scores stay. Returns the number of links deleted.

    The "result is published" notice in the run's feed goes with the link, as
    at a withdrawal, when the run has no live link left. A run published again
    after a withdrawal keeps its live link and its notice. Nothing writes the
    interim run-end notice again.

    Locks: the run ids of the expired links are read without a lock. Then
    those runs are locked in one statement in ascending id order
    (``lock_run_ids``, the lock of ``RunRepository.lock_row``, across
    organizations as in ``sweep_run_anonymize``), with no row lock held
    before. The publish, the withdrawal and the end of a run hold the same
    lock, so one that is under way commits first. Only then does a second read
    decide which links are expired: a link that a publish renewed in the
    meantime stays, with its notice.
    """
    now = reference_or_now(reference_date)
    link_repo = RunResultLinkRepository(session)

    candidate_run_ids = await link_repo.expired_run_ids(now)
    if not candidate_run_ids:
        return 0
    held_run_ids = await lock_run_ids(session, candidate_run_ids)

    lost_run_ids = await link_repo.delete_expired(held_run_ids, now)
    deleted = len(lost_run_ids)

    # The notice goes with the link, as in ``revoke_run_results``. A run with
    # a live link left has been published again: its notice belongs to that link.
    without_link = set(lost_run_ids) - await link_repo.run_ids_with_active_link(lost_run_ids)
    if without_link:
        owners = await session.execute(
            select(Run.id, Run.organization_id).where(Run.id.in_(without_link)).order_by(Run.id)
        )
        for run_id, organization_id in owners.all():
            await FeedEventRepository(session, organization_id).delete_result_link_notices(run_id)

    if deleted:
        audit_retention_swept(
            actor=dict(SYSTEM_RETENTION_ACTOR),
            deletion_class="result_links",
            swept=deleted,
            kind="run_result_links",
        )
        logger.info("retention_swept_result_links", links_deleted=deleted)
    return deleted
