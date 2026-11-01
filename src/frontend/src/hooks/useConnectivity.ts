import { useEffect, useState } from 'react';

/** Fired by the API client when a request got no answer at all. */
export const NETWORK_ERROR_EVENT = 'clavis:network-error';

const PROBE_TIMEOUT_MS = 6_000;
const RECHECK_AFTER_FAILURE_MS = 3_000;
const PROBE_EVERY_MS = 10_000;
/** A phone that wakes up needs a moment to get its radio back. */
const PROBE_AFTER_WAKE_MS = 2_000;
/** At most one probe per this span from failed requests, however many fail. */
const MIN_GAP_AFTER_ERROR_MS = 5_000;
const PROBE_WHILE_DOWN_MS = 5_000;

/**
 * One request that proves the server can be reached. It asks for a small
 * static file with a fresh query string: the service worker has no rule for
 * it and no cached copy, so only a real answer from the network counts, and
 * the API does no work for it.
 */
export async function probeServer(fetchImpl: typeof fetch = fetch): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const response = await fetchImpl(`/netcheck.html?probe=${Date.now()}`, {
      method: 'HEAD',
      cache: 'no-store',
      credentials: 'omit',
      signal: controller.signal,
    });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Whether the app can reach its server right now.
 *
 * `navigator.onLine` says only that the device has some network interface
 * up. Outdoors the usual failure is a dead connection at full signal, and a
 * phone with a VPN stays "online" in flight mode. `navigator.onLine` alone
 * shows no offline banner in either case. So this asks the server itself:
 * every `PROBE_EVERY_MS` while the tab is visible, soon after a request failed
 * without an answer, and every `PROBE_WHILE_DOWN_MS` while it is down.
 * One failure is checked again after `RECHECK_AFTER_FAILURE_MS` before the
 * answer turns to "not connected", so a single lost packet does not flash the
 * banner.
 *
 * The schedule is what finds a dead connection: on full packet loss a
 * request hangs and never fails, so no error arrives to react to. The worst
 * case until the banner shows is `PROBE_EVERY_MS` + `PROBE_TIMEOUT_MS` +
 * `RECHECK_AFTER_FAILURE_MS` + `PROBE_TIMEOUT_MS`.
 *
 * It proves the web server, not the API behind it. That is what "offline"
 * means here. An API that is down while the web server answers shows no banner.
 */
export function useConnectivity(probe: () => Promise<boolean> = probeServer): boolean {
  const [connected, setConnected] = useState<boolean>(() =>
    typeof navigator !== 'undefined' ? navigator.onLine : true,
  );

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let running = false;
    let down = typeof navigator !== 'undefined' ? !navigator.onLine : false;

    const schedule = (ms: number) => {
      clearTimeout(timer);
      timer = setTimeout(() => void check(), ms);
    };

    const check = async (): Promise<void> => {
      if (cancelled || running) return;
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        schedule(PROBE_EVERY_MS);
        return;
      }
      running = true;
      let ok = await probe();
      if (!ok && !down && !cancelled) {
        await new Promise((resolve) => setTimeout(resolve, RECHECK_AFTER_FAILURE_MS));
        if (!cancelled) ok = await probe();
      }
      running = false;
      if (cancelled) return;
      down = !ok;
      setConnected(ok);
      schedule(ok ? PROBE_EVERY_MS : PROBE_WHILE_DOWN_MS);
    };

    const checkNow = () => void check();
    // A tab that becomes visible, often a phone waking up: give the radio
    // two seconds, or both probes fail fast and the banner flashes.
    const afterWake = () => {
      if (document.visibilityState === 'visible') schedule(PROBE_AFTER_WAKE_MS);
    };
    // Many requests can fail at once. One probe answers for all of them.
    let lastErrorProbe = 0;
    const afterError = () => {
      const now = Date.now();
      if (now - lastErrorProbe < MIN_GAP_AFTER_ERROR_MS) return;
      lastErrorProbe = now;
      void check();
    };
    const wentOffline = () => {
      down = true;
      setConnected(false);
      schedule(PROBE_WHILE_DOWN_MS);
    };

    window.addEventListener('online', checkNow);
    window.addEventListener('offline', wentOffline);
    window.addEventListener(NETWORK_ERROR_EVENT, afterError);
    document.addEventListener('visibilitychange', afterWake);
    schedule(down ? 0 : PROBE_EVERY_MS);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      window.removeEventListener('online', checkNow);
      window.removeEventListener('offline', wentOffline);
      window.removeEventListener(NETWORK_ERROR_EVENT, afterError);
      document.removeEventListener('visibilitychange', afterWake);
    };
  }, [probe]);

  return connected;
}
