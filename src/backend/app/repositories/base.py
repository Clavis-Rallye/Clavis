"""
Base repositories for tenant-scoped and system-scoped entities.

``TenantScopedRepository`` is for entities that belong to an organization. It filters
every query by ``organization_id`` and leaves out soft-deleted rows.

``SystemRepository`` is for entities that are not tenant data, such as the organization
itself. It filters soft-deleted rows only.

A subclass must set the ``model`` class attribute.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified


class TenantScopedRepository[T]:
    """
    Base repository for tenant-scoped entities.

    Queries are filtered by ``organization_id`` and leave out soft-deleted rows, so that
    no query reads the data of another organization.
    """

    model: type[T]  # MUST be set by subclass
    has_soft_delete = True

    def __init__(self, session: AsyncSession, organization_id: UUID):
        self.session = session
        self.organization_id = organization_id

    def _base_query(self) -> Any:
        """
        Select the model rows of this organization. Rows that are soft-deleted are left
        out while ``has_soft_delete`` is True. A subclass whose model has no
        ``is_deleted`` column must set it to False.

        This is the foundation of tenant isolation. The ``organization_id`` filter is
        always applied, whatever the flag says. Every query must filter by
        ``organization_id``, usually by starting from this base. A method that must see
        soft-deleted rows repeats the ``organization_id`` filter and drops only the
        ``is_deleted`` filter.

        Bypass it only for a documented unscoped lookup, such as authentication or the
        guest join.
        """
        stmt = select(self.model).where(self.model.organization_id == self.organization_id)  # type: ignore[attr-defined]
        if self.has_soft_delete:
            stmt = stmt.where(self.model.is_deleted == False)  # type: ignore[attr-defined]  # noqa: E712
        return stmt

    async def get_by_id(self, entity_id: UUID, include_deleted: bool = False) -> T | None:
        """
        Get an entity by id, scoped to this organization.

        An entity of another organization is not found, so that a guessed UUID leaks
        nothing across tenants. ``include_deleted`` also matches soft-deleted entities,
        for the restore and permanent-delete flows.
        """
        if include_deleted:
            stmt = (
                select(self.model)
                .where(self.model.organization_id == self.organization_id)  # type: ignore[attr-defined]
                .where(self.model.id == entity_id)  # type: ignore[attr-defined]
            )
        else:
            stmt = self._base_query().where(self.model.id == entity_id)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        skip: int = 0,
        limit: int = 100,
        include_deleted: bool = False,
    ) -> list[T]:
        """List entities of this organization, newest first."""
        if include_deleted:
            stmt = (
                select(self.model)
                .where(self.model.organization_id == self.organization_id)  # type: ignore[attr-defined]
                .order_by(self.model.created_at.desc())  # type: ignore[attr-defined]
                .offset(skip)
                .limit(limit)
            )
        else:
            stmt = (
                self._base_query()
                .order_by(self.model.created_at.desc())  # type: ignore[attr-defined]
                .offset(skip)
                .limit(limit)
            )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        include_deleted: bool = False,
    ) -> int:
        """
        Count entities of this organization. Soft-deleted rows are left out while
        ``has_soft_delete`` is True, unless ``include_deleted`` is set.
        """
        stmt = select(func.count(self.model.id)).where(  # type: ignore[attr-defined]
            self.model.organization_id == self.organization_id  # type: ignore[attr-defined]
        )
        if self.has_soft_delete and not include_deleted:
            stmt = stmt.where(self.model.is_deleted == False)  # type: ignore[attr-defined]  # noqa: E712

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def create(self, **kwargs: Any) -> T:
        """
        Create an entity in this organization.

        A given ``organization_id`` is overridden with the scoped one, so that no entity
        is created in another organization by mistake.
        """
        kwargs["organization_id"] = self.organization_id
        entity = self.model(**kwargs)

        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)

        return entity

    async def update(self, entity_id: UUID, **kwargs: Any) -> T | None:
        """
        Update an entity by id, scoped to this organization.

        An explicit None clears a field. A key that is no attribute of the entity is
        skipped. ``organization_id`` is ignored, so an entity never changes its organization.
        Returns None when the entity is not found in this organization.
        """
        entity = await self.get_by_id(entity_id)
        if entity is None:
            return None

        kwargs.pop("organization_id", None)

        for key, value in kwargs.items():
            if hasattr(entity, key):
                setattr(entity, key, value)
                # SQLAlchemy does not see an in-place change of a JSONB value.
                column = getattr(self.model, key, None)
                if column is not None and hasattr(column, "type") and isinstance(column.type, JSONB):
                    flag_modified(entity, key)

        await self.session.flush()
        await self.session.refresh(entity)

        return entity

    def _require_soft_delete(self, method: str) -> None:
        """Refuse ``method`` on a repository whose model has no soft delete."""
        if not self.has_soft_delete:
            raise TypeError(f"{type(self).__name__} has no soft delete, so it has no {method}")

    async def soft_delete(self, entity_id: UUID) -> bool:
        """
        Soft-delete an entity of this organization. Return False when it is not found.
        Raise TypeError on a repository without soft delete.
        """
        self._require_soft_delete("soft_delete")
        entity = await self.get_by_id(entity_id)
        if entity is None:
            return False

        entity.deleted_at = datetime.utcnow()  # type: ignore[attr-defined]
        entity.is_deleted = True  # type: ignore[attr-defined]

        await self.session.flush()

        return True

    async def restore(self, entity_id: UUID) -> bool:
        """
        Restore a soft-deleted entity of this organization. Return False when it is not found.
        Raise TypeError on a repository without soft delete.
        """
        self._require_soft_delete("restore")
        stmt = (
            select(self.model)
            .where(self.model.organization_id == self.organization_id)  # type: ignore[attr-defined]
            .where(self.model.id == entity_id)  # type: ignore[attr-defined]
            .where(self.model.is_deleted == True)  # type: ignore[attr-defined]  # noqa: E712
        )
        result = await self.session.execute(stmt)
        entity = result.scalar_one_or_none()

        if entity is None:
            return False

        entity.deleted_at = None  # type: ignore[attr-defined]
        entity.is_deleted = False  # type: ignore[attr-defined]

        await self.session.flush()

        return True


class SystemRepository[T]:
    """
    Base repository for system-level entities, such as the organization itself.

    It does not filter by ``organization_id``, because these entities are not tenant
    data. It leaves out soft-deleted rows by default.
    """

    model: type[T]  # MUST be set by subclass

    def __init__(self, session: AsyncSession):
        self.session = session

    def _base_query(self) -> Any:
        return select(self.model).where(self.model.is_deleted == False)  # type: ignore[attr-defined]  # noqa: E712

    async def get_by_id(self, entity_id: UUID) -> T | None:
        """Get an entity by id, without organization scope. Soft-deleted entities are not found."""
        stmt = self._base_query().where(self.model.id == entity_id)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        skip: int = 0,
        limit: int = 100,
        include_deleted: bool = False,
    ) -> list[T]:
        """List entities without organization scope, newest first."""
        if include_deleted:
            stmt = (
                select(self.model)
                .order_by(self.model.created_at.desc())  # type: ignore[attr-defined]
                .offset(skip)
                .limit(limit)
            )
        else:
            stmt = (
                self._base_query()
                .order_by(self.model.created_at.desc())  # type: ignore[attr-defined]
                .offset(skip)
                .limit(limit)
            )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        include_deleted: bool = False,
    ) -> int:
        """Count entities without organization scope."""
        if include_deleted:
            stmt = select(func.count(self.model.id))  # type: ignore[attr-defined]
        else:
            stmt = select(func.count(self.model.id)).where(  # type: ignore[attr-defined]
                self.model.is_deleted == False  # type: ignore[attr-defined]  # noqa: E712
            )

        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def create(self, **kwargs: Any) -> T:
        entity = self.model(**kwargs)

        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)

        return entity

    async def update(self, entity_id: UUID, **kwargs: Any) -> T | None:
        """Update a system-level entity. An explicit None clears a field, an unknown key is skipped."""
        entity = await self.get_by_id(entity_id)
        if entity is None:
            return None

        for key, value in kwargs.items():
            if hasattr(entity, key):
                setattr(entity, key, value)
                # SQLAlchemy does not see an in-place change of a JSONB value.
                column = getattr(self.model, key, None)
                if column is not None and hasattr(column, "type") and isinstance(column.type, JSONB):
                    flag_modified(entity, key)

        await self.session.flush()
        await self.session.refresh(entity)

        return entity

    async def soft_delete(self, entity_id: UUID) -> bool:
        """Soft-delete a system-level entity. Return False when it is not found."""
        entity = await self.get_by_id(entity_id)
        if entity is None:
            return False

        entity.deleted_at = datetime.utcnow()  # type: ignore[attr-defined]
        entity.is_deleted = True  # type: ignore[attr-defined]

        await self.session.flush()

        return True

    async def restore(self, entity_id: UUID) -> bool:
        """Restore a soft-deleted system-level entity. Return False when it is not found."""
        stmt = (
            select(self.model)
            .where(self.model.id == entity_id)  # type: ignore[attr-defined]
            .where(self.model.is_deleted == True)  # type: ignore[attr-defined]  # noqa: E712
        )
        result = await self.session.execute(stmt)
        entity = result.scalar_one_or_none()

        if entity is None:
            return False

        entity.deleted_at = None  # type: ignore[attr-defined]
        entity.is_deleted = False  # type: ignore[attr-defined]

        await self.session.flush()

        return True
