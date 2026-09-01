"""
Instance settings repository.

``instance_settings`` holds at most one row: the primary key default and the CHECK
constraint ``id = 1`` make a second row impossible in the database. The write is one
atomic ``INSERT ... ON CONFLICT (id) DO UPDATE``. Of two concurrent first writes, the
loser of the insert race updates the winner's row, and the statement that commits last
decides the value of each column it touched.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instance_setting import InstanceSetting

SINGLETON_ID = 1


class InstanceSettingRepository:
    """Read and upsert the single ``instance_settings`` row (id = 1)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self) -> InstanceSetting | None:
        """The row, or ``None`` when nothing has ever been saved."""
        stmt = select(InstanceSetting).where(InstanceSetting.id == SINGLETON_ID)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(self, **kwargs: Any) -> InstanceSetting:
        """Create or update row 1 in one atomic statement, writing only ``kwargs``.

        A field left out of ``kwargs`` keeps its value. ``RETURNING`` hands back the row as
        it is after this statement, so no separate read can see a stale value.
        """
        stmt = (
            pg_insert(InstanceSetting)
            .values(id=SINGLETON_ID, **kwargs)
            .on_conflict_do_update(index_elements=["id"], set_=kwargs)
            .returning(InstanceSetting)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one()
