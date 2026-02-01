"""
Password hashing, verification and policy validation.

Hashes use Argon2id with parameters that follow BSI TR-02102. The policy comes
from the ``auth`` settings.
"""

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings

_password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
    encoding="utf-8",
)


def hash_password(password: str) -> str:
    """The result carries the algorithm, the parameters, the salt and the hash."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """The comparison is constant-time, against timing attacks. A malformed hash counts as a mismatch."""
    try:
        _password_hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password_policy(password: str) -> list[str]:
    """
    Return the policy errors of ``password``. An empty list means it is valid.

    - At least ``auth.password_min_length`` characters.
    - At least ``auth.password_criteria_count`` of four criteria: uppercase letter (A-Z),
      lowercase letter (a-z), digit (0-9), special character (``!@#$%^&*()_+-=[]{}|;:,.<>?``).
    """
    settings = get_settings()
    errors: list[str] = []

    if len(password) < settings.auth.password_min_length:
        errors.append(f"Password must be at least {settings.auth.password_min_length} characters long")

    criteria_met = 0
    criteria_descriptions = []

    if re.search(r"[A-Z]", password):
        criteria_met += 1
    else:
        criteria_descriptions.append("uppercase letter")

    if re.search(r"[a-z]", password):
        criteria_met += 1
    else:
        criteria_descriptions.append("lowercase letter")

    if re.search(r"[0-9]", password):
        criteria_met += 1
    else:
        criteria_descriptions.append("digit")

    if re.search(r"[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]", password):
        criteria_met += 1
    else:
        criteria_descriptions.append("special character")

    if criteria_met < settings.auth.password_criteria_count:
        errors.append(
            f"Password must meet at least {settings.auth.password_criteria_count} of 4 criteria: "
            f"uppercase letter, lowercase letter, digit, special character. "
            f"Missing: {', '.join(criteria_descriptions)}"
        )

    return errors


def check_password_reuse(password: str, last_password_hash: str | None) -> bool:
    """
    Say whether ``password`` matches the hash of an earlier password.

    A password may not be reused right away, so the caller rejects it on True.
    ``last_password_hash`` is None when there is no earlier password.
    """
    if last_password_hash is None:
        return False

    return verify_password(password, last_password_hash)
