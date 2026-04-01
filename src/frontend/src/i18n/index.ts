import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

import deCommon from './locales/de/common.json';
import deAuth from './locales/de/auth.json';
import deContent from './locales/de/content.json';
import deRuns from './locales/de/runs.json';
import deAdmin from './locales/de/admin.json';
import deLegal from './locales/de/legal.json';
import deWelcome from './locales/de/welcome.json';
import deEntryChoice from './locales/de/entryChoice.json';
import deProfileSetup from './locales/de/profileSetup.json';
import deOnboarding from './locales/de/onboarding.json';
import deInstance from './locales/de/instance.json';
import deQa from './locales/de/qa.json';
import deLegalTemplates from './locales/de/legalTemplates.json';
import enCommon from './locales/en/common.json';
import enAuth from './locales/en/auth.json';
import enContent from './locales/en/content.json';
import enRuns from './locales/en/runs.json';
import enAdmin from './locales/en/admin.json';
import enLegal from './locales/en/legal.json';
import enWelcome from './locales/en/welcome.json';
import enEntryChoice from './locales/en/entryChoice.json';
import enProfileSetup from './locales/en/profileSetup.json';
import enOnboarding from './locales/en/onboarding.json';
import enInstance from './locales/en/instance.json';
import enQa from './locales/en/qa.json';
import enLegalTemplates from './locales/en/legalTemplates.json';

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      de: { common: deCommon, auth: deAuth, content: deContent, runs: deRuns, admin: deAdmin, legal: deLegal, welcome: deWelcome, entryChoice: deEntryChoice, profileSetup: deProfileSetup, onboarding: deOnboarding, instance: deInstance, qa: deQa, legalTemplates: deLegalTemplates },
      // The three legal templates in `legalTemplates` exist in German only,
      // because they are legal texts for a German operator.
      // `utils/legalTemplates.ts` reads them from `de` in every UI language.
      // English has the namespace only for `mapLoadFailed`, a message of the UI.
      en: { common: enCommon, auth: enAuth, content: enContent, runs: enRuns, admin: enAdmin, legal: enLegal, welcome: enWelcome, entryChoice: enEntryChoice, profileSetup: enProfileSetup, onboarding: enOnboarding, instance: enInstance, qa: enQa, legalTemplates: enLegalTemplates },
    },
    // No `lng`: the LanguageDetector resolves it. A fixed `lng` would override
    // the detector on every init and reset the choice that
    // `i18n.changeLanguage` saved.
    fallbackLng: 'en',
    defaultNS: 'common',
    ns: ['common', 'auth', 'content', 'runs', 'admin', 'legal', 'welcome', 'entryChoice', 'profileSetup', 'onboarding', 'instance', 'qa', 'legalTemplates'],
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'clavis_language',
    },
    interpolation: {
      escapeValue: false,
      // Locale strings name the product as `{{brand}}`. The default matches
      // the server's BRAND_NAME fallback. `config/instanceConfig.ts` replaces
      // it with the answer of `GET /api/public/config`.
      defaultVariables: { brand: 'Clavis' },
    },
  });

// The `lang` attribute of the document follows the UI language, so a screen
// reader picks the right voice and pronunciation. `resolvedLanguage` turns a
// detected `en-US` into `en`. `language` covers the case where nothing has
// resolved yet.
function applyDocumentLanguage(): void {
  const lang = i18n.resolvedLanguage ?? i18n.language;
  if (typeof document !== 'undefined' && lang) {
    document.documentElement.lang = lang;
  }
}

applyDocumentLanguage();
i18n.on('languageChanged', applyDocumentLanguage);

export default i18n;
