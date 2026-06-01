import { describe, it, expect } from 'vitest';
import { dirAttr, isRtlLocale, localizedText, resolveLocalized } from './contentLocales';

describe('resolveLocalized', () => {
  const ml = { de: 'Hallo', en: 'Hello', ar: 'مرحبا' };

  it('resolves the UI language base code first (ar browser gets ar content)', () => {
    expect(resolveLocalized(ml, 'ar')).toEqual({ text: 'مرحبا', locale: 'ar' });
    expect(resolveLocalized(ml, 'ar-EG')).toEqual({ text: 'مرحبا', locale: 'ar' });
  });

  it('resolves de and en directly', () => {
    expect(resolveLocalized(ml, 'de-DE')).toEqual({ text: 'Hallo', locale: 'de' });
    expect(resolveLocalized(ml, 'en-US')).toEqual({ text: 'Hello', locale: 'en' });
  });

  it('falls back to German, then English, skipping empty strings', () => {
    expect(resolveLocalized({ de: 'Hallo', fa: '' }, 'fa')).toEqual({
      text: 'Hallo',
      locale: 'de',
    });
    expect(resolveLocalized({ de: '', en: 'Hello' }, 'fr')).toEqual({
      text: 'Hello',
      locale: 'en',
    });
  });

  it('returns empty text and null locale when nothing is set', () => {
    expect(resolveLocalized({ de: '', en: '' }, 'de')).toEqual({ text: '', locale: null });
    expect(resolveLocalized(null, 'de')).toEqual({ text: '', locale: null });
    expect(resolveLocalized(undefined, 'de')).toEqual({ text: '', locale: null });
  });

  it('localizedText returns just the resolved text', () => {
    expect(localizedText(ml, 'ar')).toBe('مرحبا');
    expect(localizedText({ de: 'Nur Deutsch' }, 'en')).toBe('Nur Deutsch');
    expect(localizedText(null, 'de')).toBe('');
  });
});

describe('dirAttr', () => {
  it('returns rtl for right-to-left locales', () => {
    expect(dirAttr('ar')).toBe('rtl');
    expect(dirAttr('fa')).toBe('rtl');
    expect(dirAttr('he')).toBe('rtl');
  });

  it('returns undefined for LTR locales and null', () => {
    expect(dirAttr('de')).toBeUndefined();
    expect(dirAttr('en')).toBeUndefined();
    expect(dirAttr(null)).toBeUndefined();
  });

  it('matches isRtlLocale for region-suffixed codes', () => {
    expect(isRtlLocale('ar-EG')).toBe(true);
    expect(dirAttr('ar-EG')).toBe('rtl');
  });
});
