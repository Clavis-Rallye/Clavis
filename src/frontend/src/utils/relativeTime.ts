// Shared "X minutes/hours/days ago" formatter for feed events and the
// leaderboard's last-scored timestamp — both use the same runs:participant
// namespace copy, so one place computes the bucket and looks it up.

import type { TFunction } from 'i18next';

export function formatRelativeTime(iso: string, now: number, t: TFunction): string {
  const minutes = Math.floor((now - Date.parse(iso)) / 60_000);
  if (minutes < 1) return t('participant.feed.relativeTime.justNow');
  if (minutes < 60) return t('participant.feed.relativeTime.minutesAgo', { minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t('participant.feed.relativeTime.hoursAgo', { hours });
  const days = Math.floor(hours / 24);
  return t('participant.feed.relativeTime.daysAgo', { days });
}
