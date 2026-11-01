import { describe, it, expect } from 'vitest';
import { slugFromName } from './slugFromName';

// The same pattern the server and the create dialog use for the slug.
const SLUG_RE = /^[a-z0-9-]+$/;

const CASES: Array<[label: string, name: string, slug: string]> = [
  ['the example of the spec', 'Stadt München', 'stadt-muenchen'],
  ['ä', 'Bärenstark', 'baerenstark'],
  ['ö', 'Köln', 'koeln'],
  ['ü', 'Würzburg', 'wuerzburg'],
  ['capital umlauts', 'ÄÖÜ Verein', 'aeoeue-verein'],
  ['ß', 'Straße', 'strasse'],
  ['capital ẞ', 'GROẞ', 'gross'],
  ['acute and grave', 'Café Crème à la Maison', 'cafe-creme-a-la-maison'],
  ['circumflex, tilde, cedilla', 'Île Ñandú Façade', 'ile-nandu-facade'],
  ['caron and ring', 'Škoda Čeština Åland', 'skoda-cestina-aland'],
  ['dotted capital İ', 'İstanbul', 'istanbul'],
  ['letters without decomposition', 'Łódź Ørsted Æble Đorđe', 'lodz-orsted-aeble-dorde'],
  ['a decomposed ä (a + combining diaeresis)', 'Ba\u0308r', 'baer'],
  ['a decomposed ö (o + combining diaeresis)', 'Ko\u0308ln', 'koeln'],
  ['a decomposed ü (u + combining diaeresis)', 'Stadt Mu\u0308nchen', 'stadt-muenchen'],
  ['a decomposed capital Ä', 'A\u0308rzte', 'aerzte'],
  ['a decomposed capital Ö', 'O\u0308l', 'oel'],
  ['a decomposed capital Ü', 'U\u0308bung', 'uebung'],
  ['a decomposed é (e + combining acute)', 'Cafe\u0301', 'cafe'],
  ['single spaces', 'Stadt Augsburg', 'stadt-augsburg'],
  ['leading and trailing spaces', '   Stadt Augsburg   ', 'stadt-augsburg'],
  ['several spaces in a row', 'Stadt     Augsburg', 'stadt-augsburg'],
  ['several different separators in a row', 'Stadt - / _ . Augsburg', 'stadt-augsburg'],
  ['an existing hyphen', 'Baden-Württemberg', 'baden-wuerttemberg'],
  ['a double hyphen', 'a--b', 'a-b'],
  ['tabs and line breaks', 'Stadt\t\nAugsburg', 'stadt-augsburg'],
  ['signs at the start and the end', '--(Stadt Augsburg)!--', 'stadt-augsburg'],
  ['apostrophe and ampersand', "Land & Leute's Verein", 'land-leute-s-verein'],
  ['upper case', 'STADT AUGSBURG', 'stadt-augsburg'],
  ['mixed case', 'StAdT aUgSbUrG', 'stadt-augsburg'],
  ['digits', 'Team 42', 'team-42'],
  ['a name of digits only', '2026', '2026'],
  ['digits next to letters', 'Rallye2026', 'rallye2026'],
  ['an already valid slug', 'stadt-augsburg', 'stadt-augsburg'],
  ['signs only', '!!! ???', ''],
  ['spaces only', '     ', ''],
  ['the empty name', '', ''],
  ['a script without Latin letters', 'Москва', ''],
  ['Arabic letters between Latin words', 'Haus مرحبا Augsburg', 'haus-augsburg'],
  ['an emoji', 'Stadt 🎉 Augsburg', 'stadt-augsburg'],
];

describe('slugFromName', () => {
  it.each(CASES)('%s', (_label, name, slug) => {
    expect(slugFromName(name)).toBe(slug);
  });

  it('gives the same result for the same name (pure)', () => {
    expect(slugFromName('Stadt München')).toBe(slugFromName('Stadt München'));
  });

  it('cuts a very long name at the 255 characters the server allows', () => {
    const slug = slugFromName('ä'.repeat(300));
    expect(slug.length).toBeLessThanOrEqual(255);
    expect(slug).toMatch(SLUG_RE);
  });

  it('leaves no hyphen at the end after cutting', () => {
    const slug = slugFromName(`${'a'.repeat(254)} b`);
    expect(slug).toBe('a'.repeat(254));
  });

  it('gives a slug the server accepts, or an empty string, for every name', () => {
    const names = [
      ...CASES.map(([, name]) => name),
      'ÄÖÜäöüß',
      '---',
      '_',
      'a\u0000b',
      '́',
      'Ａｂｃ Fullwidth',
      '①②③',
      'x'.repeat(1000),
    ];
    // Every single code point up to the end of the Latin Extended blocks, then
    // a run of the other BMP blocks, so no character slips through unseen.
    for (let cp = 0; cp < 0x3000; cp += 1) {
      if (cp >= 0xd800 && cp <= 0xdfff) continue;
      const c = String.fromCodePoint(cp);
      names.push(c, `a${c}b`, `${c}${c}`);
    }
    for (const name of names) {
      const slug = slugFromName(name);
      expect(slug === '' || SLUG_RE.test(slug), JSON.stringify(name)).toBe(true);
      expect(slug.startsWith('-') || slug.endsWith('-'), JSON.stringify(name)).toBe(false);
      expect(slug).not.toContain('--');
      expect(slug.length).toBeLessThanOrEqual(255);
    }
  });
});
