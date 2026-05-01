/** The interface languages, in the order the language switchers list them. */
export const SUPPORTED_LANGUAGES = ['de', 'en'] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

const LANGUAGE_STORAGE_KEY = 'clavis_language';

function normalize(value: string | null | undefined): SupportedLanguage | null {
  if (!value) return null;
  const head = value.toLowerCase().split('-')[0];
  if (head === 'de' || head === 'en') return head;
  return null;
}

/**
 * Resolve the default language of the Welcome screen dropdown. Order: the URL
 * `?lang=`, the previous choice that i18next keeps in localStorage,
 * `navigator.language`, then `'en'`. Each input counts by its base code
 * (`de-CH` is `de`), and an unsupported one falls through to the next.
 */
export function resolveDefaultLanguage(
  urlLang: string | null,
  options?: {
    storage?: Pick<Storage, 'getItem'>;
    navigatorLang?: string;
  },
): SupportedLanguage {
  const urlMatch = normalize(urlLang);
  if (urlMatch) return urlMatch;

  const storage = options?.storage ?? (typeof localStorage !== 'undefined' ? localStorage : undefined);
  const stored = normalize(storage?.getItem(LANGUAGE_STORAGE_KEY) ?? null);
  if (stored) return stored;

  const browser = normalize(
    options?.navigatorLang ?? (typeof navigator !== 'undefined' ? navigator.language : null),
  );
  if (browser) return browser;

  return 'en';
}
