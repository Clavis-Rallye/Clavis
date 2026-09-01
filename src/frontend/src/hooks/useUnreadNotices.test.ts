import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, renderHook } from '@testing-library/react';
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

describe('useUnreadNotices', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-01T12:00:00Z'));
    localStorage.clear();
    __resetNoticeStoreForTests();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // A coordination notice nobody has seen yet is unread.
  it('counts recorded coordination notices when the tab was never opened', () => {
    const { result } = renderHook(() => useUnreadNotices('run-1'));

    act(() => {
      result.current.recordNotices([makeEvent('a1', 'announcement', '2026-06-01T12:05:00Z')]);
    });

    expect(result.current.unreadCount).toBe(1);
  });

  // The feed narrates plenty the coordination never pushed — none of it is
  // a notice waiting on the activity tab.
  it('ignores feed kinds the coordination did not push', () => {
    const { result } = renderHook(() => useUnreadNotices('run-1'));

    act(() => {
      result.current.recordNotices([
        makeEvent('s1', 'system', '2026-06-01T12:05:00Z'),
        makeEvent('m1', 'milestone', '2026-06-01T12:05:00Z'),
        makeEvent('t1', 'team_update', '2026-06-01T12:05:00Z'),
        makeEvent('r1', 'results_available', '2026-06-01T12:05:00Z'),
        makeEvent('f1', 'feedback_prompt', '2026-06-01T12:05:00Z'),
      ]);
    });

    expect(result.current.unreadCount).toBe(0);
  });

  // The last-seen time is per run and persistent — notices from an earlier
  // visit must not turn unread again on the next session.
  it('reads the last-seen time persisted by an earlier session', () => {
    localStorage.setItem('clavis_feed_last_seen_run-1', String(Date.parse('2026-06-01T12:00:00Z')));
    const { result } = renderHook(() => useUnreadNotices('run-1'));

    act(() => {
      result.current.recordNotices([
        makeEvent('old', 'alert', '2026-06-01T11:59:00Z'),
        makeEvent('new', 'alert', '2026-06-01T12:01:00Z'),
      ]);
    });

    expect(result.current.unreadCount).toBe(1);
  });

  it('drops the count when the activity tab is opened and persists the time', () => {
    const { result } = renderHook(() => useUnreadNotices('run-1'));

    act(() => {
      result.current.recordNotices([
        makeEvent('a1', 'reminder', '2026-06-01T11:55:00Z'),
        makeEvent('a2', 'countdown', '2026-06-01T11:56:00Z', { target_time: '2026-06-01T13:00:00Z' }),
      ]);
    });
    expect(result.current.unreadCount).toBe(2);

    act(() => result.current.markSeen());

    expect(result.current.unreadCount).toBe(0);
    expect(localStorage.getItem('clavis_feed_last_seen_run-1')).toBe(String(Date.now()));
  });

  // The registry is shared: the feed (or the run shell's WS handler) records,
  // the bottom nav's badge counts — two hook instances, one view.
  it('shares the registry across hook instances of the same run', () => {
    const feeder = renderHook(() => useUnreadNotices('run-1'));
    const badge = renderHook(() => useUnreadNotices('run-1'));

    act(() => {
      feeder.result.current.recordNotices([makeEvent('a1', 'announcement', '2026-06-01T12:05:00Z')]);
    });

    expect(badge.result.current.unreadCount).toBe(1);
  });

  it('keeps runs separate', () => {
    const { result } = renderHook(() => useUnreadNotices('run-1'));
    const other = renderHook(() => useUnreadNotices('run-2'));

    act(() => {
      result.current.recordNotices([makeEvent('a1', 'announcement', '2026-06-01T12:05:00Z')]);
    });

    expect(other.result.current.unreadCount).toBe(0);
  });

  // The WS handler and the feed list both deliver a live notice.
  it('counts a notice delivered twice only once', () => {
    const { result } = renderHook(() => useUnreadNotices('run-1'));
    const ev = makeEvent('a1', 'announcement', '2026-06-01T12:05:00Z');

    act(() => result.current.recordNotices([ev]));
    act(() => result.current.recordNotices([ev]));

    expect(result.current.unreadCount).toBe(1);
  });

  it('counts a notice pushed after the tab was opened', () => {
    const { result } = renderHook(() => useUnreadNotices('run-1'));

    act(() => result.current.markSeen());
    vi.setSystemTime(new Date('2026-06-01T12:30:00Z'));
    act(() => {
      result.current.recordNotices([makeEvent('later', 'announcement', '2026-06-01T12:30:00Z')]);
    });

    expect(result.current.unreadCount).toBe(1);
  });

  // localStorage can be unavailable (private mode, disabled) — the badge then
  // lives for the session only and nothing throws.
  it('tolerates a localStorage that throws', () => {
    const getItem = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => { throw new Error('denied'); });
    const setItem = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => { throw new Error('denied'); });

    const { result } = renderHook(() => useUnreadNotices('run-1'));
    act(() => {
      result.current.recordNotices([makeEvent('a1', 'announcement', '2026-06-01T11:55:00Z')]);
      result.current.markSeen();
    });

    // The in-memory registry still works; only the persistence is gone.
    expect(result.current.unreadCount).toBe(0);
    getItem.mockRestore();
    setItem.mockRestore();
  });

  it('counts nothing without a run', () => {
    const { result } = renderHook(() => useUnreadNotices(null));

    expect(result.current.unreadCount).toBe(0);
    act(() => {
      result.current.recordNotices([makeEvent('a1', 'announcement', '2026-06-01T12:05:00Z')]);
      result.current.markSeen();
    });
    expect(result.current.unreadCount).toBe(0);
  });
});
