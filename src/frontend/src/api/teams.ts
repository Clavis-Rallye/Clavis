/** Team-flow API client: rename a team, read it, and advance its creator stage. */
import apiClient from './client';
import type { TeamMember, TeamRenameRequest } from './types';

interface TeamPatchResult {
  id: string;
  organization_id: string;
  run_id: string;
  name: string | null;
  color: string | null;
  member_count: number;
  share_code: string | null;
  /**
   * Only for a caller who may see the share code (creator and up,
   * coordinator and up, or a member of the team). Others get `null` and have
   * only `member_count`.
   */
  members: TeamMember[] | null;
}

/**
 * Set a team's display name. Callers map the `ApiError` statuses 403, 409
 * and 422 to inline errors.
 */
export async function patchTeamName(args: {
  orgSlug: string;
  runId: string;
  teamId: string;
  name: string;
}): Promise<TeamPatchResult> {
  const body: TeamRenameRequest = { name: args.name };
  const { data } = await apiClient.patch<TeamPatchResult>(
    `/api/orgs/${args.orgSlug}/runs/${args.runId}/teams/${args.teamId}`,
    body,
  );
  return data;
}

/** The polling fallback of the team flow. */
export async function getTeam(args: {
  orgSlug: string;
  runId: string;
  teamId: string;
}): Promise<TeamPatchResult> {
  const { data } = await apiClient.get<TeamPatchResult>(
    `/api/orgs/${args.orgSlug}/runs/${args.runId}/teams/${args.teamId}`,
  );
  return data;
}

/**
 * Broadcast the creator's step from Invite to PickName, so joined teammates
 * move from Waiting to Pending with the creator. Best effort: callers swallow
 * a failure, because the wait screen does not block on this signal.
 */
export async function advanceTeamStage(args: {
  orgSlug: string;
  runId: string;
  teamId: string;
  stage: 'naming';
}): Promise<void> {
  await apiClient.post(
    `/api/orgs/${args.orgSlug}/runs/${args.runId}/teams/${args.teamId}/advance-stage`,
    { stage: args.stage },
  );
}
