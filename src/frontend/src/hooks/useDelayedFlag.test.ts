import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useDelayedFlag } from './useDelayedFlag';

describe('useDelayedFlag', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('stays false before the delay', () => {
    const { result } = renderHook(() => useDelayedFlag(true, 400));
    act(() => {
      vi.advanceTimersByTime(399);
    });
    expect(result.current).toBe(false);
  });

  it('turns true once the delay has passed', () => {
    const { result } = renderHook(() => useDelayedFlag(true, 400));
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(result.current).toBe(true);
  });

  it('turns false at once when active becomes false', () => {
    const { result, rerender } = renderHook(({ active }) => useDelayedFlag(active, 400), {
      initialProps: { active: true },
    });
    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(result.current).toBe(true);
    rerender({ active: false });
    expect(result.current).toBe(false);
  });
});
