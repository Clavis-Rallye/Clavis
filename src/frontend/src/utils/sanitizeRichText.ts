import DOMPurify from 'dompurify';
import { resolveMediaUrl } from '../api/media';
import { isOwnMediaSrc } from './richtext';

/**
 * Own DOMPurify instance so the hooks below stay local to this renderer.
 * `img` is allowed only with a `src` under the organization media route.
 * Everything else, a hotlink or a tracking pixel, is removed on render exactly
 * as the API removes it on write. The lists of what stays are the contract of
 * the editor (`richTextContract.json`), the same file the API filter follows.
 */
const purifier = DOMPurify();

purifier.addHook('uponSanitizeElement', (node, data) => {
  if (data.tagName !== 'img') return;
  const element = node as Element;
  const src = element.getAttribute('src');
  if (!isOwnMediaSrc(src)) {
    element.parentNode?.removeChild(element);
    return;
  }
  element.setAttribute('src', resolveMediaUrl(src as string));
  element.setAttribute('loading', 'lazy');
  if (!element.hasAttribute('alt')) element.setAttribute('alt', '');
});

/**
 * Texts written before the server filtered rich text can hold these three.
 * The renderer keeps them so such a text still looks as it did; the editor never makes them.
 */
const RENDER_ONLY_TAGS = Object.freeze(['h1', 'code', 'pre']);

/**
 * Elements, attributes per element, values and URL schemes the renderer keeps.
 * `src/frontend/src/utils/richTextContract.json` is the source: the test compares both.
 * The renderer itself adds the three render-only tags and `loading` on `img`.
 */
export const RICH_TEXT_FILTER = Object.freeze({
  tags: Object.freeze([
    'p',
    'br',
    'strong',
    'em',
    'u',
    's',
    'h2',
    'h3',
    'ul',
    'ol',
    'li',
    'blockquote',
    'hr',
    'a',
    'img',
    'figure',
    'figcaption',
    'table',
    'tbody',
    'tr',
    'th',
    'td',
    ...RENDER_ONLY_TAGS,
  ]),
  renderOnlyTags: RENDER_ONLY_TAGS,
  attributes: Object.freeze({
    p: Object.freeze([]),
    br: Object.freeze([]),
    strong: Object.freeze([]),
    em: Object.freeze([]),
    u: Object.freeze([]),
    s: Object.freeze([]),
    h1: Object.freeze([]),
    h2: Object.freeze([]),
    h3: Object.freeze([]),
    ul: Object.freeze([]),
    ol: Object.freeze([]),
    li: Object.freeze([]),
    blockquote: Object.freeze([]),
    hr: Object.freeze([]),
    code: Object.freeze([]),
    pre: Object.freeze([]),
    a: Object.freeze(['href', 'rel', 'target']),
    img: Object.freeze(['alt', 'data-align', 'data-width', 'loading', 'src']),
    figure: Object.freeze(['data-align', 'data-width']),
    figcaption: Object.freeze([]),
    table: Object.freeze([]),
    tbody: Object.freeze([]),
    tr: Object.freeze([]),
    th: Object.freeze([]),
    td: Object.freeze([]),
  } as Record<string, readonly string[]>),
  attributeValues: Object.freeze({
    'data-align': Object.freeze(['center', 'left', 'right']),
    'data-width': Object.freeze(['100', '25', '50', '75']),
  } as Record<string, readonly string[]>),
  urlSchemes: Object.freeze(['http', 'https', 'mailto']),
});

/** Every attribute name any element may carry, for DOMPurify's one global list. */
const ALLOWED_ATTR = Array.from(new Set(Object.values(RICH_TEXT_FILTER.attributes).flat()));

// DOMPurify's `ALLOWED_ATTR` is one list for every element. This hook narrows it per element
// and checks the two data values exactly. It only ever removes, it never forces an attribute to stay.
purifier.addHook('uponSanitizeAttribute', (node, data) => {
  const allowed = RICH_TEXT_FILTER.attributes[node.nodeName.toLowerCase()];
  if (!allowed?.includes(data.attrName)) {
    data.keepAttr = false;
    return;
  }
  const values = RICH_TEXT_FILTER.attributeValues[data.attrName];
  if (values && !values.includes(data.attrValue)) data.keepAttr = false;
});

/**
 * DOMPurify visits a parent before its children, so a figure whose image the hook removed can
 * only be dropped after the walk. A figure stays when an image has it as its closest figure,
 * the same rule the API filter applies.
 */
function removeFiguresWithoutImage(root: ParentNode): void {
  const keep = new Set<Element>();
  for (const img of Array.from(root.querySelectorAll('img'))) {
    const figure = img.closest('figure');
    if (figure) keep.add(figure);
  }
  for (const figure of Array.from(root.querySelectorAll('figure'))) {
    if (!keep.has(figure)) figure.remove();
  }
}

export function sanitizeRichText(html: string): string {
  // Text without a `<` holds no element and no attribute. It stays as it is, as in release 0.9.1.
  if (!html.includes('<')) return html;
  const cleaned: DocumentFragment | string = purifier.sanitize(html, {
    ALLOWED_TAGS: [...RICH_TEXT_FILTER.tags],
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
    RETURN_DOM_FRAGMENT: true,
  });
  if (typeof cleaned === 'string') return cleaned;
  removeFiguresWithoutImage(cleaned);
  const holder = (cleaned.ownerDocument ?? document).createElement('div');
  holder.appendChild(cleaned);
  return holder.innerHTML;
}

/**
 * Puts every table of already clean output into its own horizontal scroll area, so a wide
 * table never makes the page scroll sideways. The wrapper is a `div` with exactly three
 * attributes: `role="region"`, `tabindex="0"` (keyboard reachable) and the label. It is made
 * at render time and is never part of the stored text. The label is set as an attribute
 * value through the DOM, so it is escaped. Only call this on the output of
 * `sanitizeRichText`: the function adds the wrapper and nothing else.
 */
export function wrapTablesForScroll(cleanHtml: string, label: string): string {
  if (!/<table[\s>]/i.test(cleanHtml)) return cleanHtml;
  const holder = document.createElement('template');
  holder.innerHTML = cleanHtml;
  const root = holder.content;
  for (const table of Array.from(root.querySelectorAll('table'))) {
    // A table inside a table scrolls with the outer one.
    if (table.parentElement?.closest('table')) continue;
    const wrapper = document.createElement('div');
    wrapper.setAttribute('role', 'region');
    wrapper.setAttribute('tabindex', '0');
    wrapper.setAttribute('aria-label', label);
    table.replaceWith(wrapper);
    wrapper.appendChild(table);
  }
  const out = document.createElement('div');
  out.appendChild(root);
  return out.innerHTML;
}
