import { useCallback, useMemo, useSyncExternalStore } from 'react';
import type { FeedEvent, FeedEventType } from '../api/types';

/** The four kinds the coordination can push at a run; everything else on the
 *  feed narrates what participants themselves did. */
const COORDINATION_NOTICE_TYPES: readonly FeedEventType[] = [
  'announcement',
  'countdown',
  'reminder',
  'alert',
];

export function isCoordinationNotice(event: FeedEvent): boolean {
  return COORDINATION_NOTICE_TYPES.includes(event.event_type);
}

/** localStorage key per run: epoch ms of the participant's last visit to the
 *  activity tab. Notices created after that time are unread. */
const lastSeenKey = (runId: string) => `clavis_feed_last_seen_${runId}`;

interface RunNotices {
  /** Coordination notices this client knows about, oldest first. */
  events: FeedEvent[];
  /** Epoch ms of the last visit to the activity tab; null = never visited. */
  lastSeen: number | null;
}

const NO_NOTICES: RunNotices = { events: [], lastSeen: null };

// Module-level registry: the run shell's WS handler, the feed's initial list
// and the bottom nav's badge are three components with no common renderer —
// they share one per-run view of the notices through this store.
const runs = new Map<string, RunNotices>();
const listeners = new Map<string, Set<() => void>>();

function readLastSeen(runId: string): number | null {
  try {
    const raw = localStorage.getItem(lastSeenKey(runId));
    if (raw === null) return null;
    const ts = Number(raw);
    return Number.isFinite(ts) ? ts : null;
  } catch {
    return null; // storage unavailable (private mode, quota, …)
  }
}

function writeLastSeen(runId: string, seenAt: number): void {
  try {
    localStorage.setItem(lastSeenKey(runId), String(seenAt));
  } catch {
    /* unpersisted — the badge then only lives for this session */
  }
}

function entryOf(runId: string): RunNotices {
  let entry = runs.get(runId);
  if (!entry) {
    entry = { events: [], lastSeen: readLastSeen(runId) };
    runs.set(runId, entry);
  }
  return entry;
}

function emit(runId: string): void {
  listeners.get(runId)?.forEach((listener) => listener());
}

function subscribe(runId: string, onChange: () => void): () => void {
  let set = listeners.get(runId);
  if (!set) {
    set = new Set();
    listeners.set(runId, set);
  }
  set.add(onChange);
  return () => set.delete(onChange);
}

/** Record coordination notices into the registry: the feed seeds it with its
 *  loaded list, the run shell's WS handler adds live `feed_event`s. Ids
 *  already known are ignored, so both paths can deliver the same notice. */
function recordRunNotices(runId: string, incoming: FeedEvent[]): void {
  const current = entryOf(runId);
  const known = new Set(current.events.map((ev) => ev.id));
  const added = incoming.filter((ev) => isCoordinationNotice(ev) && !known.has(ev.id));
  if (added.length === 0) return;
  const events = [...current.events, ...added].sort(
    (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at),
  );
  runs.set(runId, { ...current, events });
  emit(runId);
}

/** Mark everything up to `seenAt` (default: now) as seen for this run. */
function markRunSeen(runId: string, seenAt: number = Date.now()): void {
  const current = entryOf(runId);
  if (current.lastSeen !== null && current.lastSeen >= seenAt) return;
  writeLastSeen(runId, seenAt);
  runs.set(runId, { ...current, lastSeen: seenAt });
  emit(runId);
}

/** Snapshot the last-seen boundary without subscribing — used by the run
 *  shell's one-shot paged history load, which only needs the value once, at
 *  the start of its own fetch loop. */
export function peekLastSeen(runId: string): number | null {
  return entryOf(runId).lastSeen;
}

/** Subscription to a run's notices — the one registry both notice hooks read:
 *  `useActiveCountdown` watches the same events `useUnreadNotices` feeds. */
export function useCoordinationNotices(runId: string | null | undefined): RunNotices {
  const subscribeRun = useCallback(
    (onChange: () => void) => (runId ? subscribe(runId, onChange) : () => {}),
    [runId],
  );
  const getSnapshot = useCallback(
    () => (runId ? entryOf(runId) : NO_NOTICES),
    [runId],
  );
  return useSyncExternalStore(subscribeRun, getSnapshot, getSnapshot);
}

interface UseUnreadNoticesResult {
  /** Coordination notices this participant has not seen yet. */
  unreadCount: number;
  recordNotices: (events: FeedEvent[]) => void;
  markSeen: () => void;
}

/**
 * How many coordination notices are waiting on the activity tab: everything
 * recorded for the run that was created after the last time the participant
 * opened that tab (persisted per run in localStorage, so a reload doesn't turn
 * read notices unread again).
 */
export function useUnreadNotices(runId: string | null | undefined): UseUnreadNoticesResult {
  const entry = useCoordinationNotices(runId);
  const recordNotices = useCallback(
    (events: FeedEvent[]) => {
      if (runId) recordRunNotices(runId, events);
    },
    [runId],
  );
  const markSeen = useCallback(
    () => {
      if (runId) markRunSeen(runId);
    },
    [runId],
  );
  const unreadCount = useMemo(
    () =>
      entry.events.filter(
        (ev) => entry.lastSeen === null || Date.parse(ev.created_at) > entry.lastSeen,
      ).length,
    [entry],
  );
  return { unreadCount, recordNotices, markSeen };
}

/** Test-only: drop the in-memory registry (localStorage is not touched). */
export function __resetNoticeStoreForTests(): void {
  runs.clear();
  listeners.clear();
}
