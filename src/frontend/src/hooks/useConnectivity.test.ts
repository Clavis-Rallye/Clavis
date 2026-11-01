import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NETWORK_ERROR_EVENT, probeServer, useConnectivity } from './useConnectivity';

describe('useConnectivity', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('stays connected while the server answers', async () => {
    const probe = vi.fn().mockResolvedValue(true);
    const { result } = renderHook(() => useConnectivity(probe));

    await act(async () => { await vi.advanceTimersByTimeAsync(10_500); });

    expect(probe).toHaveBeenCalledTimes(1);
    expect(result.current).toBe(true);
  });

  // The connection is dead while navigator.onLine stays true.
  it('turns to not connected when a failed request is confirmed by two probes', async () => {
    const probe = vi.fn().mockResolvedValue(false);
    const { result } = renderHook(() => useConnectivity(probe));
    expect(result.current).toBe(true);

    act(() => { window.dispatchEvent(new Event(NETWORK_ERROR_EVENT)); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(result.current).toBe(true); // one failure is not yet a verdict

    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(probe).toHaveBeenCalledTimes(2);
    expect(result.current).toBe(false);
  });

  it('does not flash for one lost request', async () => {
    const probe = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
    const { result } = renderHook(() => useConnectivity(probe));

    act(() => { window.dispatchEvent(new Event(NETWORK_ERROR_EVENT)); });
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });

    expect(result.current).toBe(true);
  });

  it('comes back by itself when the server answers again', async () => {
    const probe = vi.fn().mockResolvedValue(false);
    const { result } = renderHook(() => useConnectivity(probe));
    act(() => { window.dispatchEvent(new Event(NETWORK_ERROR_EVENT)); });
    await act(async () => { await vi.advanceTimersByTimeAsync(4_000); });
    expect(result.current).toBe(false);

    probe.mockResolvedValue(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(5_500); });

    expect(result.current).toBe(true);
  });

  // Many API calls can fail at the same moment. They share one probe.
  it('answers a burst of failed requests with one probe', async () => {
    const probe = vi.fn().mockResolvedValue(true);
    renderHook(() => useConnectivity(probe));

    act(() => {
      for (let i = 0; i < 8; i += 1) window.dispatchEvent(new Event(NETWORK_ERROR_EVENT));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });

    expect(probe).toHaveBeenCalledTimes(1);
  });

  it('follows the browser when it reports offline', () => {
    const probe = vi.fn().mockResolvedValue(true);
    const { result } = renderHook(() => useConnectivity(probe));

    act(() => { window.dispatchEvent(new Event('offline')); });

    expect(result.current).toBe(false);
  });
});

describe('probeServer', () => {
  it('counts only a real answer', async () => {
    expect(await probeServer(vi.fn().mockResolvedValue({ ok: true }) as never)).toBe(true);
    expect(await probeServer(vi.fn().mockResolvedValue({ ok: false }) as never)).toBe(false);
    expect(await probeServer(vi.fn().mockRejectedValue(new TypeError('Failed to fetch')) as never)).toBe(false);
  });

  it('asks without a cache and with a fresh address each time', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true });
    await probeServer(fetchImpl as never);
    const [url, init] = fetchImpl.mock.calls[0];
    expect(url).toMatch(/^\/netcheck\.html\?probe=\d+$/);
    expect(init).toMatchObject({ method: 'HEAD', cache: 'no-store' });
  });
});
