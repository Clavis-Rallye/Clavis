import { useEffect, useRef } from 'react';

/** How often a visible page re-reads data that someone else may have changed. */
const DEFAULT_REFRESH_MS = 15_000;

/**
 * Keep a screen current without a live channel of its own.
 *
 * Several views load once on mount and then show what was true at that moment:
 * a teammate's answer, a coordinator's grade, a team that has just formed. This
 * re-runs `refresh` on an interval while the page is visible, and at once when
 * the page returns to the foreground. That trigger matters most on a rally: a
 * phone spends most of a station in a pocket, so a stale screen is read when it
 * comes back.
 *
 * Nothing runs while the page is hidden, because a pocketed phone must not keep
 * polling. `refresh` should be quiet (no loading spinner), or the view blinks
 * on every tick.
 */
export function useAutoRefresh(refresh: () => void | Promise<void>, intervalMs = DEFAULT_REFRESH_MS) {
  // A ref, so a new callback identity does not restart the interval. Callers
  // usually pass a useCallback that changes with their params.
  const latest = useRef(refresh);
  useEffect(() => {
    latest.current = refresh;
  }, [refresh]);

  useEffect(() => {
    const run = () => {
      if (document.visibilityState === 'visible') void latest.current();
    };
    const id = window.setInterval(run, intervalMs);
    document.addEventListener('visibilitychange', run);
    window.addEventListener('focus', run);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', run);
      window.removeEventListener('focus', run);
    };
  }, [intervalMs]);
}
