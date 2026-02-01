"""
TOTP (RFC 6238) two-factor authentication on top of pyotp.

Recovery codes are the fallback when the authenticator is unavailable, for
example after a lost device. They are single-use and stored as Argon2id hashes.
"""

import secrets

import pyotp
from argon2 import PasswordHasher

# The same parameters as ``app.auth.passwords``.
ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


def generate_totp_secret() -> str:
    """
    Return a random Base32 TOTP secret of 32 characters.

    The caller must store it securely and show it only to the user who sets up
    the factor, as the setup answer does.
    """
    return pyotp.random_base32()


def generate_provisioning_uri(secret: str, email: str, issuer: str) -> str:
    """Return the ``otpauth://`` URI of the setup QR code. ``email`` and ``issuer`` are the labels the app shows."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def verify_totp_code(secret: str, code: str, valid_window: int = 1) -> bool:
    """
    ``valid_window`` is the number of 30-second periods accepted before and
    after the current one. The default of 1 allows 30 seconds of clock skew.
    """
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=valid_window)


def generate_recovery_codes(count: int = 8) -> list[str]:
    """Return ``count`` cryptographically random recovery codes of 8 characters. Each must be hashed before storage."""
    codes = []
    # Uppercase letters and digits without the look-alikes 0, O, 1 and I.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    for _ in range(count):
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(code)
    return codes


def hash_recovery_code(code: str) -> str:
    """Only the hash is stored, so a leaked database does not expose the codes."""
    return ph.hash(code)


def verify_recovery_code(code: str, hashed: str) -> bool:
    """Any failure, including a malformed hash, counts as a mismatch."""
    try:
        ph.verify(hashed, code)
        return True
    except Exception:
        return False


def find_and_remove_recovery_code(code: str, hashed_codes: list[str]) -> tuple[bool, list[str]]:
    """
    Return whether ``code`` matches one of ``hashed_codes``, and the list without the match.

    A code is single-use, so the match is removed. The list is unchanged when nothing matches.
    """
    for i, hashed in enumerate(hashed_codes):
        if verify_recovery_code(code, hashed):
            new_codes = hashed_codes[:i] + hashed_codes[i + 1 :]
            return (True, new_codes)

    return (False, hashed_codes)
