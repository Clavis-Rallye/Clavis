/** What may stand in a link of a station or task text. The server keeps the same three (`URL_SCHEMES`). */
const WEB = /^https?:\/\/[^\s/]+\.[^\s/.]+/i;
const MAIL = /^[^\s@/:]+@[^\s@/:]+\.[^\s@/:.]+$/;

/**
 * The address a typed text turns into, or null when it is no usable address.
 *
 * - `https://…` and `http://…` with a host stay as they are.
 * - `mailto:name@host.tld` stays, and a bare mail address gets `mailto:` in front.
 * - `augsburg.de/rathaus` and `augsburg.de:8080/x` get `https://` in front.
 * - A text with spaces, a word without a dot, a host that ends in a dot and
 *   every other scheme are refused, `javascript:` and `tel:` among them. The
 *   server would strip such a link without a word.
 */
export function normalizeLinkAddress(input: string): string | null {
  const text = input.trim();
  if (!text || /\s/.test(text)) return null;
  if (/^mailto:/i.test(text)) return MAIL.test(text.slice(7)) ? text : null;
  if (MAIL.test(text)) return `mailto:${text}`;
  if (/^https?:\/\//i.test(text)) return WEB.test(text) ? text : null;
  // A scheme is letters before a colon that no port number follows.
  if (/^[a-z][a-z0-9+.-]*:(?!\d+(\/|$))/i.test(text)) return null;
  const withScheme = `https://${text}`;
  return WEB.test(withScheme) ? withScheme : null;
}
