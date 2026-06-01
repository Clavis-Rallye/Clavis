import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

// Capture the useWebSocket options so a test can drive the run_state_change
// handler without a real socket.
let wsOptions: {
  path: string;
  enabled: boolean;
  handlers?: Record<string, (data: unknown) => void>;
} | null = null;

// The status the fake socket reports; a test changes it and re-renders to
// play a disconnect and a reconnect.
let wsStatus: 'idle' | 'connecting' | 'open' | 'closed' | 'error' = 'open';

vi.mock('./useWebSocket', () => ({
  useWebSocket: (opts: typeof wsOptions) => {
    wsOptions = opts;
    return { status: wsStatus };
  },
}));

vi.mock('../services/publicRuns', () => ({ fetchRunState: vi.fn() }));

import { useParticipantRunState } from './useParticipantRunState';
import { fetchRunState, type PublicRunState } from '../services/publicRuns';

const mockFetch = vi.mocked(fetchRunState);

const activeRun: PublicRunState = {
  run_id: 'r1',
  name: 'Augsburg Tour',
  state: 'active',
  scheduled_for: null,
  started_at: '2026-06-01T09:00:00Z',
  paused_at: null,
  ended_at: null,
};
const endedRun: PublicRunState = { ...activeRun, state: 'ended', ended_at: '2026-06-01T12:00:00Z' };

const pausedRun: PublicRunState = { ...activeRun, state: 'paused', paused_at: '2026-06-01T10:00:00Z' };

