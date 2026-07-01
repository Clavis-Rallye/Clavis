"""Application exception types.

``CodedHTTPException`` adds a machine-readable ``error_code`` to an ``HTTPException``.
The handler in ``app.main`` serializes it as ``ErrorResponse`` (``{"detail": ..., "error_code": ...}``),
so a client (``ApiError.errorCode`` in the frontend) branches on the code instead of matching the detail text.
"""

from typing import Any

from fastapi import HTTPException


class CodedHTTPException(HTTPException):
    """HTTP error with a stable, SCREAMING_SNAKE ``error_code`` for clients."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: str,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code
        # Machine-readable context, for example ``locked_until`` of ACCOUNT_LOCKED, so
        # a client words the message in its own language instead of parsing the
        # English detail.
        self.extra = extra or {}
