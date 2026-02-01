"""Chat message repository, scoped to an organization."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.models.chat_message import ChatMessage
from app.repositories.base import TenantScopedRepository


class ChatMessageRepository(TenantScopedRepository[ChatMessage]):
    """
    Chat messages are never changed and have no soft delete, so ``_base_query`` filters by
    organization and not by ``is_deleted``. The retention sweep deletes them, and so does
    the hard delete of their team or organization.
    """

    model = ChatMessage
    has_soft_delete = False

    async def create(self, **kwargs: Any) -> ChatMessage:
        """
        Set ``created_at`` from Python's clock. PostgreSQL's ``now()`` is the start of the
        transaction, so messages created in quick succession in different transactions
        could be ordered wrongly.
        """
        if "created_at" not in kwargs:
            kwargs["created_at"] = datetime.now(UTC)
        return await super().create(**kwargs)

    async def list_by_team(
        self,
        team_id: UUID,
        skip: int = 0,
        limit: int = 100,
        latest: bool = False,
    ) -> list[ChatMessage]:
        """
        List the messages of a team, oldest first.

        ``latest`` takes the window from the newest end and ignores ``skip``. The result
        stays oldest first.
        """
        base = self._base_query().where(ChatMessage.team_id == team_id)

        if latest:
            stmt = base.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(limit)
            result = await self.session.execute(stmt)
            return list(reversed(result.scalars().all()))

        stmt = (
            base.order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())  # Tie-break by UUIDv7 id
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_team(
        self,
        team_id: UUID,
    ) -> int:
        """Count user and system messages alike."""
        stmt = (
            select(func.count(ChatMessage.id))
            .where(ChatMessage.organization_id == self.organization_id)
            .where(ChatMessage.team_id == team_id)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one()
