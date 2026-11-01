import { ApiError } from '../../api/client';

/**
 * Where a participant goes after a coordinator removed them from their
 * team. The open app would keep the station list of the run, and a station
 * would then show the raw "User is not a member of any team in this run". Both
 * participant pages send such a person to /no-runs, which says what happened.
 */
export const REMOVED_FROM_RUN_PATH = '/no-runs?reason=removed';

/** The team reads refuse a caller without a team with this code. */
export function isNotTeamMember(err: unknown): boolean {
  return err instanceof ApiError && err.errorCode === 'NOT_TEAM_MEMBER';
}

/** Only a participant or guest is sent out; staff may look at the run. */
export function isPlayingRole(role: string | undefined): boolean {
  return role === 'guest' || role === 'participant';
}
