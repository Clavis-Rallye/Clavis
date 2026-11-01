import { describe, it, expect } from 'vitest';
import { findLegalPlaceholders } from './legalPlaceholders';

describe('findLegalPlaceholders', () => {
  it('returns nothing for an empty, missing or finished text', () => {
    expect(findLegalPlaceholders('')).toEqual([]);
    expect(findLegalPlaceholders(null)).toEqual([]);
    expect(findLegalPlaceholders(undefined)).toEqual([]);
    expect(findLegalPlaceholders('<p>Stadt Musterstadt, Rathausplatz 1</p>')).toEqual([]);
  });

  it('finds a placeholder in capitals, umlauts included', () => {
    expect(findLegalPlaceholders('<p>Anbieter: [NAME DER ORGANISATION]</p>')).toEqual([
      '[NAME DER ORGANISATION]',
    ]);
    expect(findLegalPlaceholders('[ÖFFENTLICHE STELLE]')).toEqual(['[ÖFFENTLICHE STELLE]']);
  });

  it('lists each placeholder once, in the order of the text', () => {
    const html =
      '<p>[E-MAIL-ADRESSE]<br>[TELEFONNUMMER]</p><p>Schreibe an [E-MAIL-ADRESSE].</p>';
    expect(findLegalPlaceholders(html)).toEqual(['[E-MAIL-ADRESSE]', '[TELEFONNUMMER]']);
  });

  it('finds a placeholder that carries mixed-case text after its first capital', () => {
    const html = '<li><p>[FÜR VEREINE: Art. 6 Abs. 1 Buchstabe b DSGVO.]</p></li>';
    expect(findLegalPlaceholders(html)).toEqual(['[FÜR VEREINE: Art. 6 Abs. 1 Buchstabe b DSGVO.]']);
  });

  it('finds a placeholder that opens with a number and a word in capitals', () => {
    expect(findLegalPlaceholders('<p><strong>Gastkonten:</strong> [90 TAGE] nach dem Ende.</p>')).toEqual([
      '[90 TAGE]',
    ]);
  });

  it('finds a placeholder that the editor split with markup', () => {
    expect(findLegalPlaceholders('<p>[NAME <strong>DER</strong> ORGANISATION]</p>')).toEqual([
      '[NAME DER ORGANISATION]',
    ]);
  });

  it('ignores brackets that are no placeholder', () => {
    expect(findLegalPlaceholders('<p>Siehe Fußnote [1], Stand [2026] und [siehe oben].</p>')).toEqual([]);
    expect(findLegalPlaceholders('<p>[] und [ ]</p>')).toEqual([]);
  });

  it('reads plain text without markup too', () => {
    expect(findLegalPlaceholders('Stand dieser Erklärung: [DATUM]')).toEqual(['[DATUM]']);
  });
});
