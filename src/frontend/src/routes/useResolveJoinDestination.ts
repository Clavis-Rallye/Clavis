import { useEffect, useState } from 'react';

import { fetchMyTeam } from '../services/publicRuns';

export type JoinDestination =
  | { kind: 'loading' }
  | { kind: 'no-team' }
  | {
      kind: 'team-pending';
      runId: string;
      teamId: string;
      teamShareCode: string | null;
      /** From the my-team response, not from wizard state. */
      isCreator: boolean;
    }
  | { kind: 'team-ready'; runId: string };

/**
 * The team step a member of a still-unnamed team belongs on.
 *
 * The creator owns the name, so they go back to their invite screen and
 * continue from there. Everybody else waits for that name. Neither of them may
 * see the "create or join" choice again: choosing there would create a second
 * team or try to join the one they are already in.
 */
export function teamPendingPath(code: string, isCreator: boolean): string {
  return `/join/${code}/team/${isCreator ? 'invite' : 'waiting'}`;
}

/**
 * Where to send an authenticated user who lands on the join wizard.
 *
 * - `no-team`: no team in this run yet, so FindTeam comes next.
 * - `team-pending`: a team without a name. The user goes to `teamPendingPath`,
 *   and the WebSocket pushes the next step from there.
 * - `team-ready`: a fully named team. The user skips the wizard and goes to
 *   /runs/:runId/participant/stations.
 *
 * An error, a 401 or a 404 gives `no-team`, so a transient backend fault
 * leaves the caller on the normal flow instead of blocking it.
 */
export function useResolveJoinDestination(code: string | undefined): JoinDestination {
  const [state, setState] = useState<JoinDestination>(
    code ? { kind: 'loading' } : { kind: 'no-team' },
  );

  useEffect(() => {
    if (!code) {
      setState({ kind: 'no-team' });
      return;
    }
    const controller = new AbortController();
    setState({ kind: 'loading' });
    fetchMyTeam(code, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (result.kind !== 'ok' || result.data.team_id === null) {
        setState({ kind: 'no-team' });
        return;
      }
      // RunShell and TeamInfo read team_id from sessionStorage to load the
      // team. Store it here too, so the team tab shows the team and its share
      // code when a signed-in user goes straight into the run.
      try {
        sessionStorage.setItem(
          `clavis_team_for_run_${result.data.run_id}`,
          result.data.team_id,
        );
      } catch {
        // Storage disabled (private mode and the like). Not fatal: the team tab
        // lacks its team until a reload after sessionStorage is available.
      }
      if (result.data.team_name === null) {
        setState({
          kind: 'team-pending',
          runId: result.data.run_id,
          teamId: result.data.team_id,
          teamShareCode: result.data.team_share_code,
          isCreator: result.data.is_creator,
        });
      } else {
        setState({ kind: 'team-ready', runId: result.data.run_id });
      }
    });
    return () => controller.abort();
  }, [code]);

  return state;
}
