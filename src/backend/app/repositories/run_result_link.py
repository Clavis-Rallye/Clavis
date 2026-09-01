"""
Run result link repository.

The public slug is never stored. It is the HMAC-SHA256 of the link's ``id``, keyed by the
JWT secret (at least 32 characters, see ``AuthSettings``). Only ``slug_hash``, the SHA-256
of the slug, is stored. So a read of the database or of a log never discloses a live link
that needs no authentication, and the coordinator routes for status and publish can always
recompute the slug of a row.

The model has no ``organization_id``. A run's organization is reached through ``run_id``,
so every caller must first verify that the run belongs to its organization. The routers do
this through the org-scoped ``RunRepository``.
"""

import base64
import hashlib
import hmac
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from app.config import settings
from app.models.run_result_link import RunResultLink


def compute_slug(link_id: UUID) -> str:
    """Derive the public slug of a link. It is a pure function of the id and the app secret."""
    digest = hmac.new(
        settings.auth.jwt_secret_key.encode("utf-8"),
        f"result-link:{link_id}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:22]


def hash_slug(slug: str) -> str:
    """SHA-256 hex hash of a slug, for the ``slug_hash`` lookup column."""
    return hashlib.sha256(slug.encode("utf-8")).hexdigest()


class RunResultLinkRepository:
    """Repository for published run result links."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active_by_run_id(self, run_id: UUID) -> RunResultLink | None:
        """The one live (non-revoked) link for a run, if any."""
        stmt = select(RunResultLink).where(
            RunResultLink.run_id == run_id,
            RunResultLink.revoked_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_latest_by_run_id(self, run_id: UUID) -> RunResultLink | None:
        """The most recently published link for a run, active or revoked.

        The status endpoint uses it to tell "revoked" from "never published"
        after ``get_active_by_run_id`` came back empty.
        """
        stmt = (
            select(RunResultLink)
            .where(RunResultLink.run_id == run_id)
            .order_by(RunResultLink.published_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_run_id_for_update(self, run_id: UUID) -> list[RunResultLink]:
        """Every link row of a run, live, revoked or expired, locked and read again.

        For a path that rewrites the stored snapshots of a run. The rows are locked
        in ascending id order with ``FOR UPDATE``, so a re-publish or a revoke at
        the same moment waits and the snapshot read here is the one that gets
        written back. ``populate_existing`` makes the state under the lock replace
        anything this session loaded before.
        """
        stmt = (
            select(RunResultLink)
            .where(RunResultLink.run_id == run_id)
            .order_by(RunResultLink.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_slug(self, slug: str) -> RunResultLink | None:
        """Look up a link by its public slug (hashed before the query)."""
        stmt = select(RunResultLink).where(RunResultLink.slug_hash == hash_slug(slug))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def publish(
        self,
        run_id: UUID,
        published_by: UUID,
        snapshot: dict[str, Any],
        expiry_days: int,
    ) -> tuple[RunResultLink, str]:
        """Create or replace the live link of a run, and return it with its slug.

        A re-publish of a live link keeps its row, so the id and the slug stay, replaces the
        snapshot and restarts the expiry. When the run's only row is revoked, a new row with
        a new id and slug is created, because a revoked link never comes back. The old row
        stays until the retention sweep deletes it past its ``expires_at``.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=expiry_days)

        link = await self.get_active_by_run_id(run_id)
        if link is not None:
            link.snapshot = snapshot
            link.published_by = published_by
            link.published_at = now
            link.expires_at = expires_at
        else:
            link = RunResultLink(
                id=uuid7(),
                run_id=run_id,
                slug_hash="",  # Placeholder. The slug derives from the id, so it is set below.
                snapshot=snapshot,
                published_by=published_by,
                published_at=now,
                expires_at=expires_at,
            )
            self.session.add(link)

        slug = compute_slug(link.id)
        link.slug_hash = hash_slug(slug)
        await self.session.flush()
        return link, slug

    async def revoke(self, link: RunResultLink) -> None:
        """Mark a link as revoked. The row is kept for the retention sweep."""
        link.revoked_at = datetime.now(UTC)
        await self.session.flush()

    async def expired_run_ids(self, reference_date: datetime | None = None) -> list[UUID]:
        """The ids of the runs that have a link past its ``expires_at``. Takes no lock.

        The first read of the retention sweep: it tells the sweep which run
        rows to lock. What is expired is decided again under that lock, by
        ``delete_expired``.
        """
        now = reference_date if reference_date is not None else datetime.now(UTC)
        stmt = select(RunResultLink.run_id).where(RunResultLink.expires_at <= now).distinct()
        return list((await self.session.execute(stmt)).scalars())

    async def delete_expired(self, run_ids: Iterable[UUID], reference_date: datetime | None = None) -> list[UUID]:
        """Delete every link of these runs past its ``expires_at``.

        Return the run id of each deleted link: its length is the count deleted, its set
        the runs that lost a link. The caller holds the run rows (``lock_run_ids``). The
        publish renews a link under the same lock, so a link is read as expired here only
        if no publish is on its way to renew it.
        """
        ids = sorted(set(run_ids))
        if not ids:
            return []
        now = reference_date if reference_date is not None else datetime.now(UTC)
        result = await self.session.execute(
            delete(RunResultLink)
            .where(RunResultLink.run_id.in_(ids), RunResultLink.expires_at <= now)
            .returning(RunResultLink.run_id)
        )
        lost = list(result.scalars())
        await self.session.flush()
        return lost

    async def run_ids_with_active_link(self, run_ids: Iterable[UUID]) -> set[UUID]:
        """Those of these runs that have a live (non-revoked) link."""
        ids = sorted(set(run_ids))
        if not ids:
            return set()
        stmt = select(RunResultLink.run_id).where(RunResultLink.run_id.in_(ids), RunResultLink.revoked_at.is_(None))
        return set((await self.session.execute(stmt)).scalars())
