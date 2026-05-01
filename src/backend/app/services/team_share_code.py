"""
Team-share-code generation and normalization.

A team-share-code is a short, random, run-scoped credential. Format:
- 5 characters from a 32-char alphabet (uppercase letters minus I/O, digits 2-9)
- Cryptographically random via stdlib `secrets`
- Excludes visually-confusable characters

Constants are module-level so call-sites don't import private names.
"""

import secrets

# A-Z minus I,O + digits 2-9 = 32 chars total
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LENGTH = 5


def generate() -> str:
    """Return a fresh share-code (NOT checked for uniqueness — the caller does that)."""
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def normalize(s: str) -> str:
    """Trim whitespace and uppercase. Used both for storage and for lookup of user input."""
    return s.strip().upper()
