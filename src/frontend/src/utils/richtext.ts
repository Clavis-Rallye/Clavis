/**
 * Helpers around the HTML documents the rich-text editor produces.
 *
 * Long texts (descriptions, instructions, legal texts) are stored as HTML since
 * the editor exists. Texts written before that are plain strings with line
 * breaks. Every helper here treats both the same way, so a display site never
 * has to know which one it got.
 */

const HTML_TAG_RE = /<[a-z][\s\S]*>/i;

/** The only `src` an inline image may carry: the organisation media route. */
const MEDIA_ROUTE_RE = /^\/api\/orgs\/[^/]+\/media\/[^?#]+$/;

/** True when the text carries at least one tag, the same test `SafeHtml` uses. */
export function isHtml(text: string | null | undefined): boolean {
  return !!text && HTML_TAG_RE.test(text);
}

export function isOwnMediaSrc(src: string | null | undefined): boolean {
  return !!src && MEDIA_ROUTE_RE.test(src) && !src.includes('..');
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * Convert legacy plain text to the editor's document model: blank lines split
 * paragraphs, single line breaks become `<br>`. Empty input stays empty so the
 * editor shows its placeholder.
 */
export function plainTextToHtml(text: string): string {
  const trimmed = text.replace(/\r\n?/g, '\n').trim();
  if (!trimmed) return '';
  return trimmed
    .split(/\n{2,}/)
    .map((paragraph) => `<p>${escapeHtml(paragraph).replace(/\n/g, '<br>')}</p>`)
    .join('');
}

const H1_OPEN_RE = /<h1(?=[\s/>])/gi;
const H1_CLOSE_RE = /<\/h1\s*>/gi;
const PRE_BLOCK_RE = /<pre(?=[\s/>])[^>]*>([\s\S]*?)(?:<\/pre\s*>|$)/gi;
const BR_TAG_RE = /<br(?=[\s/>])[^>]*>/gi;
const ANY_TAG_RE = /<\/?[a-z][^>]*>/gi;
const CODE_TAG_RE = /<\/?code(?=[\s/>])[^>]*>/gi;
const BLANK_LINE_RE = /\n(?:[ \t]*\n)+/;

/** A `pre` block as paragraphs: only its text, a blank line starts a paragraph, a line break is `<br>`. */
function preToParagraphs(_block: string, content: string): string {
  const text = content
    .replace(BR_TAG_RE, '\n')
    .replace(ANY_TAG_RE, '')
    .replace(/\r\n?/g, '\n');
  return text
    .split(BLANK_LINE_RE)
    .map((chunk) => chunk.replace(/^\n+|\n+$/g, ''))
    .filter((chunk) => chunk.trim() !== '')
    .map((chunk) => `<p>${chunk.replace(/\n/g, '<br>')}</p>`)
    .join('');
}

/**
 * Legal texts written before the server filtered rich text can hold `h1`, `pre`
 * and `code`. Map them to what the editor has: `h1` to `h2`, a `pre` block to
 * paragraphs and `code` to plain text. The server does the same in
 * `sanitize_html`, so no word is lost and none ends up outside a block.
 */
function mapLegacyElements(html: string): string {
  return html
    .replace(H1_OPEN_RE, '<h2')
    .replace(H1_CLOSE_RE, '</h2>')
    .replace(PRE_BLOCK_RE, preToParagraphs)
    .replace(CODE_TAG_RE, '');
}

/** Either the document as is, or the legacy plain text converted, ready for the editor. */
export function toEditorHtml(value: string | null | undefined): string {
  if (!value) return '';
  return isHtml(value) ? mapLegacyElements(value) : plainTextToHtml(value);
}

/**
 * Text content of a document, block boundaries as spaces. Uses the DOM parser
 * where one exists and a tag strip otherwise, so it is safe in every environment.
 */
export function htmlToPlainText(html: string | null | undefined): string {
  if (!html) return '';
  if (!isHtml(html)) return html;
  if (typeof DOMParser !== 'undefined') {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    doc.querySelectorAll('br').forEach((br) => br.replaceWith(' '));
    doc.querySelectorAll('p, li, h1, h2, h3, h4, h5, h6, div').forEach((el) => el.append(' '));
    return doc.body.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  }
  return html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/\s+/g, ' ')
    .trim();
}

/** A short, markup-free preview for lists and cards. */
export function excerpt(value: string | null | undefined, maxLength = 160): string {
  const text = htmlToPlainText(value);
  if (text.length <= maxLength) return text;
  const cut = text.slice(0, maxLength);
  const lastSpace = cut.lastIndexOf(' ');
  return `${(lastSpace > maxLength * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd()} …`;
}

/** True when a document carries neither visible text nor an image. */
export function isRichTextEmpty(value: string | null | undefined): boolean {
  if (!value) return true;
  if (/<img\b/i.test(value)) return false;
  return htmlToPlainText(value).trim() === '';
}

/** An optional rich text as the API takes it: null for an empty editor, the document otherwise. */
export function optionalRichText(value: string | null | undefined): string | null {
  return isRichTextEmpty(value) ? null : (value as string);
}
