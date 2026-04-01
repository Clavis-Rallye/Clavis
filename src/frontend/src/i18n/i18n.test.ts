import { describe, it, expect, beforeEach, vi } from 'vitest';
import i18n from './index';
import deCommon from './locales/de/common.json';
import enCommon from './locales/en/common.json';
import deAuth from './locales/de/auth.json';
import enAuth from './locales/en/auth.json';

describe('i18n', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
  });

  it('starts in German by default', () => {
    expect(i18n.language).toBe('de');
  });

  it('switches language at runtime', async () => {
    await i18n.changeLanguage('en');
    expect(i18n.language).toBe('en');
    expect(i18n.t('nav.dashboard', { ns: 'common' })).toBe('Dashboard');
  });

  it('falls back to English for missing German keys', async () => {
    await i18n.changeLanguage('de');
    expect(i18n.t('missing.nonexistent.key')).toBe('missing.nonexistent.key');
  });

  it('de and en common.json have identical key shape', () => {
    expect(Object.keys(deCommon).sort()).toEqual(Object.keys(enCommon).sort());
  });

  it('de and en auth.json have identical top-level keys', () => {
    expect(Object.keys(deAuth).sort()).toEqual(Object.keys(enAuth).sort());
  });

  it('auth.register.errors has the same keys in both locales', () => {
    expect(Object.keys(deAuth.register.errors).sort()).toEqual(
      Object.keys(enAuth.register.errors).sort(),
    );
  });

  // Change path: every `changeLanguage` moves the `lang` attribute with
  // it. `beforeEach` has just applied German, so the attribute starts at 'de'.
  it('sets the document language from the resolved language', async () => {
    expect(document.documentElement.lang).toBe(i18n.resolvedLanguage);
    expect(document.documentElement.lang).toBe('de');

    await i18n.changeLanguage('en');
    expect(document.documentElement.lang).toBe('en');
  });

  // Init path: the module must set the attribute while it initialises,
  // not only from the `languageChanged` handler. The test above cannot show that,
  // because the suite's `beforeEach` and the global setup both call
  // `changeLanguage` first. So drop the module cache, clear the attribute and
  // let a fresh `import` run the init again — with no language change at all.
  it('sets the document language at init, without a language change', async () => {
    const savedLanguage = localStorage.getItem('clavis_language');
    try {
      // English, which is not the language the suite runs in. A leftover 'de'
      // attribute therefore cannot make this test pass.
      localStorage.setItem('clavis_language', 'en');
      document.documentElement.lang = '';
      vi.resetModules();

      const freshI18n = (await import('./index')).default;

      expect(document.documentElement.lang).toBe('en');
      expect(document.documentElement.lang).toBe(freshI18n.resolvedLanguage);
    } finally {
      if (savedLanguage === null) {
        localStorage.removeItem('clavis_language');
      } else {
        localStorage.setItem('clavis_language', savedLanguage);
      }
    }
  });

  // The sidebar group labels are German in German and English in English.
  it('has a group label per nav group in both locales', () => {
    expect(deCommon.nav.groups.coordination).toBe('Spielleitung');
    expect(deCommon.nav.groups.creation).toBe('Erstellung');
    expect(deCommon.nav.groups.administration).toBe('Verwaltung');
    expect(enCommon.nav.groups.coordination).toBe('Coordination');
    expect(enCommon.nav.groups.creation).toBe('Creation');
    expect(enCommon.nav.groups.administration).toBe('Administration');
  });

  it('every common.nav key resolves in both locales', async () => {
    for (const lang of ['de', 'en']) {
      await i18n.changeLanguage(lang);
      const nav = i18n.t('nav', { ns: 'common', returnObjects: true }) as Record<string, string>;
      for (const key of Object.keys(nav)) {
        expect(nav[key]).toBeTruthy();
      }
    }
  });
});
