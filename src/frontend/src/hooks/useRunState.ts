import { useCallback, useEffect, useRef, useState } from 'react';
import {
  endRun as apiEndRun,
  getRun,
  pauseRun as apiPauseRun,
  resumeRun as apiResumeRun,
  scheduleRun as apiScheduleRun,
  startRun as apiStartRun,
} from '../api/runs';
import { ApiError } from '../api/client';
import type { Run, RunState } from '../api/types';
import { useWebSocket, type WsStatus } from './useWebSocket';

/**
 * Payload pushed by the backend on the run_state_change WS event.
 * See src/backend/app/services/notify.py — notify_run_state_change().
 */
interface RunStateChangePayload {
  run_id: string;
  organization_id: string;
  previous_state: string;
  new_state: RunState;
  changed_at: string | null;
}

interface UseRunStateOptions {
  orgSlug: string | undefined;
  runId: string | undefined;
  /** Disable the WebSocket + initial fetch without unmounting. */
  enabled?: boolean;
  /**
   * Fires once per real transition observed through the WS channel.
   * `previous` is null on the very first notification if no prior fetch
   * populated the run yet.
   */
  onStateChange?: (next: RunState, previous: RunState | null) => void;
}

interface UseRunStateResult {
  run: Run | null;
  state: RunState | null;
  wsStatus: WsStatus;
  loading: boolean;
  error: string | null;
  /** Force-refresh the run via HTTP GET (useful after a manual action). */
  refresh: () => Promise<void>;

  start: () => Promise<void>;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  end: () => Promise<void>;
  schedule: (scheduledFor: string) => Promise<void>;
}

/**
 * Tracks a single run's state in real time.
 *
 * - Fetches the run once on mount via `getRun`.
 * - Subscribes to `/api/orgs/{slug}/runs/{runId}/ws` for `run_state_change`
 *   notifications and applies them locally (also refreshes the full run
 *   so all metadata — started_at / paused_at / ended_at — stays in sync).
 * - Exposes transition helpers that call the corresponding POST endpoints;
 *   each helper updates local state from the response immediately, so the
 *   UI does not need to wait for the WS event round-trip.
 */
export function useRunState({
  orgSlug,
  runId,
  enabled = true,
  onStateChange,
}: UseRunStateOptions): UseRunStateResult {
  // Keep the latest callback in a ref so we don't rebuild the WS handler
  // just because the parent passes a fresh inline function every render.
  const onStateChangeRef = useRef(onStateChange);
  onStateChangeRef.current = onStateChange;
  const [run, setRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const canQuery = enabled && !!orgSlug && !!runId;

  const refresh = useCallback(async () => {
    if (!canQuery) {
      // Disabled (enabled:false) or missing params: resolve loading so
      // consumers that gate on it (e.g. a spinner) don't hang forever waiting
      // on a fetch that will never run.
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const fresh = await getRun(orgSlug!, runId!);
      setRun(fresh);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [canQuery, orgSlug, runId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const { status: wsStatus } = useWebSocket({
    path: canQuery ? `/api/orgs/${orgSlug}/runs/${runId}/ws` : '',
    enabled: canQuery,
    handlers: {
      run_state_change: (data) => {
        const payload = data as RunStateChangePayload;
        // Only react to events for our run (defensive — the backend scopes
        // by channel already, but drift is cheap to guard against).
        if (payload.run_id !== runId) return;
        // Local optimistic update of the state field, then refresh to pull
        // the other timestamps that changed with the transition.
        setRun((prev) => {
          const previousState = prev?.state ?? null;
          if (previousState !== payload.new_state) {
            // Fire the transition callback from the WS handler (not a
            // reactive effect) so react-hooks/set-state-in-effect is happy.
            onStateChangeRef.current?.(payload.new_state, previousState);
          }
          return prev ? { ...prev, state: payload.new_state } : prev;
        });
        void refresh();
      },
    },
  });

  const applyTransition = useCallback(
    async (fn: (slug: string, id: string) => Promise<Run>) => {
      if (!canQuery) return;
      setError(null);
      try {
        const next = await fn(orgSlug!, runId!);
        setRun(next);
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : String(err));
        throw err;
      }
    },
    [canQuery, orgSlug, runId],
  );

  const start = useCallback(() => applyTransition(apiStartRun), [applyTransition]);
  const pause = useCallback(() => applyTransition(apiPauseRun), [applyTransition]);
  const resume = useCallback(() => applyTransition(apiResumeRun), [applyTransition]);
  const end = useCallback(() => applyTransition(apiEndRun), [applyTransition]);
  const schedule = useCallback(
    async (scheduledFor: string) => {
      if (!canQuery) return;
      setError(null);
      try {
        const next = await apiScheduleRun(orgSlug!, runId!, {
          scheduled_for: scheduledFor,
        });
        setRun(next);
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : String(err));
        throw err;
      }
    },
    [canQuery, orgSlug, runId],
  );

  return {
    run,
    state: run?.state ?? null,
    wsStatus,
    loading,
    error,
    refresh,
    start,
    pause,
    resume,
    end,
    schedule,
  };
}
