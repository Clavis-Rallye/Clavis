import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

// Flush pending microtasks + fake timers together so effects can complete
// without racing @testing-library/react's waitFor (which uses real timers).
async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}
import { useWebSocket } from './useWebSocket';
import apiClient from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: {
      ...actual.default,
      get: vi.fn(),
      defaults: { baseURL: 'http://localhost:8000' },
    },
  };
});

// Minimal scripted WebSocket double. Tests pull the instance from the
// `instances` array to drive events.
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;

  readyState = FakeWebSocket.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  sent: string[] = [];

  url: string;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close(code?: number, reason?: string) {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code: code ?? 1000, reason: reason ?? '' } as CloseEvent);
  }

  // Test helpers
  simulateOpen() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.(new Event('open'));
  }
  simulateMessage(text: string) {
    this.onmessage?.({ data: text } as MessageEvent);
  }
  simulateClose(code: number, reason = '') {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code, reason } as CloseEvent);
  }
}

describe('useWebSocket', () => {
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    // @ts-expect-error — test-only assignment of minimal stub
    globalThis.WebSocket = FakeWebSocket;
    vi.mocked(apiClient.get).mockResolvedValue({ data: { token: 'fake-jwt' } });
  });

  afterEach(() => {
    vi.useRealTimers();
    globalThis.WebSocket = originalWebSocket;
    vi.clearAllMocks();
  });

  it('fetches a ws-token, opens a socket, and transitions to status=open', async () => {
    const { result } = renderHook(() =>
      useWebSocket({ path: '/api/orgs/o/runs/r/ws' }),
    );

    await flush();
    expect(FakeWebSocket.instances.length).toBe(1);
    expect(FakeWebSocket.instances[0].url).toBe(
      'ws://localhost:8000/api/orgs/o/runs/r/ws?token=fake-jwt',
    );

    act(() => FakeWebSocket.instances[0].simulateOpen());
    expect(result.current.status).toBe('open');
  });

  it('dispatches JSON messages by type to the matching handler', async () => {
    const feedHandler = vi.fn();
    const onMessage = vi.fn();

    renderHook(() =>
      useWebSocket({
        path: '/api/orgs/o/runs/r/ws',
        handlers: { feed_event: feedHandler },
        onMessage,
      }),
    );

    await flush();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.simulateOpen());

    const payload = {
      type: 'feed_event',
      data: { id: '123', text: 'hi' },
      timestamp: '2026-02-14T12:00:00Z',
    };
    act(() => ws.simulateMessage(JSON.stringify(payload)));

    expect(feedHandler).toHaveBeenCalledWith(payload.data, payload);
    expect(onMessage).toHaveBeenCalledWith(payload);
  });

  it('ignores pong and invalid JSON without calling handlers', async () => {
    const handler = vi.fn();
    renderHook(() =>
      useWebSocket({ path: '/api/orgs/o/runs/r/ws', handlers: { any: handler } }),
    );
    await flush();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.simulateOpen());

    act(() => ws.simulateMessage('pong'));
    act(() => ws.simulateMessage('not-json'));
    expect(handler).not.toHaveBeenCalled();
  });

  it('reconnects with exponential backoff on non-auth close', async () => {
    renderHook(() =>
      useWebSocket({
        path: '/api/orgs/o/runs/r/ws',
        reconnectBaseDelayMs: 100,
      }),
    );

    await flush();
    const first = FakeWebSocket.instances[0];
    act(() => first.simulateOpen());
    act(() => first.simulateClose(1006, 'abnormal'));

    // First retry scheduled at base delay = 100ms
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    // After the reconnect timer fires, connect() kicks off another ws-token
    // fetch; flush once more so the new socket is created.
    await flush();
    expect(FakeWebSocket.instances.length).toBe(2);
  });

  it('does not reconnect when closed with auth code 4001', async () => {
    const { result } = renderHook(() =>
      useWebSocket({
        path: '/api/orgs/o/runs/r/ws',
        reconnectBaseDelayMs: 100,
      }),
    );

    await vi.runOnlyPendingTimersAsync();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.simulateOpen());
    act(() => ws.simulateClose(4001, 'auth failed'));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(FakeWebSocket.instances.length).toBe(1);
    expect(result.current.lastError).toContain('auth failed');
  });

  it('does not reconnect when closed with the coordinator-forbidden code 4003', async () => {
    const { result } = renderHook(() =>
      useWebSocket({
        path: '/api/orgs/o/runs/r/ws',
        reconnectBaseDelayMs: 100,
      }),
    );

    await vi.runOnlyPendingTimersAsync();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.simulateOpen());
    act(() => ws.simulateClose(4003, 'chat window closed'));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(FakeWebSocket.instances.length).toBe(1);
    expect(result.current.lastError).toContain('chat window closed');
  });

  // The backend closes the account's sockets with 4000
  // when its password changed. Unlike 4001/4003 that is recoverable: the
  // person signs in again and the socket must have kept its reconnect
  // schedule running.
  it('reconnects when closed with the session-revoked code 4000', async () => {
    renderHook(() =>
      useWebSocket({
        path: '/api/orgs/o/runs/r/ws',
        reconnectBaseDelayMs: 100,
      }),
    );

    await flush();
    const first = FakeWebSocket.instances[0];
    act(() => first.simulateOpen());
    act(() => first.simulateClose(4000, 'session revoked'));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    await flush();
    expect(FakeWebSocket.instances.length).toBe(2);
  });

  it('closes the socket when the component unmounts', async () => {
    const { unmount } = renderHook(() =>
      useWebSocket({ path: '/api/orgs/o/runs/r/ws' }),
    );

    await vi.runOnlyPendingTimersAsync();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.simulateOpen());

    unmount();
    expect(ws.readyState).toBe(FakeWebSocket.CLOSED);
  });

  it('send() writes to the open socket and returns true', async () => {
    const { result } = renderHook(() =>
      useWebSocket({ path: '/api/orgs/o/runs/r/ws' }),
    );
    await vi.runOnlyPendingTimersAsync();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.simulateOpen());

    const ok = result.current.send({ type: 'ping', payload: 1 });
    expect(ok).toBe(true);
    expect(ws.sent).toContain(JSON.stringify({ type: 'ping', payload: 1 }));
  });

  it('send() returns false when the socket is not OPEN', async () => {
    const { result } = renderHook(() =>
      useWebSocket({ path: '/api/orgs/o/runs/r/ws' }),
    );
    // Socket created but never opened
    await vi.runOnlyPendingTimersAsync();
    expect(result.current.send('anything')).toBe(false);
  });

  it('does not open a socket when enabled=false', async () => {
    renderHook(() =>
      useWebSocket({ path: '/api/orgs/o/runs/r/ws', enabled: false }),
    );
    await vi.runOnlyPendingTimersAsync();
    expect(FakeWebSocket.instances.length).toBe(0);
    expect(apiClient.get).not.toHaveBeenCalled();
  });
});
