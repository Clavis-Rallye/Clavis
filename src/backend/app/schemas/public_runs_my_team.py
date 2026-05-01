"""Public read schema: an authenticated user's team for a run, by join code.

Used by the participant onboarding flow to short-circuit the FindTeam step
when the caller is already a member of a team in this run (jump them
directly to /runs/:runId/participant). See `GET /api/public/runs/{code}/my-team`.
"""

from uuid import UUID

from pydantic import BaseModel


class PublicRunMyTeamResponse(BaseModel):
    """Caller's team in the run identified by `code`, or `team_id=None` if
    they have no membership yet. `run_id` is always set, so the client can
    navigate in the no-team case too and can check that the code resolves.

    `team_name` is `None` while the team has no name yet. The client uses this
    to choose between the waiting screens and the run shell.

    `is_creator` is true for the member with the earliest `joined_at` in the
    team, the same rule `routers/teams.py` uses for the right to rename. The
    wizard can read it instead of keeping its own flag. It is always `False`
    in the no-team case.
    """

    run_id: UUID
    team_id: UUID | None = None
    team_name: str | None = None
    team_share_code: str | None = None
    is_creator: bool = False
