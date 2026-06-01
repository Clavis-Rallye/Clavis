import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchRunState, type PublicRunState } from '../services/publicRuns';
import type { RunState } from '../api/types';
import { useWebSocket, type WsStatus } from './useWebSocket';

/**
 * Payload of the run_state_change WS event, sent by notify_run_state_change()
 * in src/backend/app/services/notify.py.
 */
interface RunStateChangePayload {
  run_id: string;
  organization_id: string;
  previous_state: string;
  new_state: RunState;
  changed_at: string | null;
}

interface UseParticipantRunStateOptions {
  /**
   * The participant's org slug, only for the run WebSocket. Its *path* is
   * org-scoped, but it is guest-authorized by design (`ws.py`). The REST
   * run-state read does not need it.
   */
  orgSlug: string | undefined;
  runId: string | undefined;
  /** Disable the fetch + WS without unmounting. */
  enabled?: boolean;
  /** Fires once per real transition observed through the WS channel. */
  onStateChange?: (next: RunState, previous: RunState | null) => void;
}

interface UseParticipantRunStateResult {
  run: PublicRunState | null;
  state: RunState | null;
  wsStatus: WsStatus;
  loading: boolean;
}

/**
 * Participant-side, **read-only** counterpart of `useRunState`.
 *
 * - Reads the run lifecycle from `GET /api/public/runs/{runId}/state`, which
 *   team membership authorizes. So a guest gets the run name and state without
 *   the coordinator-scoped `/api/orgs/...` run endpoints, which answer them 403.
 * - Applies `run_state_change` from the run WS locally, then re-fetches
 *   `/state` so the transition timestamps stay in sync. The WS path is
 *   org-scoped but guest-authorized by design, so it is reused, not duplicated.
 * - Offers **no** transition helpers, because participants never start, pause
 *   or end a run. `RunDashboard` keeps `useRunState` for the coordinator.
 *
 * `loading` always settles (also when disabled, without `runId`, or for an
 * ended run), so a shell that gates a spinner on it never hangs.
 */
export function useParticipantRunState({
  orgSlug,
  runId,
  enabled = true,
  onStateChange,
}: UseParticipantRunStateOptions): UseParticipantRunStateResult {
  // Keep the latest callback in a ref so a fresh inline function each render
  // doesn't rebuild the WS handler.
  const onStateChangeRef = useRef(onStateChange);
  onStateChangeRef.current = onStateChange;
  const [run, setRun] = useState<PublicRunState | null>(null);
  const [loading, setLoading] = useState(true);

  // What the hook shows right now, readable from an async callback.
  const runRef = useRef<PublicRunState | null>(null);
  // Ordering: `fetchSeq` numbers every fetch. `messageSeq` counts the live
  // messages. A response is applied only when no newer fetch started and no
  // message arrived after its own fetch started. The older state never wins.
  const fetchSeq = useRef(0);
  const messageSeq = useRef(0);

  const canFetch = enabled && !!runId;

  const show = useCallback((next: PublicRunState | null) => {
    runRef.current = next;
    setRun(next);
  }, []);

  // Drops a response that is still on its way when the run changes or the hook
  // unmounts.
  const dropPendingFetch = useCallback(() => {
    fetchSeq.current++;
  }, []);

  const refresh = useCallback(
    async (initial = false) => {
      if (!canFetch) {
        setLoading(false);
        return;
      }
      const seq = ++fetchSeq.current;
      const messagesAtStart = messageSeq.current;
      // A re-fetch never raises the spinner: the shell would hide the page.
      if (initial) setLoading(true);
      const fresh = await fetchRunState(runId!);
      if (seq !== fetchSeq.current || messagesAtStart !== messageSeq.current) return;
      setLoading(false);
      if (!fresh) {
        // A failed re-fetch keeps the last known run. Only a first load of
        // this run clears what an earlier run left.
        if (initial) show(null);
        return;
      }
      const previous = runRef.current?.state ?? null;
      show(fresh);
      // A re-fetch that finds a different state tells it like a live message.
      // The first load has no previous state and announces nothing.
      if (previous !== null && previous !== fresh.state) {
        onStateChangeRef.current?.(fresh.state, previous);
      }
    },
    [canFetch, runId, show],
  );

  useEffect(() => {
    void refresh(true);
    return dropPendingFetch;
  }, [refresh, dropPendingFetch]);

  // The page comes back to the front (a phone that slept): the socket may have
  // missed a message without telling.
  useEffect(() => {
    if (!canFetch) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [canFetch, refresh]);

  const canWs = enabled && !!orgSlug && !!runId;
  const { status: wsStatus } = useWebSocket({
    path: canWs ? `/api/orgs/${orgSlug}/runs/${runId}/ws` : '',
    enabled: canWs,
    handlers: {
      run_state_change: (data) => {
        const payload = data as RunStateChangePayload;
        // Defensive. The backend already scopes by channel.
        if (payload.run_id !== runId) return;
        messageSeq.current++;
        const prev = runRef.current;
        const previousState = prev?.state ?? null;
        if (previousState !== payload.new_state) {
          onStateChangeRef.current?.(payload.new_state, previousState);
        }
        if (prev) show({ ...prev, state: payload.new_state });
        // Pull the other timestamps that changed with the transition.
        void refresh();
      },
    },
  });

  // Every 'open' after the first one for this run is a reconnect: a message
  // sent while the socket was down is gone, so ask the server what is true.
  const openedForRun = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (wsStatus !== 'open' || !canFetch) return;
    if (openedForRun.current === runId) void refresh();
    else openedForRun.current = runId;
  }, [wsStatus, canFetch, runId, refresh]);

  return {
    run,
    state: run?.state ?? null,
    wsStatus,
    loading,
  };
}
