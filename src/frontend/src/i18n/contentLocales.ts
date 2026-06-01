// Content locales of the multilingual editor (titles, descriptions, tasks).
// An organization enables any set of locale codes in its `languages` setting.
// Names come from Intl.DisplayNames.

export type Locale = string;

/** German is always present and is the required / fallback locale. */
export const REQUIRED_LOCALE = 'de';

// Suggestions of the Settings "add language" picker (ISO 639-1). Authors can
// also type any other code.
export const COMMON_LOCALES: string[] = [
  'de', 'en', 'fr', 'es', 'it', 'pt', 'nl', 'pl', 'ru', 'uk', 'tr', 'ar', 'he',
  'fa', 'ur', 'hi', 'bn', 'zh', 'ja', 'ko', 'vi', 'th', 'id', 'ms', 'cs', 'sk',
  'hu', 'ro', 'bg', 'el', 'sv', 'da', 'fi', 'nb', 'sr', 'hr', 'sl', 'lt', 'lv',
  'et', 'sq', 'sw',
];

const RTL_BASES = new Set(['ar', 'he', 'fa', 'ur', 'ps', 'sd', 'ug', 'yi', 'dv', 'ckb']);

/** True for right-to-left scripts (input/text direction). */
export function isRtlLocale(code: string): boolean {
  return RTL_BASES.has(code.toLowerCase().split('-')[0]);
}

/**
 * Resolve a multilingual content value to a display string **plus the locale
 * it resolved to**.
 *
 * Order: the base code of the UI language, then German, then English. Empty
 * strings fall through like missing keys. Content is often authored in German
 * only and leaves the other locales as `""`, where `??` would render a blank.
 *
 * Callers set the text direction from the locale (`dirAttr`). Content RTL is
 * per field, the UI chrome stays LTR.
 */
export function resolveLocalized(
  ml: Record<string, string> | null | undefined,
  uiLang: string,
): { text: string; locale: Locale | null } {
  if (!ml) return { text: '', locale: null };
  const base = uiLang.toLowerCase().split('-')[0];
  for (const candidate of [base, REQUIRED_LOCALE, 'en']) {
    if (ml[candidate]) return { text: ml[candidate], locale: candidate };
  }
  return { text: '', locale: null };
}

/**
 * `resolveLocalized` without the locale. Returns `''` only when nothing is
 * set, so a caller that wants a fallback appends `|| fallback`.
 */
export function localizedText(
  ml: Record<string, string> | null | undefined,
  uiLang: string,
): string {
  return resolveLocalized(ml, uiLang).text;
}

/** `dir` attribute for content in `locale`. `undefined` inherits LTR. */
export function dirAttr(locale: Locale | null): 'rtl' | undefined {
  return locale && isRtlLocale(locale) ? 'rtl' : undefined;
}

/** Human-readable language name for any code, localized to the UI language. */
export function localeLabel(code: string, uiLang?: string): string {
  try {
    const name = new Intl.DisplayNames([uiLang || 'en'], { type: 'language' }).of(code);
    if (name && name.toLowerCase() !== code.toLowerCase()) return name;
  } catch {
    /* unknown code — fall through */
  }
  return code.toUpperCase();
}

/**
 * Normalize an organization's content locales: trimmed, lowercase, unique, no
 * blanks. The required locale comes first, the rest keep the org's order.
 */
export function resolveContentLocales(orgLanguages: readonly string[] | undefined | null): Locale[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of [REQUIRED_LOCALE, ...(orgLanguages ?? [])]) {
    const code = (raw ?? '').trim().toLowerCase();
    if (code && !seen.has(code)) {
      seen.add(code);
      out.push(code);
    }
  }
  return out;
}