/** A fetch the test resolves by hand, to order responses. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
  document.dispatchEvent(new Event('visibilitychange'));
}

const fireMessage = (newState: string, previous: string) =>
  wsOptions!.handlers!.run_state_change({
    run_id: 'r1',
    organization_id: 'o1',
    new_state: newState,
    previous_state: previous,
    changed_at: null,
  });

beforeEach(() => {
  wsStatus = 'open';
  setVisibility('visible');
  wsOptions = null;
  mockFetch.mockReset();
});

describe('useParticipantRunState', () => {
  it('fetches /state via fetchRunState and resolves loading with the run', async () => {
    mockFetch.mockResolvedValue(activeRun);
    const { result } = renderHook(() => useParticipantRunState({ orgSlug: 'o1', runId: 'r1' }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockFetch).toHaveBeenCalledWith('r1');
    expect(result.current.run?.name).toBe('Augsburg Tour');
    expect(result.current.state).toBe('active');
  });

  it('resolves loading=false without fetching when disabled (no hang)', async () => {
    const { result } = renderHook(() =>
      useParticipantRunState({ orgSlug: 'o1', runId: 'r1', enabled: false }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('resolves deterministically for an already-ended run', async () => {
    mockFetch.mockResolvedValue(endedRun);
    const { result } = renderHook(() => useParticipantRunState({ orgSlug: 'o1', runId: 'r1' }));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.state).toBe('ended');
  });

  it('applies run_state_change from the WS and fires onStateChange', async () => {
    mockFetch.mockResolvedValueOnce(activeRun).mockResolvedValue(endedRun);
    const onStateChange = vi.fn();
    const { result } = renderHook(() =>
      useParticipantRunState({ orgSlug: 'o1', runId: 'r1', onStateChange }),
    );
    await waitFor(() => expect(result.current.state).toBe('active'));

    await act(async () => {
      wsOptions!.handlers!.run_state_change({
        run_id: 'r1',
        new_state: 'ended',
        previous_state: 'active',
        changed_at: null,
      });
    });

    expect(onStateChange).toHaveBeenCalledWith('ended', 'active');
    await waitFor(() => expect(result.current.state).toBe('ended'));
  });

  it('ignores run_state_change for a different run', async () => {
    mockFetch.mockResolvedValue(activeRun);
    const onStateChange = vi.fn();
    const { result } = renderHook(() =>
      useParticipantRunState({ orgSlug: 'o1', runId: 'r1', onStateChange }),
    );
    await waitFor(() => expect(result.current.state).toBe('active'));

    act(() => {
      wsOptions!.handlers!.run_state_change({
        run_id: 'OTHER',
        new_state: 'ended',
        previous_state: 'active',
        changed_at: null,
      });
    });

    expect(onStateChange).not.toHaveBeenCalled();
    expect(result.current.state).toBe('active');
  });

  it('subscribes to the (guest-authorized) run WS path with the org slug', () => {
    mockFetch.mockResolvedValue(activeRun);
    renderHook(() => useParticipantRunState({ orgSlug: 'o1', runId: 'r1' }));

    expect(wsOptions!.path).toBe('/api/orgs/o1/runs/r1/ws');
    expect(wsOptions!.enabled).toBe(true);
  });

  it('does not subscribe to the WS when the org slug is missing', () => {
    mockFetch.mockResolvedValue(activeRun);
    renderHook(() => useParticipantRunState({ orgSlug: undefined, runId: 'r1' }));

    expect(wsOptions!.enabled).toBe(false);
  });

  it('does not call onStateChange for the first fetch on mount, even when paused', async () => {
    mockFetch.mockResolvedValue(pausedRun);
    const onStateChange = vi.fn();
    const { result } = renderHook(() =>
      useParticipantRunState({ orgSlug: 'o1', runId: 'r1', onStateChange }),
    );
    await waitFor(() => expect(result.current.state).toBe('paused'));
    expect(onStateChange).not.toHaveBeenCalled();
  });

  describe('fetches the state again', () => {
    it('after a reconnect: a resume missed while the socket was down is made good', async () => {
      mockFetch.mockResolvedValueOnce(pausedRun).mockResolvedValue(activeRun);
      const onStateChange = vi.fn();
      const { result, rerender } = renderHook(() =>
        useParticipantRunState({ orgSlug: 'o1', runId: 'r1', onStateChange }),
      );
      await waitFor(() => expect(result.current.state).toBe('paused'));
      expect(mockFetch).toHaveBeenCalledTimes(1);

      wsStatus = 'closed';
      rerender();
      expect(mockFetch).toHaveBeenCalledTimes(1);
      wsStatus = 'open';
      rerender();

      await waitFor(() => expect(result.current.state).toBe('active'));
      expect(mockFetch).toHaveBeenCalledTimes(2);
      expect(onStateChange).toHaveBeenCalledTimes(1);
      expect(onStateChange).toHaveBeenCalledWith('active', 'paused');
    });

    it('after a reconnect: a pause missed while the socket was down is made good', async () => {
      mockFetch.mockResolvedValueOnce(activeRun).mockResolvedValue(pausedRun);
      const onStateChange = vi.fn();
      const { result, rerender } = renderHook(() =>
        useParticipantRunState({ orgSlug: 'o1', runId: 'r1', onStateChange }),
      );
      await waitFor(() => expect(result.current.state).toBe('active'));

      wsStatus = 'closed';
      rerender();
      wsStatus = 'open';
      rerender();

      await waitFor(() => expect(result.current.state).toBe('paused'));
      expect(onStateChange).toHaveBeenCalledTimes(1);
      expect(onStateChange).toHaveBeenCalledWith('paused', 'active');
    });

    it('does not fetch again for the first open, nor while the status stays open', async () => {
      mockFetch.mockResolvedValue(activeRun);
      const { result, rerender } = renderHook(() =>
        useParticipantRunState({ orgSlug: 'o1', runId: 'r1' }),
      );
      await waitFor(() => expect(result.current.loading).toBe(false));
      rerender();
      rerender();
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });

    it('when the page becomes visible again, and applies a changed state', async () => {
      mockFetch.mockResolvedValueOnce(pausedRun).mockResolvedValue(activeRun);
      const onStateChange = vi.fn();
      const { result } = renderHook(() =>
        useParticipantRunState({ orgSlug: 'o1', runId: 'r1', onStateChange }),
      );
      await waitFor(() => expect(result.current.state).toBe('paused'));

      act(() => setVisibility('hidden'));
      expect(mockFetch).toHaveBeenCalledTimes(1);
      act(() => setVisibility('visible'));

      await waitFor(() => expect(result.current.state).toBe('active'));
      expect(mockFetch).toHaveBeenCalledTimes(2);
      expect(onStateChange).toHaveBeenCalledWith('active', 'paused');
    });

    it('removes the visibility listener on unmount', async () => {
      mockFetch.mockResolvedValue(activeRun);
      const { result, unmount } = renderHook(() =>
        useParticipantRunState({ orgSlug: 'o1', runId: 'r1' }),
      );
      await waitFor(() => expect(result.current.loading).toBe(false));
      unmount();
      act(() => setVisibility('hidden'));
      act(() => setVisibility('visible'));
      expect(mockFetch).toHaveBeenCalledTimes(1);
    });
  });

  describe('order of a re-fetch and a live message', () => {
    it('a re-fetch that resolves after a newer live message does not win', async () => {
      mockFetch.mockResolvedValueOnce(activeRun);
      const { result, rerender } = renderHook(() =>
        useParticipantRunState({ orgSlug: 'o1', runId: 'r1' }),
      );
      await waitFor(() => expect(result.current.state).toBe('active'));

      const stale = deferred<PublicRunState | null>();
      mockFetch.mockReturnValueOnce(stale.promise).mockResolvedValue(pausedRun);
      wsStatus = 'closed';
      rerender();
      wsStatus = 'open';
      rerender();
      expect(mockFetch).toHaveBeenCalledTimes(2);

      await act(async () => {
        fireMessage('paused', 'active');
      });
      await waitFor(() => expect(result.current.state).toBe('paused'));

      await act(async () => {
        stale.resolve(activeRun);
      });
      expect(result.current.state).toBe('paused');
    });

    it('the older of two fetches resolving after the newer one does not win', async () => {
      mockFetch.mockResolvedValueOnce(activeRun);
      const { result } = renderHook(() => useParticipantRunState({ orgSlug: 'o1', runId: 'r1' }));
      await waitFor(() => expect(result.current.state).toBe('active'));

      const older = deferred<PublicRunState | null>();
      const newer = deferred<PublicRunState | null>();
      mockFetch.mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
      act(() => setVisibility('hidden'));
      act(() => setVisibility('visible'));
      act(() => setVisibility('hidden'));
      act(() => setVisibility('visible'));
      expect(mockFetch).toHaveBeenCalledTimes(3);

      await act(async () => {
        newer.resolve(pausedRun);
      });
      await waitFor(() => expect(result.current.state).toBe('paused'));
      await act(async () => {
        older.resolve(activeRun);
      });
      expect(result.current.state).toBe('paused');
    });

    it('a failed re-fetch keeps the last known run', async () => {
      mockFetch.mockResolvedValueOnce(pausedRun).mockResolvedValue(null);
      const onStateChange = vi.fn();
      const { result, rerender } = renderHook(() =>
        useParticipantRunState({ orgSlug: 'o1', runId: 'r1', onStateChange }),
      );
      await waitFor(() => expect(result.current.state).toBe('paused'));

      wsStatus = 'closed';
      rerender();
      wsStatus = 'open';
      rerender();
      await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
      await act(async () => {});

      expect(result.current.run?.name).toBe('Augsburg Tour');
      expect(result.current.state).toBe('paused');
      expect(result.current.loading).toBe(false);
      expect(onStateChange).not.toHaveBeenCalled();
    });
  });
});
