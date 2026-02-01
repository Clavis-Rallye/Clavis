"""
Role checks for routes. Each role has every right of the roles below it.

The tenant boundary is not in this module. ``auth.dependencies.get_org_from_slug`` admits a caller to
``/api/orgs/{slug}/...`` only for the organization of their account, and every ``TenantScopedRepository``
filters each query by its ``organization_id``.
"""

from enum import IntEnum
from typing import Any

from fastapi import Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.models.user import User


class RoleLevel(IntEnum):
    """The roles in rank order, so a role check is one comparison."""

    GUEST = 0
    PARTICIPANT = 1
    COORDINATOR = 2
    CREATOR = 3
    ORG_ADMIN = 4


def require_role(min_role: str) -> Any:
    """Return a dependency that passes the current user with ``min_role`` or a higher role, and answers 403 otherwise.

    ``min_role`` is a role name in any case. A wrong name raises ``ValueError``
    when the route module is imported, not at request time.
    """
    try:
        min_level = RoleLevel[min_role.upper()]
    except KeyError as e:
        valid_roles = ", ".join([role.lower() for role in RoleLevel.__members__])
        raise ValueError(f"Invalid role '{min_role}'. Must be one of: {valid_roles}") from e

    async def checker(current_user: User = Depends(get_current_user)) -> User:
        user_level = RoleLevel[current_user.role.value.upper()]

        if user_level < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {min_role} role or higher",
            )

        return current_user

    return checker
