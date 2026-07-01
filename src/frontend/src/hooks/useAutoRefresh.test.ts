import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useAutoRefresh } from './useAutoRefresh';

const setVisibility = (state: DocumentVisibilityState) => {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
};

describe('useAutoRefresh', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility('visible');
  });

  afterEach(() => {
    vi.useRealTimers();
    setVisibility('visible');
  });

  it('refreshes on the interval while the page is visible', () => {
    const refresh = vi.fn();
    renderHook(() => useAutoRefresh(refresh, 1000));

    expect(refresh).not.toHaveBeenCalled(); // mount is the caller's own job
    vi.advanceTimersByTime(2500);
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('stays quiet while the page is hidden', () => {
    const refresh = vi.fn();
    renderHook(() => useAutoRefresh(refresh, 1000));

    setVisibility('hidden');
    vi.advanceTimersByTime(5000);
    expect(refresh).not.toHaveBeenCalled();
  });

  it('refreshes immediately when the page returns to the foreground', () => {
    const refresh = vi.fn();
    renderHook(() => useAutoRefresh(refresh, 60_000));

    setVisibility('hidden');
    document.dispatchEvent(new Event('visibilitychange'));
    expect(refresh).not.toHaveBeenCalled();

    // A pocketed phone comes back out — this is the moment a stale screen is
    // actually read, so it must not wait for the next tick.
    setVisibility('visible');
    document.dispatchEvent(new Event('visibilitychange'));
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('keeps the interval running when the callback identity changes', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(({ fn }) => useAutoRefresh(fn, 1000), {
      initialProps: { fn: first },
    });

    vi.advanceTimersByTime(1000);
    expect(first).toHaveBeenCalledTimes(1);

    // Callers pass a useCallback that changes with their params; a restart here
    // would reset the clock on every such change and could starve the refresh.
    rerender({ fn: second });
    vi.advanceTimersByTime(1000);
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledTimes(1);
  });

  it('stops on unmount', () => {
    const refresh = vi.fn();
    const { unmount } = renderHook(() => useAutoRefresh(refresh, 1000));

    unmount();
    vi.advanceTimersByTime(5000);
    document.dispatchEvent(new Event('visibilitychange'));
    expect(refresh).not.toHaveBeenCalled();
  });
});
