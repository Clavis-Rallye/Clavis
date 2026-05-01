import { describe, it, expect } from 'vitest';
import { resolveDefaultLanguage } from './resolveDefaultLanguage';

function makeStorage(value: string | null) {
  return { getItem: () => value };
}

describe('resolveDefaultLanguage', () => {
  it('priority 1: URL parameter wins over everything', () => {
    expect(
      resolveDefaultLanguage('de', {
        storage: makeStorage('en'),
        navigatorLang: 'en-US',
      }),
    ).toBe('de');
  });

  it('priority 2: LocalStorage wins when no URL parameter', () => {
    expect(
      resolveDefaultLanguage(null, {
        storage: makeStorage('de'),
        navigatorLang: 'en-US',
      }),
    ).toBe('de');
  });

  it('priority 3: navigator.language is used when URL+storage are empty', () => {
    expect(
      resolveDefaultLanguage(null, {
        storage: makeStorage(null),
        navigatorLang: 'de-DE',
      }),
    ).toBe('de');
  });

  it('priority 4: falls back to "en" when nothing else matches', () => {
    expect(
      resolveDefaultLanguage(null, {
        storage: makeStorage(null),
        navigatorLang: 'fr-FR',
      }),
    ).toBe('en');
  });

  it('normalizes regional variants (de-CH → de, en-GB → en)', () => {
    expect(
      resolveDefaultLanguage('de-CH', { storage: makeStorage(null), navigatorLang: 'fr' }),
    ).toBe('de');
    expect(
      resolveDefaultLanguage(null, { storage: makeStorage(null), navigatorLang: 'en-GB' }),
    ).toBe('en');
  });

  it('ignores unsupported URL/storage values and falls through', () => {
    expect(
      resolveDefaultLanguage('fr', {
        storage: makeStorage('de'),
        navigatorLang: 'en-US',
      }),
    ).toBe('de');
    expect(
      resolveDefaultLanguage('xx', {
        storage: makeStorage('yy'),
        navigatorLang: 'fr-FR',
      }),
    ).toBe('en');
  });
});
