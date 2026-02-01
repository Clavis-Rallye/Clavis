"""
Join codes for runs: six alphanumeric characters without the ambiguous ones.
"""

import secrets
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.run import RunRepository


def generate_join_code(length: int = 6) -> str:
    """
    A random uppercase join code. The alphabet leaves out 0, O, I, 1 and L,
    which are confused when read or typed.
    """
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    alphabet = alphabet.replace("0", "").replace("O", "").replace("I", "")
    alphabet = alphabet.replace("1", "").replace("L", "")

    # secrets is cryptographically strong, so a code cannot be predicted.
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def generate_unique_join_code(
    session: AsyncSession,
    organization_id: UUID,
    length: int = 6,
) -> str:
    """
    A join code that no run of the organization uses. Raises ``ValueError``
    after 10 draws that all collide.
    """
    repo = RunRepository(session, organization_id)
    max_attempts = 10

    for _attempt in range(max_attempts):
        code = generate_join_code(length)

        existing_run = await repo.get_by_join_code(code)

        if existing_run is None:
            return code

    raise ValueError(
        f"Unable to generate unique join code after {max_attempts} attempts. "
        "This is extremely unlikely and may indicate a problem with the random number generator "
        "or that the organization has an unusually large number of active runs."
    )
