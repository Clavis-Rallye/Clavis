import { useEffect, useState } from 'react';
import { getOrg } from '../api/admin';

/** organizations.settings key of the automatic start (backend services/run_start.py). */
export const AUTO_START_SETTING = 'auto_start_runs';

/**
 * Whether the organization starts planned runs at their time. Missing means on,
 * like the backend. Null while unknown.
 */
export function useAutoStartRuns(orgSlug: string | undefined): boolean | null {
  const [value, setValue] = useState<boolean | null>(null);
  useEffect(() => {
    if (!orgSlug) return;
    let cancelled = false;
    getOrg(orgSlug)
      .then((org) => {
        if (!cancelled) setValue(org.settings?.[AUTO_START_SETTING] !== false);
      })
      .catch(() => {
        if (!cancelled) setValue(null);
      });
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);
  return value;
}
