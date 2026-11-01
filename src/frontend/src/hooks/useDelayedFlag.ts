import { useEffect, useState } from 'react';

/**
 * A wait under `delayMs` reads as instant, so only a longer wait needs a
 * progress indicator. The flag stays false until `active` has been true for
 * `delayMs`, and turns false at once when `active` becomes false. A fast
 * lookup then flashes no spinner.
 */
export function useDelayedFlag(active: boolean, delayMs = 400): boolean {
  const [flag, setFlag] = useState(false);
  useEffect(() => {
    if (!active) {
      setFlag(false);
      return;
    }
    const timer = setTimeout(() => setFlag(true), delayMs);
    return () => clearTimeout(timer);
  }, [active, delayMs]);
  return flag;
}
