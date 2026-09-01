/**
 * Date formatting for staff screens.
 *
 * A two-digit year ("01.02.26") is ambiguous, and seconds make a table cell
 * wrap without adding information. These helpers pin the format, so every
 * staff screen prints the same shape:
 *
 * - `formatDate`: medium date, four-digit year ("1. Feb. 2026").
 * - `formatDateTime`: the same date plus hours and minutes, no seconds.
 * - `formatTime`: hours and minutes only.
 *
 * The UI language is `de` or `en`, but the region decides the order and the
 * separators. So each language maps to the region we ship for (de → de-DE,
 * en → en-GB), because a bare `en` would give US order.
 */

const LOCALES: Record<string, string> = {
  de: 'de-DE',
  en: 'en-GB',
};

function localeOf(lang: string): string {
  const base = (lang || 'de').toLowerCase().split('-')[0];
  return LOCALES[base] ?? LOCALES.de;
}

/** Parses an ISO timestamp, or returns null when it is not a usable date. */
function parse(iso: string): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * An unparsable input comes back unchanged: the raw value tells the operator
 * more than "Invalid Date".
 */
export function formatDate(iso: string, lang: string): string {
  const date = parse(iso);
  if (!date) return iso ?? '';
  return new Intl.DateTimeFormat(localeOf(lang), { dateStyle: 'medium' }).format(date);
}

/**
 * Prints, for example, "1. Feb. 2026, 14:30". An unparsable input comes back
 * unchanged.
 */
export function formatDateTime(iso: string, lang: string): string {
  const date = parse(iso);
  if (!date) return iso ?? '';
  return new Intl.DateTimeFormat(localeOf(lang), {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

/**
 * Prints "14:30". The browser locale must not decide it: a German page on an
 * en-US browser would print "9:13:43 AM". An unparsable input comes back
 * unchanged.
 */
export function formatTime(iso: string, lang: string): string {
  const date = parse(iso);
  if (!date) return iso ?? '';
  return new Intl.DateTimeFormat(localeOf(lang), { timeStyle: 'short' }).format(date);
}
