"""The scheduled hard deletion of an archived organization.

An operator schedules it with ``POST /api/instance/organizations/{slug}/deletion``
(``app/routers/instance.py``), which sets ``Organization.deletion_requested_at``
and checks ``archived_at`` under one row lock. The daily sweep here deletes the
organization once ``DELETION_GRACE_DAYS`` have passed.

Each due organization is its own transaction, under a ``SELECT ... FOR UPDATE``
lock that checks again that it is still archived and due. A restored or
vanished organization is skipped.

1. Collect the object keys, reads only: the legacy ``uploads/{date}/…`` keys
   of submissions and of station and run station title images, and the
   objects under the organization's prefix ``{org_id}/``.
2. Delete every row of the organization table by table, then the
   organization row, which must delete exactly once. Children go before their
   parents: an ``ON DELETE CASCADE`` would otherwise take a child with its
   parent, and the child's own count would read zero. Flush, do not commit: a
   database error here leaves every object untouched.
3. Delete the collected objects. A failed object delete rolls the rows back
   too. Object deletes are idempotent: a missing object is no error and is not
   counted.
4. Commit. Only after that commit are the ``instance.org_deleted`` audit event
   and the log line written, with the counts and the slug read before the lock.

A failure after the flush, or of an object delete, leaves the organization as
due as before. The rollback undoes the row deletes, the next sweep deletes the
rows and the remaining objects again, and nothing is lost or counted twice.

The sweep reads the due set as plain ``(id, slug)`` tuples, never ORM
instances: a commit or a rollback expires every tracked instance, and reading
an expired attribute from code that does not await breaks an async session.
The lock is a column select with ``with_for_update()``, not ``session.get()``:
it never eager-loads the organization's ``users``, and it reads the current
row instead of a cached instance.

Legacy keys never cross organizations: a candidate must start with
``uploads/``, and it is skipped while a submission, station or run station of
another organization references it. The prefix ``{org_id}/`` of an
organization is listed and deleted only for that organization.
"""

from datetime import datetime, timedelta
from uuid import UUID

import structlog
from minio.error import S3Error
from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.challenge import Challenge
from app.models.chat_message import ChatMessage
from app.models.feed_event import FeedEvent
from app.models.feedback import Feedback
from app.models.organization import Organization
from app.models.password_reset_token import PasswordResetToken
from app.models.rallye import Rallye
from app.models.rallye_station import RallyeStation
from app.models.rallye_station_challenge import RallyeStationChallenge
from app.models.run import Run
from app.models.run_challenge import RunChallenge
from app.models.run_departure import RunDeparture
from app.models.run_result_link import RunResultLink
from app.models.run_station import RunStation
from app.models.score import Score
from app.models.station import Station
from app.models.submission import Submission
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.user import User
from app.models.user_invitation import UserInvitation
from app.repositories.organization import OrganizationRepository
from app.services.audit import record_audit_event
from app.services.gdpr import SYSTEM_RETENTION_ACTOR
from app.services.retention_tasks import reference_or_now, submission_media_keys
from app.services.ws_manager import manager
from app.storage import delete_file, list_all_files

logger = structlog.get_logger(__name__)

# The deletion runs 30 days after the operator's request.
DELETION_GRACE_DAYS = 30

# Every table reached from "organizations" through ON DELETE CASCADE foreign
# keys alone: every row the database would cascade away with the organization
# row. A child comes before every table it would be removed with.
#
# Derived from Base.metadata: the CASCADE foreign keys as edges (child,
# parent), a breadth-first walk from "organizations", then a topological sort
# with the child first. Two tables have no ``organization_id`` of their own:
# ``run_result_links`` (``run_id`` -> ``runs``) and ``password_reset_tokens``
# (``user_id`` -> ``users``). ``_scope_predicate`` scopes them through their
# parent, which is still present because it comes later.
# ``test_org_scoped_delete_order_matches_metadata_cascade_graph``
# (test_instance_org_deletion.py) derives the graph again and fails when this
# list drifts from it.
_ORG_SCOPED_TABLES_IN_DELETE_ORDER = (
    Score,
    ChatMessage,
    TeamMember,
    RallyeStationChallenge,
    Submission,
    RunChallenge,
    FeedEvent,
    RunStation,
    Team,
    RunDeparture,
    Feedback,
    RunResultLink,
    Run,
    RallyeStation,
    Station,
    Challenge,
    Rallye,
    UserInvitation,
    PasswordResetToken,
    User,
)


