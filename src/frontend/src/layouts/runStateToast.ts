import type { RunState } from '../api/types';

/**
 * Which `participant.runStateChange.*` message a transition deserves.
 *
 * The new state alone is not enough. A return from a pause would announce the
 * start line "the run has begun", so the previous state decides.
 *
 * It lives outside RunShell because a component file that also exports helpers
 * breaks fast refresh (react-refresh/only-export-components).
 */
export function runStateToastKey(next: RunState, previous: RunState | null): string {
  if (next === 'active' && previous === 'paused') return 'resumed';
  return next;
}
