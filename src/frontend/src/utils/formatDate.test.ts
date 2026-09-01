import { describe, it, expect } from 'vitest';
import { formatDate, formatDateTime } from './formatDate';

const ISO = '2026-02-01T14:30:45Z';

describe('formatDate', () => {
  it('prints a four-digit year in German', () => {
    const out = formatDate(ISO, 'de');
    expect(out).toContain('2026');
    expect(out).not.toMatch(/\b26\b/);
  });

  it('prints a four-digit year in English', () => {
    const out = formatDate(ISO, 'en');
    expect(out).toContain('2026');
    expect(out).not.toMatch(/\b26\b/);
  });

  it('differs between German and English (region-aware, not one fixed string)', () => {
    expect(formatDate(ISO, 'de')).not.toBe(formatDate(ISO, 'en'));
  });

  it('accepts a region tag such as de-AT and falls back to German', () => {
    expect(formatDate(ISO, 'de-AT')).toBe(formatDate(ISO, 'de'));
  });

  it('returns an unparsable input unchanged', () => {
    expect(formatDate('not-a-date', 'de')).toBe('not-a-date');
    expect(formatDate('', 'de')).toBe('');
  });
});

describe('formatDateTime', () => {
  it('prints hours and minutes but no seconds in German', () => {
    const out = formatDateTime(ISO, 'de');
    expect(out).toContain('2026');
    expect(out).toMatch(/\d{1,2}:\d{2}/);
    expect(out).not.toMatch(/\d{1,2}:\d{2}:\d{2}/);
  });

  it('prints hours and minutes but no seconds in English', () => {
    const out = formatDateTime(ISO, 'en');
    expect(out).toContain('2026');
    expect(out).toMatch(/\d{1,2}:\d{2}/);
    expect(out).not.toMatch(/\d{1,2}:\d{2}:\d{2}/);
  });

  it('adds a time to what formatDate prints', () => {
    expect(formatDateTime(ISO, 'de').length).toBeGreaterThan(formatDate(ISO, 'de').length);
  });

  it('returns an unparsable input unchanged', () => {
    expect(formatDateTime('nope', 'en')).toBe('nope');
  });
});