def _scope_predicate(model: type, organization_id: UUID) -> ColumnElement[bool]:
    """The WHERE clause that scopes the delete of one table to one organization.

    The two tables without ``organization_id`` are scoped through their
    CASCADE parent (``Run``, ``User``), which comes later in the order and so
    is still present.
    """
    if model is RunResultLink:
        return RunResultLink.run_id.in_(select(Run.id).where(Run.organization_id == organization_id))
    if model is PasswordResetToken:
        return PasswordResetToken.user_id.in_(select(User.id).where(User.organization_id == organization_id))
    return model.organization_id == organization_id  # type: ignore[attr-defined,no-any-return]


def compute_deletion_due_at(deletion_requested_at: datetime | None) -> datetime | None:
    """The timestamp the sweep will act on, or None while nothing is scheduled."""
    if deletion_requested_at is None:
        return None
    return deletion_requested_at + timedelta(days=DELETION_GRACE_DAYS)


async def _media_bearing_keys(session: AsyncSession, *, organization_id: UUID, exclude: bool) -> set[str]:
    """The object keys that submissions, stations and run stations reference.

    Those of ``organization_id``, or with ``exclude`` those of every other
    organization.
    """
    org_filter = (
        Submission.organization_id != organization_id if exclude else Submission.organization_id == organization_id
    )
    contents = (await session.execute(select(Submission.content).where(org_filter))).scalars().all()

    keys: set[str] = set()
    for content in contents:
        if content:
            keys.update(submission_media_keys(content))

    for model in (Station, RunStation):
        image_filter = model.organization_id != organization_id if exclude else model.organization_id == organization_id
        image_keys = (
            (
                await session.execute(
                    select(model.title_image_key).where(image_filter, model.title_image_key.isnot(None))
                )
            )
            .scalars()
            .all()
        )
        keys.update(key for key in image_keys if key)

    return keys


async def _collect_legacy_media_keys(session: AsyncSession, organization_id: UUID) -> set[str]:
    """The legacy ``uploads/{date}/…`` keys this organization's rows reference.

    A key that another organization still references is left out. Collected
    before the rows are deleted. A key under the prefix ``{other_org_id}/`` of
    another organization is never a candidate.
    """
    own_keys = await _media_bearing_keys(session, organization_id=organization_id, exclude=False)
    legacy_candidates = {key for key in own_keys if key.startswith("uploads/")}
    if not legacy_candidates:
        return set()

    referenced_elsewhere = await _media_bearing_keys(session, organization_id=organization_id, exclude=True)
    return legacy_candidates - referenced_elsewhere


async def _delete_organization_rows(session: AsyncSession, organization_id: UUID) -> int:
    """Delete every row of the organization, children first, then the organization row.

    Flushes and does not commit. Returns the rows deleted, the organization
    row included. Raises when the organization row does not delete exactly
    once: two sweeps would then have raced past the lock, and the transaction
    must roll back rather than count too few.
    """
    rows_deleted = 0
    for model in _ORG_SCOPED_TABLES_IN_DELETE_ORDER:
        result = await session.execute(delete(model).where(_scope_predicate(model, organization_id)))
        rows_deleted += result.rowcount or 0  # type: ignore[attr-defined]

    org_result = await session.execute(delete(Organization).where(Organization.id == organization_id))
    org_rows_deleted = org_result.rowcount or 0  # type: ignore[attr-defined]
    if org_rows_deleted != 1:
        raise RuntimeError(
            f"expected to delete exactly one organization row for {organization_id}, deleted {org_rows_deleted}"
        )
    rows_deleted += org_rows_deleted

    await session.flush()
    return rows_deleted


