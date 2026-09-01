import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useActiveCountdown } from './useActiveCountdown';
import { useUnreadNotices, __resetNoticeStoreForTests } from './useUnreadNotices';
import type { FeedEvent, FeedEventType } from '../api/types';

const makeEvent = (
  id: string,
  type: FeedEventType,
  createdAt: string,
  content: Record<string, unknown> = {},
): FeedEvent => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  event_type: type,
  content,
  author_id: null,
  target_team_id: null,
  created_at: createdAt,
});

describe('useActiveCountdown', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-01T12:00:00Z'));
    localStorage.clear();
    __resetNoticeStoreForTests();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Seeds the shared registry the way the feed's list or the run shell's WS
   *  handler would, then hands back a hook instance reading it. */
  const seedAndRender = (events: FeedEvent[]) => {
    const feeder = renderHook(() => useUnreadNotices('run-1'));
    act(() => feeder.result.current.recordNotices(events));
    return renderHook(() => useActiveCountdown('run-1'));
  };

  it('returns null while no countdown notice is running', () => {
    const { result } = seedAndRender([makeEvent('a1', 'announcement', '2026-06-01T11:55:00Z')]);

    expect(result.current).toBeNull();
  });

  it('returns the target time of the running countdown', () => {
    const { result } = seedAndRender([
      makeEvent('c1', 'countdown', '2026-06-01T11:55:00Z', { target_time: '2026-06-01T12:10:00Z' }),
    ]);

    expect(result.current).toBe('2026-06-01T12:10:00Z');
  });

  // The coordination can push a second countdown — the latest instruction
  // is the one that counts.
  it('prefers the most recently pushed of two running countdowns', () => {
    const { result } = seedAndRender([
      makeEvent('c1', 'countdown', '2026-06-01T11:50:00Z', { target_time: '2026-06-01T12:30:00Z' }),
      makeEvent('c2', 'countdown', '2026-06-01T11:55:00Z', { target_time: '2026-06-01T12:15:00Z' }),
    ]);

    expect(result.current).toBe('2026-06-01T12:15:00Z');
  });

  // The newer countdown supersedes the older
  // one outright: once the newer countdown's own target passes, nothing
  // shows, even though the older countdown's target is still ahead.
  it('shows nothing once the newest countdown expires, even while an older one is still ahead', () => {
    const { result } = seedAndRender([
      makeEvent('c1', 'countdown', '2026-06-01T11:50:00Z', { target_time: '2026-06-01T12:30:00Z' }),
      makeEvent('c2', 'countdown', '2026-06-01T11:55:00Z', { target_time: '2026-06-01T12:10:00Z' }),
    ]);

    expect(result.current).toBe('2026-06-01T12:10:00Z');

    act(() => {
      vi.advanceTimersByTime(10 * 60_000 + 1_000);
    });

    expect(result.current).toBeNull();
  });

  it('ignores a countdown whose target time already passed', () => {
    const { result } = seedAndRender([
      makeEvent('c1', 'countdown', '2026-06-01T11:00:00Z', { target_time: '2026-06-01T11:30:00Z' }),
    ]);

    expect(result.current).toBeNull();
  });

  it('ignores a countdown without a usable target time', () => {
    const { result } = seedAndRender([
      makeEvent('c1', 'countdown', '2026-06-01T11:55:00Z', {}),
      makeEvent('c2', 'countdown', '2026-06-01T11:56:00Z', { target_time: 'not-a-time' }),
    ]);

    expect(result.current).toBeNull();
  });

  // The countdown runs until its target time, then there is
  // nothing: the hook's own tick retires it without any event arriving.
  it('retires the countdown once its target time passes', () => {
    const { result } = seedAndRender([
      makeEvent('c1', 'countdown', '2026-06-01T11:55:00Z', { target_time: '2026-06-01T12:02:00Z' }),
    ]);
    expect(result.current).toBe('2026-06-01T12:02:00Z');

    act(() => {
      vi.advanceTimersByTime(2 * 60_000 + 1_000);
    });

    expect(result.current).toBeNull();
  });

  // The coordinator pushes the countdown while the participant is already
  // somewhere in the run — the header picks it up live.
  it('picks up a countdown pushed after mount', () => {
    const countdown = renderHook(() => useActiveCountdown('run-1'));
    expect(countdown.result.current).toBeNull();

    const feeder = renderHook(() => useUnreadNotices('run-1'));
    act(() => {
      feeder.result.current.recordNotices([
        makeEvent('c1', 'countdown', '2026-06-01T12:00:30Z', { target_time: '2026-06-01T12:10:00Z' }),
      ]);
    });

    expect(countdown.result.current).toBe('2026-06-01T12:10:00Z');
  });

  it('returns null without a run', () => {
    const { result } = renderHook(() => useActiveCountdown(null));

    expect(result.current).toBeNull();
  });
});
