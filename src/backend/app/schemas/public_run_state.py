"""Public read schema: participant-scoped run lifecycle state, by run id.

Backs `GET /api/public/runs/{run_id}/state`. Returns only the run's
lifecycle projection — name + state-machine timestamps — to a caller who is a
member of a team in the run. Carries no organization, coordinator, or
other-team data (tenant isolation).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.run import RunState


class PublicRunStateResponse(BaseModel):
    """A participant's read-only view of a run's lifecycle.

    Mirrors the lifecycle fields the coordinator `Run` payload exposes, minus
    everything tenant-scoped: no organization, rallye internals, team rosters,
    or settings. Enough for the participant shell to show the run name and to
    drive the state-change toast + end-of-run redirect.
    """

    run_id: UUID
    name: str
    state: RunState
    scheduled_for: datetime | None = None
    started_at: datetime | None = None
    paused_at: datetime | None = None
    ended_at: datetime | None = None
    grading_complete: bool
