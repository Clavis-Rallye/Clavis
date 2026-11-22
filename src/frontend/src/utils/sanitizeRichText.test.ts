import { describe, expect, it } from 'vitest';
import { sanitizeRichText } from './sanitizeRichText';
import { EDITOR_091, RICH_TEXT_091_CORPUS, SANITIZED_091 } from './richText091.fixtures';

describe('sanitizeRichText on the 0.9.1 corpus', () => {
  it('records the same keys in all three fixture objects', () => {
    const keys = Object.keys(RICH_TEXT_091_CORPUS).sort();
    expect(Object.keys(SANITIZED_091).sort()).toEqual(keys);
    expect(Object.keys(EDITOR_091).sort()).toEqual(keys);
  });

  it.each(Object.keys(RICH_TEXT_091_CORPUS))('cleans %s as release 0.9.1 did', (key) => {
    expect(sanitizeRichText(RICH_TEXT_091_CORPUS[key])).toBe(SANITIZED_091[key]);
  });
});
