"""Public read schema: a published run's frozen final standing, by slug.

Backs ``POST /api/public/results/lookup``. The slug travels in
the JSON body, never the URL path or query string: a path or query value ends
up in the request line that Uvicorn's access log and any reverse proxy log
record on every request, and a body does not. Carries exactly the snapshot
frozen at publish time: run name, end date, organization name, the run's first
content language, and the ranked team entries. Nothing else about the run, its
organization's users, or its participants (tenant isolation, extended here to
an unauthenticated audience).
"""

from datetime import datetime

from pydantic import BaseModel, Field


class PublicResultsLookupRequest(BaseModel):
    """Request body for looking up a published result by its slug."""

    slug: str = Field(..., min_length=1, max_length=64)


class PublicResultsEntry(BaseModel):
    """One team's frozen standing in a published result."""

    team_name: str | None = None
    points: int
    rank: int
    is_winner: bool


class PublicResultsResponse(BaseModel):
    """The public projection of a published run result."""

    run_name: str
    ended_at: datetime | None = None
    language: str
    entries: list[PublicResultsEntry]
