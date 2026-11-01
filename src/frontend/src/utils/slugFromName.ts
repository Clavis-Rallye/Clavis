// The slug the server accepts is `^[a-z0-9-]+$`, at most 255 characters
// (OrganizationCreate in src/backend/app/routers/schemas.py). The create
// dialog of an organization suggests one from the name.

const MAX_SLUG_LENGTH = 255;

// Letters that have no decomposition into a base letter plus a mark, so
// Unicode normalisation alone cannot reduce them to a-z.
const SPECIAL_LETTERS: Record<string, string> = {
  ä: 'ae',
  ö: 'oe',
  ü: 'ue',
  ß: 'ss',
  æ: 'ae',
  œ: 'oe',
  ø: 'o',
  ł: 'l',
  đ: 'd',
  ð: 'd',
  ı: 'i',
};

const SPECIAL_RE = new RegExp(`[${Object.keys(SPECIAL_LETTERS).join('')}]`, 'g');

/**
 * Turns an organization name into a slug suggestion: lower case letters,
 * digits and single hyphens, never a hyphen at the start or the end. A name
 * without a usable character gives an empty string.
 */
export function slugFromName(name: string): string {
  const slug = name
    // Compose first: macOS and some phone keyboards type "ü" as "u" plus the
    // combining diaeresis U+0308, which must still become "ue".
    .normalize('NFC')
    .toLowerCase()
    .replace(SPECIAL_RE, (c) => SPECIAL_LETTERS[c])
    // Split "é" into "e" + combining mark and drop the mark.
    .normalize('NFD')
    .replace(/\p{M}+/gu, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  // Cut at the server limit without leaving a hyphen at the end.
  return slug.slice(0, MAX_SLUG_LENGTH).replace(/-+$/, '');
}
