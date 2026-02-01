"""
Request rate limiting with slowapi.

- Key: ``client_key``, the client address the server resolved (see
  ``app/client_address.py``). The audit line of the same request carries it too.
- Storage: in memory. A single-server deployment needs no Redis.
- Strategy: moving window. "5/minute" means at most five in any 60 seconds.
  A fixed window lets up to ten pass within one minute when they straddle a
  window boundary, so "the sixth wrong code is refused" would hold only from a
  fresh window.

Apply ``@limiter.limit("10/minute")`` to an endpoint that needs a limit.
"""

from slowapi import Limiter
from starlette.requests import Request

from app.client_address import client_address


def client_key(request: Request) -> str:
    """The limiter key: the client address. slowapi passes the request only to a parameter named ``request``."""
    return client_address(request)


limiter = Limiter(key_func=client_key, storage_uri="memory://", strategy="moving-window")
