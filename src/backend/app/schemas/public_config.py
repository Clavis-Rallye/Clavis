"""Public, unauthenticated schema for the instance configuration endpoint.

``GET /api/public/config`` answers, before any authentication, what the
frontend needs at boot: the configured brand name, whether the one-time
organization bootstrap may still run, whether exactly one organization exists
whose picker step can be skipped, and whether the feedback bar loads.

The surface is deliberately small: operator state, organization counts beyond
"exactly one", and anything tenant-specific are not public.
"""

from pydantic import BaseModel


class PublicConfig(BaseModel):
    brand_name: str
    bootstrap_open: bool
    single_org_slug: str | None
    # Whether the frontend loads the feedback bar. Off by default.
    qa_feedback: bool = False
