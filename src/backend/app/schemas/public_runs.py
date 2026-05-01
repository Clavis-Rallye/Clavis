"""Public, unauthenticated read-only schema for run lookups by join code."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PublicRunResponse(BaseModel):
    """Public projection of a run, found by its join code.

    The fields are limited to a minimum, so that guessing join codes cannot leak
    data across tenants. `organization_slug` is public already: it appears in
    other public URLs and in `GET /api/public/organizations`. The onboarding
    flow needs it to register a user into the right organization.
    `state` and `scheduled_for` let the first join step say at once that a run
    has not started or has ended, and when a planned run starts. Whoever holds
    the join code learns the state anyway, from the refusal of the join itself.
    """

    run_id: UUID
    rallye_name: str
    organization_slug: str
    state: str
    scheduled_for: datetime | None = None
