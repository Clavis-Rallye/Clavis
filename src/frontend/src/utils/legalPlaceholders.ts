import { htmlToPlainText } from './richtext';

/**
 * A template placeholder is a bracket that opens with a capital letter, such
 * as `[NAME DER ORGANISATION]`. The second branch also takes a bracket that
 * opens with a number and a word in capitals, such as `[90 TAGE]`, because
 * the privacy template writes its default periods that way. A footnote mark
 * like `[1]` or a year like `[2026]` is no placeholder.
 */
const PLACEHOLDER_RE = /\[(?:[A-ZÄÖÜ]|\d+ [A-ZÄÖÜ]{2})[^\]]*\]/g;

/**
 * The distinct open placeholders of a legal text, in the order they appear.
 * The text may be an editor document (HTML) or plain text. The search runs on
 * the visible text, so markup inside a bracket does not hide a placeholder.
 */
export function findLegalPlaceholders(text: string | null | undefined): string[] {
  if (!text) return [];
  const matches = htmlToPlainText(text).match(PLACEHOLDER_RE) ?? [];
  return [...new Set(matches)];
}
