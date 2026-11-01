import { useEffect } from 'react';
import { useLocation } from 'react-router';
import { useTranslation } from 'react-i18next';
import { useInstanceConfig } from '../config/instanceConfig';
import { findPageTitleKey } from './pageTitles';

/**
 * Sets `document.title` to "<page name> · <brand>" on every route, so a screen
 * reader user and anyone with several tabs can tell the pages apart. A route
 * without an entry in `pageTitles.ts` gets the brand name alone.
 *
 * `instanceConfig.ts` writes the plain brand name at boot, before the first
 * route is known. This component takes over from the first render and follows
 * the route, the interface language and the configured brand.
 */
export default function DocumentTitle() {
  const { pathname } = useLocation();
  const { t, i18n } = useTranslation();
  const { brand_name: brand } = useInstanceConfig();
  const key = findPageTitleKey(pathname);
  // `i18n.language` is a dependency, so a language switch writes the title again.
  const pageName = key ? t(key) : '';

  useEffect(() => {
    document.title = pageName ? `${pageName} · ${brand}` : brand;
  }, [pageName, brand, i18n.language]);

  return null;
}
