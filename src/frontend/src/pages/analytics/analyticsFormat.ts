/**
 * Number, time and percent formats of the analytics views.
 *
 * Every value reads in the reader's format: "42,5" and "66,7 %" on a German
 * screen, "42.5" and "66.7%" on an English one. A missing value is a dash,
 * never 0, because "no team measured" and "zero minutes" mean different
 * things.
 */

export const NO_VALUE = '—';

/** Seconds as minutes with one decimal for a chart axis. Null stays null, so the chart leaves a gap. */
export function toMinutes(seconds: number | null | undefined): number | null {
  return seconds == null ? null : Math.round((seconds / 60) * 10) / 10;
}

export function formatNumber(value: number | null | undefined, lang: string, digits = 1): string {
  if (value == null) return NO_VALUE;
  return new Intl.NumberFormat(lang, { maximumFractionDigits: digits }).format(value);
}

/** A share given as 0 to 100. */
export function formatPercent(value: number | null | undefined, lang: string): string {
  if (value == null) return NO_VALUE;
  return new Intl.NumberFormat(lang, { style: 'percent', maximumFractionDigits: 1 }).format(value / 100);
}

/**
 * A duration in the reader's words: "42 Min." below an hour, "1 Std. 35 Min."
 * from an hour on. Below ten minutes it keeps one decimal, "2,5 Min.".
 */
export function formatDuration(seconds: number | null | undefined, lang: string): string {
  if (seconds == null) return NO_VALUE;
  const german = lang.toLowerCase().startsWith('de');
  const minuteWord = german ? 'Min.' : 'min';
  const totalMinutes = seconds / 60;
  if (totalMinutes < 9.95) return `${formatNumber(totalMinutes, lang, 1)} ${minuteWord}`;
  // Round first: 59,6 minutes are "1 Std. 0 Min.", not "60 Min.".
  const rounded = Math.round(totalMinutes);
  if (rounded < 60) return `${rounded} ${minuteWord}`;
  const hours = Math.floor(rounded / 60);
  const minutes = rounded % 60;
  return `${hours} ${german ? 'Std.' : 'h'} ${minutes} ${minuteWord}`;
}

/** The middle half of the teams: "22 bis 28 Min.", or a dash without both ends. */
export function formatSpread(
  low: number | null | undefined,
  high: number | null | undefined,
  lang: string,
): string {
  if (low == null || high == null) return NO_VALUE;
  const joiner = lang.toLowerCase().startsWith('de') ? 'bis' : 'to';
  return `${formatDuration(low, lang)} ${joiner} ${formatDuration(high, lang)}`;
}

/** The time of day of a moment, "14:05". */
export function formatClock(iso: string | null | undefined, lang: string): string {
  if (!iso) return NO_VALUE;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return NO_VALUE;
  return new Intl.DateTimeFormat(lang, { hour: '2-digit', minute: '2-digit' }).format(date);
}

/**
 * The colours of the runs in the comparison. The app's blue leads. Checked
 * as a categorical palette against the light surface, in this order. The worst
 * adjacent colour distance (ΔE) is 9.8 under a colour-vision deficiency and
 * 16.1 for normal vision. Every colour has a contrast of at least 3:1.
 */
export const RUN_COLORS = ['#2850A9', '#D0632C', '#1B9E77', '#A57D00', '#C24C86', '#7059D0'] as const;

/**
 * A run that stays keeps its colour slot, and a new run takes the lowest free
 * slot. So removing one run never repaints the others.
 */
export function assignSlots(previous: Record<string, number>, runIds: string[]): Record<string, number> {
  const next: Record<string, number> = {};
  runIds.forEach((id) => {
    if (previous[id] !== undefined) next[id] = previous[id];
  });
  const used = new Set(Object.values(next));
  runIds.forEach((id) => {
    if (next[id] !== undefined) return;
    let slot = 0;
    while (used.has(slot)) slot += 1;
    next[id] = slot;
    used.add(slot);
  });
  return next;
}
