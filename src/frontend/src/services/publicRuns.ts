import apiClient, { ApiError } from '../api/client';
import type { RunState } from '../api/types';

/**
 * `GET /api/public/runs/{run_id}/state` for a member of a team in the run.
 * Lifecycle fields only, nothing scoped to the organization or coordinator.
 */
export interface PublicRunState {
  run_id: string;
  name: string;
  state: RunState;
  scheduled_for: string | null;
  started_at: string | null;
  paused_at: string | null;
  ended_at: string | null;
  /**
   * True once the run has ended and every submission is scored. The endpoint
   * always sends it. Optional only so local fixtures stay assignable.
   */
  grading_complete?: boolean;
}

export interface PublicRun {
  run_id: string;
  rallye_name: string;
  organization_slug: string;
  /** Lets the first join step refuse at once. Optional, because an older API
   *  in front of a newer client does not send it. */
  state?: RunState;
  scheduled_for?: string | null;
}

export interface PublicRunMyTeam {
  run_id: string;
  /** Null when the caller has no membership in this run yet. */
  team_id: string | null;
  /** Null while the team is still in the name-finding phase. */
  team_name: string | null;
  team_share_code: string | null;
  /**
   * True for the member who created the team (the earliest `joined_at`).
   * The wizard routes on this, not on a local flag that a browser back or a
   * reload loses. `false` without a team.
   */
  is_creator: boolean;
}

const FETCH_TIMEOUT_MS = 3000;

/**
 * Fetch the minimal public projection of a run by its join code.
 *
 * Returns `null` on any failure, a timeout included. The Welcome screen must
 * not block on this, so callers render without the rallye name.
 *
 * A 404 with ORG_ARCHIVED is `null` too, but it also calls `onOrgArchived`.
 * The first join page then says the run is gone, instead of letting the
 * person walk to the last step.
 */
export async function fetchByCode(
  code: string,
  signal?: AbortSignal,
  refusal?: { onOrgArchived?: () => void },
): Promise<PublicRun | null> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  try {
    const { data } = await apiClient.get<PublicRun>(
      `/api/public/runs/${encodeURIComponent(code)}`,
      { signal: controller.signal },
    );
    return data;
  } catch (err) {
    if (err instanceof ApiError && err.errorCode === 'ORG_ARCHIVED') {
      refusal?.onOrgArchived?.();
    }
    return null;
  } finally {
    clearTimeout(timeoutId);
  }
}

type FetchMyTeamResult =
  | { kind: 'unauthenticated' }
  | { kind: 'not-found' }
  | { kind: 'error' }
  | { kind: 'ok'; data: PublicRunMyTeam };

/**
 * Fetch the caller's team in the run of `code`. Onboarding uses it to skip
 * FindTeam for a caller who is already in a team. The endpoint requires auth,
 * so an anonymous caller or an expired session gets `'unauthenticated'`.
 */
export async function fetchMyTeam(
  code: string,
  signal?: AbortSignal,
): Promise<FetchMyTeamResult> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', () => controller.abort(), { once: true });
  }

  try {
    const { data } = await apiClient.get<PublicRunMyTeam>(
      `/api/public/runs/${encodeURIComponent(code)}/my-team`,
      { signal: controller.signal },
    );
    return { kind: 'ok', data };
  } catch (err) {
    // The client interceptor wraps every failed response in an ApiError. The
    // raw axios `err.response` is gone by then, so a check on it misses 404
    // and 401.
    const status = err instanceof ApiError ? err.status : undefined;
    if (status === 401) return { kind: 'unauthenticated' };
    if (status === 404) return { kind: 'not-found' };
    return { kind: 'error' };
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * Fetch the lifecycle state of a run by its **id**. Team membership
 * authorizes it, so a guest needs no coordinator-scoped `/api/orgs/...`
 * endpoint. Returns `null` on any failure. The participant shell then shows
 * the brand wordmark, and the run-state features wait.
 */
export async function fetchRunState(
  runId: string,
  signal?: AbortSignal,
): Promise<PublicRunState | null> {
  try {
    const { data } = await apiClient.get<PublicRunState>(
      `/api/public/runs/${encodeURIComponent(runId)}/state`,
      signal ? { signal } : undefined,
    );
    return data;
  } catch {
    return null;
  }
}
