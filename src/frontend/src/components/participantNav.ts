// Shared constants/helpers for the participant bottom navigation. Kept separate
// from the component file so Fast Refresh stays component-only.

export const BOTTOM_NAV_HEIGHT = 64;

const LAST_RUN_KEY = 'clavis_last_run_id';

/** Remember the run a participant is in, so run-less pages (history, legal)
 * can route the bottom nav back into it. */
export function rememberLastRun(runId: string): void {
  try {
    localStorage.setItem(LAST_RUN_KEY, runId);
  } catch {
    /* Without storage the nav has no run to fall back to. */
  }
}

export function getLastRunId(): string | null {
  try {
    return localStorage.getItem(LAST_RUN_KEY);
  } catch {
    return null;
  }
}

export type ParticipantTab = 'feed' | 'rallye' | 'team';

// Map UI tab → route segment under /runs/:runId/participant/
export const TAB_ROUTE: Record<ParticipantTab, string> = {
  feed: 'feed',
  rallye: 'stations',
  team: 'team/leaderboard',
};
