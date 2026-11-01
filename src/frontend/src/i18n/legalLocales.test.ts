import { describe, it, expect } from 'vitest';
import deLegal from './locales/de/legal.json';
import enLegal from './locales/en/legal.json';

// The bundled copy of the legal pages shows when no operator has published a
// text. So it cannot name an operator, a contact or a deadline.
describe('bundled legal fallback', () => {
  it('names the pages as the statements they are', () => {
    expect(deLegal.privacy.title).toBe('Datenschutzerklärung');
    expect(deLegal.accessibility.title).toBe('Erklärung zur Barrierefreiheit');
    expect(enLegal.privacy.title).toBe('Privacy notice');
    expect(enLegal.accessibility.title).toBe('Accessibility statement');
  });

  it('promises no answer within a fixed time', () => {
    expect(deLegal.accessibility.contactBody).not.toMatch(/Wochen|innerhalb/);
    expect(enLegal.accessibility.contactBody).not.toMatch(/weeks|within/);
  });

  it('points to no contact in an imprint that the fallback does not have', () => {
    for (const body of [deLegal.accessibility.contactBody, deLegal.privacy.rightsBody]) {
      expect(body).not.toMatch(/Impressum/);
    }
    for (const body of [enLegal.accessibility.contactBody, enLegal.privacy.rightsBody]) {
      expect(body).not.toMatch(/imprint/i);
    }
  });

  it('keeps the notice that the final text is missing', () => {
    expect(deLegal.placeholderNotice).toBe(
      'Der Betreiber hat den endgültigen Text noch nicht veröffentlicht.',
    );
    expect(enLegal.placeholderNotice).toBe('The operator has not yet published the final text.');
  });

  it('has the same keys in both languages', () => {
    const keys = (value: object): string[] =>
      Object.entries(value).flatMap(([key, inner]) =>
        typeof inner === 'object' && inner !== null ? keys(inner).map((k) => `${key}.${k}`) : [key],
      );
    expect(keys(deLegal).sort()).toEqual(keys(enLegal).sort());
  });
});