async def _delete_organization_objects(organization_id: UUID, keys: list[str]) -> int:
    """Delete the objects off the event loop, because the MinIO client is synchronous.

    Returns how many existed. Raises at the first failure and logs how many
    went before it. The caller then rolls the flushed row deletes back.
    """
    objects_deleted = 0
    for key in keys:
        try:
            if await run_in_threadpool(delete_file, key):
                objects_deleted += 1
        except (S3Error, RuntimeError) as exc:
            logger.warning(
                "org_deletion_object_delete_failed",
                organization_id=str(organization_id),
                object_key=key,
                objects_deleted_before_failure=objects_deleted,
                error=str(exc),
            )
            raise
    return objects_deleted


async def _delete_organization(session: AsyncSession, organization_id: UUID) -> tuple[int, int]:
    """Delete one due organization: collect the keys, delete the rows (flush only), delete the objects.

    Does not commit. The caller commits on success and rolls back on any
    exception.
    """
    legacy_keys = await _collect_legacy_media_keys(session, organization_id)
    prefix_objects = await run_in_threadpool(list_all_files, prefix=f"{organization_id}/")
    all_keys = [obj["key"] for obj in prefix_objects] + list(legacy_keys)

    rows_deleted = await _delete_organization_rows(session, organization_id)
    objects_deleted = await _delete_organization_objects(organization_id, all_keys)

    return rows_deleted, objects_deleted


async def sweep_due_organizations(session: AsyncSession, reference_date: datetime | None = None) -> int:
    """Delete every archived organization whose scheduled deletion is due, and return how many.

    Each organization gets one ``try``: lock and check again under
    ``FOR UPDATE`` (skip it when gone, no longer archived or no longer due,
    for example after a restore), delete, commit. A failing delete and a
    failing commit are handled alike: this organization rolls back, its slug
    is logged, and the sweep moves on. The audit event and the log line follow
    only a successful commit.
    """
    now = reference_or_now(reference_date)
    cutoff = now - timedelta(days=DELETION_GRACE_DAYS)
    due_ids_and_slugs = await OrganizationRepository(session).due_for_deletion(cutoff)

    deleted_count = 0
    for organization_id, slug in due_ids_and_slugs:
        try:
            locked_row = (
                await session.execute(
                    select(Organization.archived_at, Organization.deletion_requested_at)
                    .where(Organization.id == organization_id)
                    .with_for_update()
                )
            ).one_or_none()

            if locked_row is None:
                await session.rollback()
                continue
            archived_at, deletion_requested_at = locked_row
            if archived_at is None or deletion_requested_at is None or deletion_requested_at > cutoff:
                await session.rollback()  # no longer due, e.g. a concurrent restore cancelled it
                continue

            # Read before the rows go: the accounts whose sockets end with them
            member_ids = (
                (await session.execute(select(User.id).where(User.organization_id == organization_id))).scalars().all()
            )
            rows_deleted, objects_deleted = await _delete_organization(session, organization_id)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("org_deletion_failed", slug=slug)
            continue

        deleted_count += 1
        # The accounts are gone, and their open sockets go with them. Archiving
        # closed them already, this is for a socket that slipped in since.
        await manager.close_accounts(member_ids)
        record_audit_event(
            event="instance.org_deleted",
            actor=dict(SYSTEM_RETENTION_ACTOR),
            organization_id=None,
            target_slug=slug,
            rows_deleted=rows_deleted,
            objects_deleted=objects_deleted,
        )
        logger.info(
            "org_deletion_swept",
            slug=slug,
            rows_deleted=rows_deleted,
            objects_deleted=objects_deleted,
        )

    return deleted_count
