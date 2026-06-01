import { createContext, useContext } from 'react';
import type { Locale } from '../i18n/contentLocales';

interface OrgContextValue {
  /** Display name of the current organization (null until loaded). */
  name: string | null;
  /** Content locales enabled for this org — drives the multilingual inputs. */
  languages: Locale[];
  /** Re-fetch the org (e.g. after Settings changes the enabled languages). */
  refresh: () => void;
}

// Sensible defaults for any consumer rendered outside the provider (e.g. unit
// tests): German + English, German required.
export const OrgContext = createContext<OrgContextValue>({
  name: null,
  languages: ['de', 'en'],
  refresh: () => {},
});

export const useOrg = (): OrgContextValue => useContext(OrgContext);
