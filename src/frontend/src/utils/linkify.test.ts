import { describe, expect, it } from 'vitest';
import { linkifyParts } from './linkify';

const links = (text: string) =>
  linkifyParts(text)
    .filter((p) => p.kind === 'link')
    .map((p) => (p.kind === 'link' ? [p.text, p.href] : null));

describe('linkifyParts', () => {
  it('finds an https address inside a sentence and keeps the text around it', () => {
    expect(linkifyParts('Siehe https://augsburg.de/rathaus für mehr.')).toEqual([
      { kind: 'text', text: 'Siehe ' },
      { kind: 'link', text: 'https://augsburg.de/rathaus', href: 'https://augsburg.de/rathaus' },
      { kind: 'text', text: ' für mehr.' },
    ]);
  });

  it('gives a www address the https scheme', () => {
    expect(links('Mehr auf www.augsburg.de')).toEqual([['www.augsburg.de', 'https://www.augsburg.de']]);
  });

  it('leaves the full stop and the comma of the sentence outside the link', () => {
    expect(links('Erst https://a.de, dann https://b.de/x.')).toEqual([
      ['https://a.de', 'https://a.de'],
      ['https://b.de/x', 'https://b.de/x'],
    ]);
  });

  it('keeps a bracket the address opens itself, and drops one the sentence opens', () => {
    expect(links('https://de.wikipedia.org/wiki/Rathaus_(Augsburg)')).toEqual([
      ['https://de.wikipedia.org/wiki/Rathaus_(Augsburg)', 'https://de.wikipedia.org/wiki/Rathaus_(Augsburg)'],
    ]);
    expect(links('(siehe https://augsburg.de)')).toEqual([['https://augsburg.de', 'https://augsburg.de']]);
  });

  it('links no other scheme and no bare word', () => {
    expect(links('javascript:alert(1) mailto:a@b.de augsburg.de https:// www.')).toEqual([]);
  });

  it('returns the text as one part when it holds no address', () => {
    expect(linkifyParts('Zeile 1\nZeile 2')).toEqual([{ kind: 'text', text: 'Zeile 1\nZeile 2' }]);
  });
});
