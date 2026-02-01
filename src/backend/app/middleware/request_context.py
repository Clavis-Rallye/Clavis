"""
Request context middleware: every log entry of an HTTP request carries its request id.

The id is the X-Request-ID header, which correlates with external systems, or a
new UUID4. It is bound to the structlog contextvars with the method, the path and
the client IP, and returned in the X-Request-ID response header.
"""

from typing import Any
from uuid import uuid4

import structlog
from fastapi import Request
from starlette.responses import Response

from app.client_address import client_address


async def request_context_middleware(request: Request, call_next: Any) -> Response:
    request_id = request.headers.get("X-Request-ID")
    if not request_id:
        request_id = str(uuid4())

    # Context left by an earlier request must not leak into this one.
    structlog.contextvars.clear_contextvars()

    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        client_ip=client_address(request),
    )

    response = await call_next(request)

    response.headers["X-Request-ID"] = request_id

    return response  # type: ignore[no-any-return]  # call_next returns Any
