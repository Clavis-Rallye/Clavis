/**
 * Web addresses inside a plain text, found at render time.
 *
 * Texts stored before the rich-text editor existed are plain strings. A URL
 * typed into one would stay dead text, while the same URL written in the
 * editor becomes a link. The stored text is never changed: the renderer splits
 * it into text and link parts, and React escapes every part.
 *
 * Only two shapes count as an address, so ordinary prose never turns into a
 * link by accident:
 * - `http://…` and `https://…`
 * - `www.…`, which gets `https://` in front
 *
 * Punctuation at the end of an address belongs to the sentence: "siehe
 * https://augsburg.de." links to `https://augsburg.de`. A closing bracket stays
 * part of the address only when the address also opens one, as in Wikipedia
 * titles.
 */

type LinkifyPart = { kind: 'text'; text: string } | { kind: 'link'; text: string; href: string };

const URL_RE = /\b(?:https?:\/\/|www\.)[^\s<>"]+/gi;
const TRAILING_RE = /[.,;:!?'"»«“”‘’]+$/;

function trimAddress(candidate: string): string {
  let address = candidate;
  for (;;) {
    const before = address;
    address = address.replace(TRAILING_RE, '');
    if (address.endsWith(')')) {
      const opens = (address.match(/\(/g) ?? []).length;
      const closes = (address.match(/\)/g) ?? []).length;
      if (closes > opens) address = address.slice(0, -1);
    }
    if (address === before) return address;
  }
}

/** The address a found text points at, or null when it holds no host. */
function hrefOf(address: string): string | null {
  const href = /^www\./i.test(address) ? `https://${address}` : address;
  try {
    const url = new URL(href);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    // "https://" alone, or "www." with nothing after it, is no address.
    if (!url.hostname || !url.hostname.includes('.')) return null;
    return href;
  } catch {
    return null;
  }
}

/** Split a plain text into text parts and link parts, in order. */
export function linkifyParts(text: string): LinkifyPart[] {
  const parts: LinkifyPart[] = [];
  let last = 0;
  for (const match of text.matchAll(URL_RE)) {
    const start = match.index ?? 0;
    const address = trimAddress(match[0]);
    const href = hrefOf(address);
    if (!href) continue;
    if (start > last) parts.push({ kind: 'text', text: text.slice(last, start) });
    parts.push({ kind: 'link', text: address, href });
    last = start + address.length;
  }
  if (last < text.length) parts.push({ kind: 'text', text: text.slice(last) });
  return parts;
}
