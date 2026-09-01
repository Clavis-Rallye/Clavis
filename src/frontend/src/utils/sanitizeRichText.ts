import DOMPurify from 'dompurify';
import { resolveMediaUrl } from '../api/media';
import { isOwnMediaSrc } from './richtext';

/**
 * Own DOMPurify instance so the image hook below stays local to this renderer.
 * `img` is allowed only with a `src` under the organization media route.
 * Everything else, a hotlink or a tracking pixel, is removed on render exactly
 * as the API removes it on write.
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

const ALLOWED_TAGS = [
  'p',
  'br',
  'strong',
  'em',
  'u',
  's',
  'h1',
  'h2',
  'h3',
  'ul',
  'ol',
  'li',
  'a',
  'img',
  'blockquote',
  'code',
  'pre',
];

const ALLOWED_ATTR = ['href', 'rel', 'target', 'src', 'alt', 'loading'];

export function sanitizeRichText(html: string): string {
  return purifier.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
  });
}
