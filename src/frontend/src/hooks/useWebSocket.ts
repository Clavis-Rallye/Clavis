import { useCallback, useEffect, useRef, useState } from 'react';
import apiClient from '../api/client';

/**
 * Generic WebSocket payload shape emitted by the backend WS endpoints.
 * See src/backend/app/routers/ws.py — every broadcast is
 *   { type: string, data: <anything>, timestamp: ISO8601 }
 */
export interface WsMessage<T = unknown> {
  type: string;
  data: T;
  timestamp: string;
}

export type WsStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

interface UseWebSocketOptions {
  /**
   * Relative path from the API host, e.g. `/api/orgs/slug/runs/:id/ws`.
   * The hook prepends the configured API base URL (converted to ws://) and
   * appends `?token=<jwt>` with a short-lived ticket fetched from
   * /api/auth/ws-token on connect (and on every reconnect).
   */
  path: string;
  /**
   * Optional handlers keyed by `message.type`. An `onMessage` handler is
   * also called for every parsed message (useful for logging / catch-all).
   */
  handlers?: Record<string, (data: unknown, msg: WsMessage) => void>;
  onMessage?: (msg: WsMessage) => void;
  /**
   * Disable the connection entirely without unmounting the owning component
   * (e.g. while on an unauthenticated route). Defaults to true.
   */
  enabled?: boolean;
  /**
   * Base delay in ms for the exponential backoff on reconnect. Capped at 30s.
   */
  reconnectBaseDelayMs?: number;
  /**
   * Send a "ping" keepalive every N ms. Server responds "pong". Disable with 0.
   */
  pingIntervalMs?: number;
}

const DEFAULT_BASE_DELAY = 1000;
const MAX_BACKOFF_DELAY = 30_000;
const DEFAULT_PING_INTERVAL = 25_000;

/**
 * Auth codes the backend uses to close WS connections that cannot be
 * rescued by reconnecting (invalid/expired token, wrong org, etc).
 * See ws_dependencies.py — 4001 (auth failed) and 4003 (forbidden).
 */
const NON_RECOVERABLE_CODES = new Set([4001, 4003]);

function buildWsUrl(path: string, token: string): string {
  // `||` (not `??`): an empty baseURL means relative/same-origin API calls,
  // but WebSocket needs an absolute URL — fall back to the page origin.
  const base = apiClient.defaults.baseURL || window.location.origin;
  const wsBase = base.replace(/^http/, 'ws');
  const sep = path.includes('?') ? '&' : '?';
  return `${wsBase}${path}${sep}token=${encodeURIComponent(token)}`;
}

async function fetchWsToken(): Promise<string> {
  const { data } = await apiClient.get<{ token: string }>('/api/auth/ws-token');
  return data.token;
}

export function useWebSocket({
  path,
  handlers,
  onMessage,
  enabled = true,
  reconnectBaseDelayMs = DEFAULT_BASE_DELAY,
  pingIntervalMs = DEFAULT_PING_INTERVAL,
}: UseWebSocketOptions) {
  const [status, setStatus] = useState<WsStatus>('idle');
  const [lastError, setLastError] = useState<string | null>(null);

  // Latest-handler refs so callers can pass new inline callbacks every
  // render without tearing down the socket.
  const handlersRef = useRef(handlers);
  const onMessageRef = useRef(onMessage);
  handlersRef.current = handlers;
  onMessageRef.current = onMessage;

  // Exposed so consumers can push messages. A ref that points at the
  // currently-open socket is reassigned by the effect below.
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!enabled) {
      setStatus('idle');
      return;
    }

    let cancelled = false;
    let reconnectAttempt = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let pingTimer: ReturnType<typeof setInterval> | null = null;
    let ws: WebSocket | null = null;

    const clearTimers = () => {
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (pingTimer) {
        clearInterval(pingTimer);
        pingTimer = null;
      }
    };

    const scheduleReconnect = () => {
      if (cancelled) return;
      const attempt = reconnectAttempt++;
      const delay = Math.min(
        reconnectBaseDelayMs * 2 ** attempt,
        MAX_BACKOFF_DELAY,
      );
      reconnectTimer = setTimeout(() => {
        void connect();
      }, delay);
    };

    const connect = async () => {
      if (cancelled) return;
      setStatus('connecting');
      setLastError(null);

      let token: string;
      try {
        token = await fetchWsToken();
      } catch (err) {
        if (cancelled) return;
        setLastError(err instanceof Error ? err.message : 'ws-token fetch failed');
        setStatus('error');
        scheduleReconnect();
        return;
      }
      if (cancelled) return;

      ws = new WebSocket(buildWsUrl(path, token));
      socketRef.current = ws;

      ws.onopen = () => {
        if (cancelled) return;
        reconnectAttempt = 0;
        setStatus('open');
        if (pingIntervalMs > 0) {
          pingTimer = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) ws.send('ping');
          }, pingIntervalMs);
        }
      };

      ws.onmessage = (event) => {
        const text = typeof event.data === 'string' ? event.data : '';
        if (text === 'pong' || text === '') return;
        let parsed: WsMessage;
        try {
          parsed = JSON.parse(text) as WsMessage;
        } catch {
          return;
        }
        onMessageRef.current?.(parsed);
        handlersRef.current?.[parsed.type]?.(parsed.data, parsed);
      };

      ws.onerror = () => {
        if (cancelled) return;
        setStatus('error');
        setLastError('WebSocket error');
      };

      ws.onclose = (event) => {
        if (pingTimer) {
          clearInterval(pingTimer);
          pingTimer = null;
        }
        if (cancelled) return;
        setStatus('closed');
        if (NON_RECOVERABLE_CODES.has(event.code)) {
          setLastError(event.reason || `WS closed with code ${event.code}`);
          return;
        }
        scheduleReconnect();
      };
    };

    void connect();

    return () => {
      cancelled = true;
      clearTimers();
      ws?.close(1000, 'component unmount');
      ws = null;
      socketRef.current = null;
    };
  }, [path, enabled, reconnectBaseDelayMs, pingIntervalMs]);

  const send = useCallback((payload: string | object) => {
    const ws = socketRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    const text = typeof payload === 'string' ? payload : JSON.stringify(payload);
    ws.send(text);
    return true;
  }, []);

  return { status, lastError, send };
}
