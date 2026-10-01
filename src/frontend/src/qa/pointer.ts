/**
 * "Point at" for the feedback bar: the selector and the visible text of the
 * element the tester picks. The selector has the same shape as the selectors
 * of the CDP observer in the external test kit, so a note's element and the
 * observer journal's clicks read alike: `tag#id`, `tag[data-testid=…]`, or
 * `tag.class1.class2`, at most four levels, stopping at the first id. Unlike
 * the observer, it skips emotion's generated `css-…` classes.
 */

// Fields whose value must never leave the page, same rule as the observer.
const REDACT = /pass|token|secret|totp|otp|code/i;

const INTERACTIVE =
  'a,button,[role=button],[role=link],[role=tab],[role=menuitem],input,select,textarea,label,summary';

const MAX_DEPTH = 4;
const MAX_TEXT = 300;

function bit(node: Element): string {
  let part = node.tagName.toLowerCase();
  if (node.id) return `${part}#${node.id}`;
  const testId = node.getAttribute('data-testid') || node.getAttribute('data-test');
  if (testId) return `${part}[data-testid=${testId}]`;
  // Emotion's generated classes (`css-1821gv5`) change with every style edit and
  // say nothing to a reader. The MUI and app classes stay.
  const classes = [...node.classList].filter((name) => !/^css-[a-z0-9]+$/.test(name));
  if (classes.length) part += '.' + classes.slice(0, 2).join('.');
  return part;
}

export function selectorOf(element: Element | null): string {
  if (!element) return '?';
  const parts: string[] = [];
  let node: Element | null = element;
  while (node && parts.length < MAX_DEPTH) {
    parts.unshift(bit(node));
    if (node.id) break;
    node = node.parentElement;
  }
  return parts.join('>');
}

/** The interactive element a click lands on, as the observer resolves it. */
export function resolveTarget(target: EventTarget | null): Element | null {
  if (!(target instanceof Element)) return null;
  return target.closest(INTERACTIVE) ?? target;
}

function squash(value: string | null | undefined): string {
  return (value ?? '').replace(/\s+/g, ' ').trim();
}

function isSecretField(element: Element): boolean {
  if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement)) return false;
  if (element instanceof HTMLInputElement && element.type === 'password') return true;
  return REDACT.test(element.name || '') || REDACT.test(element.id || '');
}

/** What the tester sees on the element, at most 300 characters. */
export function visibleText(element: Element | null): string {
  if (!element) return '';
  const candidates: string[] = [];
  if (element instanceof HTMLElement) candidates.push(element.innerText ?? element.textContent ?? '');
  else candidates.push(element.textContent ?? '');
  candidates.push(element.getAttribute('aria-label') ?? '');
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
    if (!isSecretField(element)) candidates.push(element.value);
    candidates.push(element.placeholder);
  }
  candidates.push(element.getAttribute('alt') ?? '', element.getAttribute('title') ?? '');
  const text = candidates.map(squash).find((value) => value.length > 0) ?? '';
  return text.slice(0, MAX_TEXT);
}
