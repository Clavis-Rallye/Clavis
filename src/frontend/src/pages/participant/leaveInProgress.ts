/**
 * The teams whose leave this tab started.
 *
 * The last member to leave dissolves the team, and the server broadcasts
 * team_dissolved on the run channel, the leaver's socket included. The shell
 * sends a member of a dissolved team to the removed-member page. For a leave
 * from this tab that is the wrong page, and the event can arrive before the
 * leave's answer. TeamInfo marks the team before it sends the request and
 * clears the mark when the request fails or is answered. The shell ignores
 * team_dissolved for a marked team.
 */
const marked = new Set<string>();

export function markLeaveStarted(teamId: string): void {
  marked.add(teamId);
}

export function clearLeaveMark(teamId: string): void {
  marked.delete(teamId);
}

export function isLeaveInProgress(teamId: string): boolean {
  return marked.has(teamId);
}
