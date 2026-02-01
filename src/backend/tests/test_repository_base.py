"""Unit tests for the TenantScopedRepository and SystemRepository base classes.

TenantScopedRepository scopes every query to one organization: org A never sees org B's data.
This holds for all CRUD operations, soft delete and restore included.
SystemRepository works without organization scoping.
"""

from uuid import UUID

import pytest
from sqlalchemy import ForeignKey, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import SoftDeleteMixin, TimestampMixin, UUIDMixin
from app.repositories.base import SystemRepository, TenantScopedRepository
from tests.factories import create_test_organization


class _TenantScopedTestEntity(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Minimal tenant-scoped model with just name and organization_id.

    Prefixed with _ so that pytest does not collect it as a test class.
    """

    __tablename__ = "test_entities"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Entity name for testing",
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Foreign key to organization",
    )


class _TenantScopedTestRepository(TenantScopedRepository[_TenantScopedTestEntity]):
    model = _TenantScopedTestEntity


class _SystemTestEntity(UUIDMixin, TimestampMixin, SoftDeleteMixin, Base):
    """System-level model without organization_id.

    Prefixed with _ so that pytest does not collect it as a test class.
    """

    __tablename__ = "system_test_entities"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Entity name for testing",
    )


class _SystemTestRepository(SystemRepository[_SystemTestEntity]):
    model = _SystemTestEntity


@pytest.mark.asyncio
class TestTenantScopedRepository:
    async def test_create_auto_sets_organization_id(self, db_session: AsyncSession) -> None:
        """create() automatically sets organization_id from repository scope."""
        org = await create_test_organization(db_session, name="Test Org", slug="test")
        repo = _TenantScopedTestRepository(db_session, organization_id=org.id)

        entity = await repo.create(name="Test Entity")
        await db_session.commit()

        assert entity.organization_id == org.id
        assert entity.name == "Test Entity"

    async def test_create_overrides_provided_organization_id(self, db_session: AsyncSession) -> None:
        """create() OVERRIDES organization_id even if provided in kwargs."""
        org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
        org_b = await create_test_organization(db_session, name="Org B", slug="org-b")
        repo = _TenantScopedTestRepository(db_session, organization_id=org_a.id)

        entity = await repo.create(name="Test Entity", organization_id=org_b.id)
        await db_session.commit()

        assert entity.organization_id == org_a.id

    async def test_get_by_id_returns_none_for_other_org(self, db_session: AsyncSession) -> None:
        """get_by_id returns None for entities from another organization."""
        org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
        org_b = await create_test_organization(db_session, name="Org B", slug="org-b")

        repo_b = _TenantScopedTestRepository(db_session, organization_id=org_b.id)
        entity_b = await repo_b.create(name="Entity B")
        await db_session.commit()

        repo_a = _TenantScopedTestRepository(db_session, organization_id=org_a.id)
        result = await repo_a.get_by_id(entity_b.id)

        assert result is None

    async def test_get_by_id_returns_entity_from_same_org(self, db_session: AsyncSession) -> None:
        """get_by_id returns entity from same organization."""
        org = await create_test_organization(db_session, name="Org A", slug="org-a")
        repo = _TenantScopedTestRepository(db_session, organization_id=org.id)
        entity = await repo.create(name="Entity A")
        await db_session.commit()

        result = await repo.get_by_id(entity.id)

        assert result is not None
        assert result.id == entity.id
        assert result.organization_id == org.id

    async def test_list_returns_only_current_org_records(self, db_session: AsyncSession) -> None:
        """list() returns only records from the scoped organization."""
        org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
        org_b = await create_test_organization(db_session, name="Org B", slug="org-b")

        repo_a = _TenantScopedTestRepository(db_session, organization_id=org_a.id)
        await repo_a.create(name="Entity A1")
        await repo_a.create(name="Entity A2")
        await repo_a.create(name="Entity A3")

        repo_b = _TenantScopedTestRepository(db_session, organization_id=org_b.id)
        await repo_b.create(name="Entity B1")
        await repo_b.create(name="Entity B2")

        await db_session.commit()

        entities_a = await repo_a.list()

        assert len(entities_a) == 3
        assert all(e.organization_id == org_a.id for e in entities_a)
        assert all(e.name.startswith("Entity A") for e in entities_a)

    async def test_list_excludes_soft_deleted_by_default(self, db_session: AsyncSession) -> None:
        """list() excludes soft-deleted records by default."""
        org = await create_test_organization(db_session, name="Org A", slug="org-a")
        repo = _TenantScopedTestRepository(db_session, organization_id=org.id)

        await repo.create(name="Entity 1")
        entity2 = await repo.create(name="Entity 2")
        await repo.create(name="Entity 3")
        await db_session.commit()

        await repo.soft_delete(entity2.id)
        await db_session.commit()

        entities = await repo.list()

        assert len(entities) == 2
        assert all(e.id != entity2.id for e in entities)

    async def test_list_includes_deleted_when_requested(self, db_session: AsyncSession) -> None:
        """list(include_deleted=True) includes soft-deleted records."""
        org = await create_test_organization(db_session, name="Org A", slug="org-a")
        repo = _TenantScopedTestRepository(db_session, organization_id=org.id)

        await repo.create(name="Entity 1")
        entity2 = await repo.create(name="Entity 2")
        await repo.create(name="Entity 3")
        await db_session.commit()

        await repo.soft_delete(entity2.id)
        await db_session.commit()

        entities = await repo.list(include_deleted=True)

        assert len(entities) == 3

    async def test_count_returns_only_current_org_records(self, db_session: AsyncSession) -> None:
        """count() returns only count from the scoped organization."""
        org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
        org_b = await create_test_organization(db_session, name="Org B", slug="org-b")

        repo_a = _TenantScopedTestRepository(db_session, organization_id=org_a.id)
        repo_b = _TenantScopedTestRepository(db_session, organization_id=org_b.id)

        await repo_a.create(name="A1")
        await repo_a.create(name="A2")
        await repo_a.create(name="A3")
        await repo_b.create(name="B1")
        await repo_b.create(name="B2")
        await db_session.commit()

        count_a = await repo_a.count()
        count_b = await repo_b.count()

        assert count_a == 3
        assert count_b == 2

    async def test_count_excludes_deleted_by_default(self, db_session: AsyncSession) -> None:
        """count() excludes soft-deleted records by default."""
        org = await create_test_organization(db_session, name="Org A", slug="org-a")
        repo = _TenantScopedTestRepository(db_session, organization_id=org.id)

        await repo.create(name="Entity 1")
        entity2 = await repo.create(name="Entity 2")
        await repo.create(name="Entity 3")
        await db_session.commit()

        await repo.soft_delete(entity2.id)
        await db_session.commit()

        count = await repo.count()
        count_with_deleted = await repo.count(include_deleted=True)

        assert count == 2
        assert count_with_deleted == 3

    async def test_update_only_updates_same_org(self, db_session: AsyncSession) -> None:
        """update() cannot update entities from another organization."""
        org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
        org_b = await create_test_organization(db_session, name="Org B", slug="org-b")

        repo_b = _TenantScopedTestRepository(db_session, organization_id=org_b.id)
        entity_b = await repo_b.create(name="Original Name")
        await db_session.commit()

        repo_a = _TenantScopedTestRepository(db_session, organization_id=org_a.id)
        result = await repo_a.update(entity_b.id, name="Hacked Name")

        assert result is None

        await db_session.refresh(entity_b)
        assert entity_b.name == "Original Name"

    async def test_update_prevents_changing_organization_id(self, db_session: AsyncSession) -> None:
        """update() ignores organization_id in kwargs."""
        org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
        org_b = await create_test_organization(db_session, name="Org B", slug="org-b")

        repo_a = _TenantScopedTestRepository(db_session, organization_id=org_a.id)
        entity = await repo_a.create(name="Entity A")
        await db_session.commit()

        updated = await repo_a.update(entity.id, name="Updated", organization_id=org_b.id)
        await db_session.commit()

        assert updated is not None
        assert updated.organization_id == org_a.id
        assert updated.name == "Updated"

    async def test_soft_delete_only_deletes_same_org(self, db_session: AsyncSession) -> None:
        """soft_delete() cannot delete entities from another organization."""
        org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
        org_b = await create_test_organization(db_session, name="Org B", slug="org-b")

        repo_b = _TenantScopedTestRepository(db_session, organization_id=org_b.id)
        entity_b = await repo_b.create(name="Entity B")
        await db_session.commit()

        repo_a = _TenantScopedTestRepository(db_session, organization_id=org_a.id)
        result = await repo_a.soft_delete(entity_b.id)
        await db_session.commit()

        assert result is False

        await db_session.refresh(entity_b)
        assert entity_b.is_deleted is False

    async def test_soft_delete_marks_entity_as_deleted(self, db_session: AsyncSession) -> None:
        """soft_delete() properly marks entity as deleted."""
        org = await create_test_organization(db_session, name="Org A", slug="org-a")
        repo = _TenantScopedTestRepository(db_session, organization_id=org.id)
        entity = await repo.create(name="Entity A")
        await db_session.commit()

        result = await repo.soft_delete(entity.id)
        await db_session.commit()

        assert result is True

        await db_session.refresh(entity)
        assert entity.is_deleted is True
        assert entity.deleted_at is not None

        retrieved = await repo.get_by_id(entity.id)
        assert retrieved is None

    async def test_restore_only_restores_same_org(self, db_session: AsyncSession) -> None:
        """restore() cannot restore entities from another organization."""
        org_a = await create_test_organization(db_session, name="Org A", slug="org-a")
        org_b = await create_test_organization(db_session, name="Org B", slug="org-b")

        repo_b = _TenantScopedTestRepository(db_session, organization_id=org_b.id)
        entity_b = await repo_b.create(name="Entity B")
        await db_session.commit()
        await repo_b.soft_delete(entity_b.id)
        await db_session.commit()

        repo_a = _TenantScopedTestRepository(db_session, organization_id=org_a.id)
        result = await repo_a.restore(entity_b.id)
        await db_session.commit()

        assert result is False

        await db_session.refresh(entity_b)
        assert entity_b.is_deleted is True

    async def test_restore_restores_soft_deleted_entity(self, db_session: AsyncSession) -> None:
        """restore() properly restores a soft-deleted entity."""
        org = await create_test_organization(db_session, name="Org A", slug="org-a")
        repo = _TenantScopedTestRepository(db_session, organization_id=org.id)
        entity = await repo.create(name="Entity A")
        await db_session.commit()

        await repo.soft_delete(entity.id)
        await db_session.commit()

        result = await repo.restore(entity.id)
        await db_session.commit()

        assert result is True

        await db_session.refresh(entity)
        assert entity.is_deleted is False
        assert entity.deleted_at is None

        retrieved = await repo.get_by_id(entity.id)
        assert retrieved is not None
        assert retrieved.id == entity.id

    async def test_list_pagination(self, db_session: AsyncSession) -> None:
        """list() pagination works correctly."""
        org = await create_test_organization(db_session, name="Org A", slug="org-a")
        repo = _TenantScopedTestRepository(db_session, organization_id=org.id)

        for i in range(1, 11):
            await repo.create(name=f"Entity {i}")
        await db_session.commit()

        page1 = await repo.list(skip=0, limit=5)
        page2 = await repo.list(skip=5, limit=5)

        assert len(page1) == 5
        assert len(page2) == 5
        # No overlap (assuming ordering is consistent)
        page1_ids = {e.id for e in page1}
        page2_ids = {e.id for e in page2}
        assert len(page1_ids & page2_ids) == 0


@pytest.mark.asyncio
class TestSystemRepository:
    async def test_create_without_organization_id(self, db_session: AsyncSession) -> None:
        """SystemRepository.create() works without organization_id."""
        repo = _SystemTestRepository(db_session)

        entity = await repo.create(name="System Entity")
        await db_session.commit()

        assert entity.name == "System Entity"
        assert hasattr(entity, "id")

    async def test_get_by_id_not_scoped_to_organization(self, db_session: AsyncSession) -> None:
        """SystemRepository.get_by_id() is not organization-scoped."""
        repo = _SystemTestRepository(db_session)
        entity = await repo.create(name="System Entity")
        await db_session.commit()

        result = await repo.get_by_id(entity.id)

        assert result is not None
        assert result.id == entity.id

    async def test_list_returns_all_records(self, db_session: AsyncSession) -> None:
        """SystemRepository.list() returns all records (not org-scoped)."""
        repo = _SystemTestRepository(db_session)
        await repo.create(name="Entity 1")
        await repo.create(name="Entity 2")
        await repo.create(name="Entity 3")
        await db_session.commit()

        entities = await repo.list()

        assert len(entities) == 3

    async def test_soft_delete_and_restore(self, db_session: AsyncSession) -> None:
        """SystemRepository soft delete and restore work correctly."""
        repo = _SystemTestRepository(db_session)
        entity = await repo.create(name="System Entity")
        await db_session.commit()

        deleted = await repo.soft_delete(entity.id)
        await db_session.commit()

        assert deleted is True
        result = await repo.get_by_id(entity.id)
        assert result is None

        restored = await repo.restore(entity.id)
        await db_session.commit()

        assert restored is True
        result = await repo.get_by_id(entity.id)
        assert result is not None
