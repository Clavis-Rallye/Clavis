"""How long the backups of this installation keep deleted data.

A backup written before the app deletes personal data still holds it until the
backup is rotated out. Only the operator knows that period, because the operator
sets the backup job. So it is an installation setting, and every text that
states the period reads it here.

Precedence: the value saved in the operator console
(``instance_settings.backup_retention_days``), else ``BACKUP_ROTATION_DAYS``
from the environment (default 30, the rotation of ``deployment/standalone/backup.sh``).

The app runs and changes no backup job. The value is a statement, and the
operator is responsible that it matches the real job.
"""

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.repositories.instance_setting import InstanceSettingRepository

BACKUP_RETENTION_MIN_DAYS = 1
BACKUP_RETENTION_MAX_DAYS = 3650

BackupRetentionSource = Literal["instance", "default"]


async def effective_backup_retention(db: AsyncSession) -> tuple[int, BackupRetentionSource]:
    """The stated backup period in days, and where it comes from."""
    setting = await InstanceSettingRepository(db).get()
    if setting is not None and setting.backup_retention_days is not None:
        return setting.backup_retention_days, "instance"
    return get_settings().retention.backup_rotation_days, "default"
