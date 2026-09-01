import { useEffect, useMemo, useState } from 'react';
import type { FeedEvent } from '../api/types';
import { useCoordinationNotices } from './useUnreadNotices';

function targetTimeOf(event: FeedEvent): string | undefined {
  const target = (event.content as { target_time?: unknown }).target_time;
  return typeof target === 'string' && Number.isFinite(new Date(target).getTime())
    ? target
    : undefined;
}

/** The running countdown: the target time of the most recently pushed
 *  countdown notice, while that target still lies in the future. A later
 *  countdown supersedes earlier ones outright. Once its own target passes there
 *  is nothing, even if an older countdown's target is still ahead. */
function pickRunningCountdown(events: FeedEvent[], now: number): string | null {
  let target: string | null = null;
  let pushedAt = Number.NEGATIVE_INFINITY;
  for (const event of events) {
    if (event.event_type !== 'countdown') continue;
    const time = targetTimeOf(event);
    if (!time) continue;
    const createdAt = Date.parse(event.created_at);
    if (createdAt > pushedAt) {
      target = time;
      pushedAt = createdAt;
    }
  }
  return target !== null && new Date(target).getTime() > now ? target : null;
}

/**
 * The countdown notice running for this run, as an ISO target time, or null.
 * It ticks every second while one runs, so it disappears the moment its target
 * time passes, wherever in the run the participant is.
 */
export function useActiveCountdown(runId: string | null | undefined): string | null {
  const { events } = useCoordinationNotices(runId);
  const [now, setNow] = useState(() => Date.now());
  const targetTime = useMemo(() => pickRunningCountdown(events, now), [events, now]);

  useEffect(() => {
    if (!targetTime) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [targetTime]);

  return targetTime;
}
