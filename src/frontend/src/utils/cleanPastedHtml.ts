import { isOwnMediaSrc } from './richtext';
import { flattenTablesOutsideContract } from './richTextTables';

/**
 * Cleans HTML from the clipboard (Word, Google Docs, a web page) before the
 * editor reads it. The output holds only what the contract of the editor lists
 * (`richTextContract.json`), so a paste shows in the editor what will be
 * stored: no style, class, font, colour or highlight, no `o:p`, no conditional
 * comment, no list marker characters, and no image from outside the media route.
 *
 * Pure: the input is parsed with `DOMParser` into a document of its own, the
 * result is built from new elements, nothing is executed or loaded.
 */
export interface CleanedPaste {
  html: string;
  /** Images that were not taken over because they are not on the media route. */
  imagesLeftOut: number;
  /** Set when the paste was over a budget. Then `html` is empty and nothing was cleaned. */
  overBudget?: PasteBudget;
}

/**
 * The budgets of one paste. The cleaning runs in one go on the thread of the page, so a paste
 * that is too large would freeze the tab. A paste over a budget is refused before any of that
 * work, and the editor says so.
 *
 * - `characters`: the markup on the clipboard. Word writes about twenty times the visible text, so
 *   three million characters hold far more than the longest text the server stores.
 * - `nodes` and `depth`: the parsed document, and the document that the cleaning builds. The
 *   cleaning walks both with one function call for each level of depth. The built document can
 *   be larger and deeper than the parsed one: a list of Word is flat paragraphs with a level
 *   each, and every line of a preformatted block becomes nodes. So the cleaning counts the nodes
 *   it makes and the levels of a list it opens, and stops at the budget.
 */
export const PASTE_BUDGET = { characters: 3_000_000, nodes: 100_000, depth: 100 } as const;
export type PasteBudget = keyof typeof PASTE_BUDGET;

/** Thrown inside the cleaning when the document it builds goes over a budget. */
class PasteOverBudgetError extends Error {
  readonly budget: PasteBudget;

  constructor(budget: PasteBudget) {
    super(`paste over budget: ${budget}`);
    this.budget = budget;
  }
}

const refused = (budget: PasteBudget): CleanedPaste => ({ html: '', imagesLeftOut: 0, overBudget: budget });

/**
 * The budget that a document is over, or `null`. One walk without recursion, which ends
 * at the first budget that is exceeded.
 */
function overParsedBudget(root: Node): PasteBudget | null {
  let nodes = 0;
  let depth = 0;
  let node: Node | null = root;
  while (node) {
    nodes += 1;
    if (nodes > PASTE_BUDGET.nodes) return 'nodes';
    if (node.firstChild) {
      depth += 1;
      if (depth > PASTE_BUDGET.depth) return 'depth';
      node = node.firstChild;
      continue;
    }
    while (node && node !== root && !node.nextSibling) {
      node = node.parentNode;
      depth -= 1;
    }
    node = node && node !== root ? node.nextSibling : null;
  }
  return null;
}

type Mark = 'strong' | 'em' | 'u' | 's';

/** Outermost first: the order in which the marks of a text are written. */
const MARK_ORDER: readonly Mark[] = ['strong', 'em', 'u', 's'];

interface Context {
  marks: ReadonlySet<Mark>;
  /** Inside a `figure`: a `figcaption` is kept as such. */
  inFigure: boolean;
}

/** Elements that are dropped with everything inside. */
const DROP_TAGS = new Set([
  'script',
  'style',
  'iframe',
  'frame',
  'frameset',
  'object',
  'embed',
  'applet',
  'noscript',
  'template',
  'head',
  'title',
  'meta',
  'link',
  'base',
  'svg',
  'math',
  'canvas',
  'video',
  'audio',
  'map',
  'input',
  'button',
  'select',
  'textarea',
  'datalist',
]);

/** Elements whose content is a run of blocks of its own; loose text in it becomes paragraphs. */
const BLOCK_CONTAINERS = new Set([
  'html',
  'body',
  'div',
  'p',
  'section',
  'article',
  'aside',
  'header',
  'footer',
  'main',
  'nav',
  'address',
  'center',
  'form',
  'fieldset',
  'legend',
  'details',
  'summary',
  'dl',
  'dt',
  'dd',
  'hgroup',
  'li',
  'caption',
  'figcaption',
  'thead',
  'tbody',
  'tfoot',
  'tr',
  'td',
  'th',
]);

/** What the cleaner writes as a block. Everything else it writes is inline. */
const BLOCK_OUTPUT = new Set(['p', 'h2', 'h3', 'ul', 'ol', 'blockquote', 'hr', 'table', 'figure', 'figcaption', 'img']);

/** The kept inline elements. A line break or a text is inline too. */
const INLINE_OUTPUT = new Set<string>([...MARK_ORDER, 'a']);

const NOT_BOLD = /font-weight\s*:\s*(?:normal|lighter|[1-5]00)\b/i;
const BOLD = /font-weight\s*:\s*(?:bold|bolder|[6-9]00)\b/i;
const NOT_ITALIC = /font-style\s*:\s*normal/i;
const ITALIC = /font-style\s*:\s*(?:italic|oblique)/i;
const UNDERLINE_WORD = /\bunderline\b/;
const STRIKE_WORD = /\bline-through\b/;
const HIDDEN = /display\s*:\s*none|mso-hide\s*:\s*all/i;
const WORD_LIST = /mso-list\s*:\s*l\d+\s+level\d+/i;
const WORD_LIST_IGNORE = /mso-list\s*:\s*ignore/i;
const ORDERED_MARKER = /^\(?[0-9a-z]{1,4}[.)]$/i;

const WIDTHS = ['25', '50', '75', '100'];
const ALIGNS = ['left', 'center', 'right'];

/**
 * The values of the `text-decoration` and `text-decoration-line` declarations of a style, in order.
 * The style is split at `;` and each declaration at its first `:`, so no pattern ever scans a
 * long style from many start points.
 */
function decorationsOf(style: string): string[] {
  const values: string[] = [];
  for (const declaration of style.split(';')) {
    const colon = declaration.indexOf(':');
    if (colon === -1) continue;
    const property = declaration.slice(0, colon).trim().toLowerCase();
    if (property === 'text-decoration' || property === 'text-decoration-line') values.push(declaration.slice(colon + 1).toLowerCase());
  }
  return values;
}

const nameOf = (node: Node): string => node.nodeName.toLowerCase();
const isElement = (node: Node | null | undefined): node is Element => node?.nodeType === 1;
const isBlock = (node: Node): boolean => isElement(node) && BLOCK_OUTPUT.has(nameOf(node));
const hasText = (node: Node): boolean => /\S/.test(node.textContent ?? '');
const styleOf = (element: Element): string => element.getAttribute('style') ?? '';

/** Collapses white space (a non-breaking space too) to single spaces and drops zero-width characters. */
const collapse = (text: string): string =>
  text.replace(/[\u200b\ufeff]/g, '').replace(/[ \t\r\n\f\u00a0]+/g, ' ');

const isWordListParagraph = (node: Node): node is HTMLElement =>
  isElement(node) && nameOf(node) === 'p' && WORD_LIST.test(styleOf(node));

const isIgnorable = (node: Node): boolean =>
  node.nodeType === 8 || (node.nodeType === 3 && !/\S/.test(node.textContent ?? ''));

const allowedValue = (list: string[], value: string | null): string | null =>
  value !== null && list.includes(value) ? value : null;

export function cleanPastedHtml(html: string): CleanedPaste {
  if (html.length > PASTE_BUDGET.characters) return refused('characters');
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const over = overParsedBudget(doc.documentElement);
  if (over) return refused(over);
  let imagesLeftOut = 0;
  // Every node the cleaning makes is counted, so the document it builds stays inside the budget
  // whatever the input expands to.
  let made = 0;
  const count = (): void => {
    made += 1;
    if (made > PASTE_BUDGET.nodes) throw new PasteOverBudgetError('nodes');
  };

  const el = (tag: string, attributes: Record<string, string> = {}, children: Node[] = []): HTMLElement => {
    count();
    const element = doc.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
    for (const child of children) element.appendChild(child);
    return element;
  };

  /** The marks an element adds to the text inside it. */
  const marksOf = (element: Element): Mark[] => {
    const tag = nameOf(element);
    const style = styleOf(element);
    const marks: Mark[] = [];
    if (tag === 'b' || tag === 'strong') {
      if (!NOT_BOLD.test(style)) marks.push('strong');
    } else if (tag === 'i' || tag === 'em') {
      if (!NOT_ITALIC.test(style)) marks.push('em');
    } else if (tag === 'u') {
      marks.push('u');
    } else if (tag === 's' || tag === 'strike' || tag === 'del') {
      marks.push('s');
    } else if (tag === 'span' || tag === 'font') {
      if (BOLD.test(style)) marks.push('strong');
      if (ITALIC.test(style)) marks.push('em');
      const decorations = decorationsOf(style);
      if (decorations.some((value) => UNDERLINE_WORD.test(value))) marks.push('u');
      if (decorations.some((value) => STRIKE_WORD.test(value))) marks.push('s');
    }
    return marks;
  };

  const textNode = (text: string, context: Context): Node => {
    count();
    let node: Node = doc.createTextNode(text);
    for (const mark of [...MARK_ORDER].reverse()) {
      if (context.marks.has(mark)) node = el(mark, {}, [node]);
    }
    return node;
  };

  /** Runs of inline nodes become paragraphs; a run without text is dropped. */
  const blockify = (nodes: Node[]): Node[] => {
    const result: Node[] = [];
    let run: Node[] = [];
    const flush = () => {
      if (run.some(hasText)) result.push(el('p', {}, run));
      run = [];
    };
    for (const node of nodes) {
      if (isBlock(node)) {
        flush();
        result.push(node);
      } else {
        run.push(node);
      }
    }
    flush();
    return result;
  };

  /** The text of blocks without the blocks around it, for a heading or a caption. */
  const inlineOnly = (nodes: Node[]): Node[] => {
    const result: Node[] = [];
    for (const node of nodes) {
      if (!isBlock(node)) {
        result.push(node);
      } else if (!['img', 'hr'].includes(nameOf(node))) {
        count();
        result.push(...inlineOnly(Array.from(node.childNodes)), doc.createTextNode(' '));
      }
    }
    return result;
  };

  const walkChildren = (parent: Node, context: Context): Node[] => {
    const result: Node[] = [];
    const kids = Array.from(parent.childNodes);
    for (let i = 0; i < kids.length; i += 1) {
      const kid = kids[i];
      if (!isWordListParagraph(kid)) {
        result.push(...walk(kid, context));
        continue;
      }
      // Word writes a list as paragraphs; the neighbouring ones form one list.
      const group: HTMLElement[] = [kid];
      for (let j = i + 1; j < kids.length; j += 1) {
        if (isWordListParagraph(kids[j])) {
          group.push(kids[j] as HTMLElement);
          i = j;
        } else if (!isIgnorable(kids[j])) {
          break;
        }
      }
      result.push(...buildWordLists(group, context));
    }
    return result;
  };

  /**
   * Takes the marker (bullet or number) out of a Word list paragraph and
   * returns its text. Word puts it between conditional comments or in a span
   * with `mso-list:Ignore`.
   */
  const extractMarker = (paragraph: HTMLElement): string => {
    const kids = Array.from(paragraph.childNodes);
    const start = kids.findIndex((n) => n.nodeType === 8 && /^\[if !supportLists\]/i.test((n as Comment).data));
    if (start >= 0) {
      const end = kids.findIndex((n, index) => index > start && n.nodeType === 8 && /^\[endif\]/i.test((n as Comment).data));
      if (end > start) {
        const marker = kids
          .slice(start + 1, end)
          .map((n) => n.textContent ?? '')
          .join('');
        kids.slice(start, end + 1).forEach((n) => n.parentNode?.removeChild(n));
        return marker;
      }
    }
    const ignored = Array.from(paragraph.querySelectorAll('span')).find((span) => WORD_LIST_IGNORE.test(styleOf(span)));
    if (!ignored) return '';
    const marker = ignored.textContent ?? '';
    ignored.remove();
    return marker;
  };

  interface Frame {
    level: number;
    ordered: boolean;
    list: HTMLElement;
    lastItem: HTMLElement | null;
  }

  const buildWordLists = (group: HTMLElement[], context: Context): Node[] => {
    const roots: Node[] = [];
    const stack: Frame[] = [];
    const open = (level: number, ordered: boolean, parent: HTMLElement | null) => {
      // Each open list adds a list and an item to the depth of the built document.
      if ((stack.length + 1) * 2 > PASTE_BUDGET.depth) throw new PasteOverBudgetError('depth');
      const list = el(ordered ? 'ol' : 'ul');
      if (parent) parent.appendChild(list);
      else roots.push(list);
      stack.push({ level, ordered, list, lastItem: null });
    };
    for (const paragraph of group) {
      const marker = extractMarker(paragraph).replace(/\s+/g, '');
      const level = Number(/level(\d+)/i.exec(styleOf(paragraph))?.[1] ?? 1);
      const ordered = ORDERED_MARKER.test(marker);
      while (stack.length > 0 && stack[stack.length - 1].level > level) stack.pop();
      let top = stack[stack.length - 1];
      if (!top || (stack.length === 1 && top.level === level && top.ordered !== ordered)) {
        stack.length = 0;
        open(level, ordered, null);
      } else if (top.level < level) {
        if (!top.lastItem) {
          top.lastItem = el('li');
          top.list.appendChild(top.lastItem);
        }
        open(level, ordered, top.lastItem);
      }
      top = stack[stack.length - 1];
      const item = el('li', {}, blockify(walkChildren(paragraph, context)));
      top.list.appendChild(item);
      top.lastItem = item;
    }
    return roots;
  };

  const buildList = (source: Element, context: Context): Node[] => {
    const list = el(nameOf(source) === 'ol' ? 'ol' : 'ul');
    let lastItem: HTMLElement | null = null;
    for (const kid of Array.from(source.childNodes)) {
      if (isElement(kid) && ['ul', 'ol'].includes(nameOf(kid))) {
        // A list directly in a list (Google Docs) belongs to the item before it.
        const nested = buildList(kid, context);
        if (nested.length === 0) continue;
        if (!lastItem) {
          lastItem = el('li');
          list.appendChild(lastItem);
        }
        nested.forEach((n) => lastItem?.appendChild(n));
        continue;
      }
      const nodes = isElement(kid) && nameOf(kid) === 'li' ? walkChildren(kid, context) : walk(kid, context);
      if (!nodes.some(hasText) && !nodes.some(isBlock)) continue;
      lastItem = el('li', {}, blockify(nodes));
      list.appendChild(lastItem);
    }
    return list.childElementCount > 0 ? [list] : [];
  };

  const buildTable = (source: Element, context: Context): Node[] => {
    const rows: HTMLElement[] = [];
    const before: Node[] = [];
    const collect = (parent: Element) => {
      for (const kid of Array.from(parent.children)) {
        const name = nameOf(kid);
        if (name === 'tr') {
          const cells = Array.from(kid.children)
            .filter((cell) => ['td', 'th'].includes(nameOf(cell)) && !HIDDEN.test(styleOf(cell)))
            .map((cell) => el(nameOf(cell), {}, blockify(walkChildren(cell, context))));
          if (cells.length > 0) rows.push(el('tr', {}, cells));
        } else if (['thead', 'tbody', 'tfoot'].includes(name)) {
          collect(kid);
        } else if (name === 'caption') {
          before.push(...blockify(walkChildren(kid, context)));
        }
      }
    };
    collect(source);
    return rows.length > 0 ? [...before, el('table', {}, [el('tbody', {}, rows)])] : before;
  };

  const buildFigure = (source: Element, context: Context): Node[] => {
    const kids = blockify(walkChildren(source, { ...context, inFigure: true }));
    const image = kids.find((n) => nameOf(n) === 'img') as HTMLElement | undefined;
    const caption = kids.find((n) => nameOf(n) === 'figcaption' && hasText(n));
    if (!image) return kids.map((n) => (nameOf(n) === 'figcaption' ? el('p', {}, Array.from(n.childNodes)) : n));
    const rest = kids.filter((n) => n !== image && nameOf(n) !== 'figcaption');
    const width = allowedValue(WIDTHS, source.getAttribute('data-width')) ?? image.getAttribute('data-width');
    const align = allowedValue(ALIGNS, source.getAttribute('data-align')) ?? image.getAttribute('data-align');
    if (!caption) return [image, ...rest];
    image.removeAttribute('data-width');
    image.removeAttribute('data-align');
    const attributes: Record<string, string> = {};
    if (width) attributes['data-width'] = width;
    if (align) attributes['data-align'] = align;
    return [el('figure', attributes, [image, caption]), ...rest];
  };

  const buildImage = (source: Element): Node[] => {
    const src = source.getAttribute('src');
    if (!isOwnMediaSrc(src)) {
      imagesLeftOut += 1;
      return [];
    }
    const attributes: Record<string, string> = { src: src as string, alt: source.getAttribute('alt') ?? '' };
    const width = allowedValue(WIDTHS, source.getAttribute('data-width'));
    const align = allowedValue(ALIGNS, source.getAttribute('data-align'));
    if (width) attributes['data-width'] = width;
    if (align) attributes['data-align'] = align;
    return [el('img', attributes)];
  };

  const buildLink = (source: Element, context: Context): Node[] => {
    const children = walkChildren(source, context);
    const href = (source.getAttribute('href') ?? '').trim();
    // A link around a block cannot stay a link: the editor's link is a mark on text.
    if (!/^(?:https?:|mailto:)/i.test(href) || children.some(isBlock) || !children.some(hasText)) return children;
    return [el('a', { href }, children)];
  };

  const walk = (node: Node, context: Context): Node[] => {
    if (node.nodeType === 3) {
      const text = collapse(node.textContent ?? '');
      return text === '' ? [] : [textNode(text, context)];
    }
    if (!isElement(node)) return []; // Comments, among them Word's conditional comments.
    const name = nameOf(node);
    if (DROP_TAGS.has(name) || name.startsWith('o:') || name.startsWith('v:')) return [];
    if (HIDDEN.test(styleOf(node)) || node.hasAttribute('hidden')) return [];

    const marks = marksOf(node);
    const inner: Context = marks.length > 0 ? { ...context, marks: new Set([...context.marks, ...marks]) } : context;

    switch (name) {
      case 'br':
        return [el('br')];
      case 'hr':
        return [el('hr')];
      case 'img':
        return buildImage(node);
      case 'a':
        return buildLink(node, context);
      case 'ul':
      case 'ol':
        return buildList(node, context);
      case 'table':
        return buildTable(node, context);
      case 'figure':
        return buildFigure(node, context);
      case 'figcaption':
        if (context.inFigure) return [el('figcaption', {}, inlineOnly(walkChildren(node, context)))];
        return blockify(walkChildren(node, context));
      case 'blockquote': {
        const children = blockify(walkChildren(node, context));
        return children.length > 0 ? [el('blockquote', {}, children)] : [];
      }
      case 'h1':
      case 'h2':
      case 'h3':
      case 'h4':
      case 'h5':
      case 'h6': {
        const children = inlineOnly(walkChildren(node, context));
        return children.some(hasText) ? [el(name === 'h1' || name === 'h2' ? 'h2' : 'h3', {}, children)] : [];
      }
      case 'pre': {
        // The lines of a preformatted block stay lines, as line breaks of one paragraph.
        const lines = (node.textContent ?? '').replace(/\r\n?/g, '\n').split('\n');
        const children: Node[] = [];
        lines.forEach((line, index) => {
          if (index > 0) children.push(el('br'));
          if (collapse(line).trim() !== '') children.push(textNode(collapse(line), context));
        });
        return children.some(hasText) ? [el('p', {}, children)] : [];
      }
      default:
        if (BLOCK_CONTAINERS.has(name)) return blockify(walkChildren(node, inner));
        // A span, a font, a bold element and the like: only their text and their marks count.
        return walkChildren(node, inner);
    }
  };

  try {
    const root = el('div', {}, blockify(walkChildren(doc.body, { marks: new Set(), inFigure: false })));
    const overBuilt = overParsedBudget(root);
    if (overBuilt) return refused(overBuilt);
    tidy(root);
    flattenTablesOutsideContract(root);
    return { html: root.innerHTML, imagesLeftOut };
  } catch (error) {
    if (error instanceof PasteOverBudgetError) return refused(error.budget);
    // The walk calls itself once for each level. A document that is still too deep for the
    // engine is over the depth budget, whatever the count said.
    if (error instanceof RangeError) return refused('depth');
    throw error;
  }
}

/**
 * White space, line breaks at the edge of a block, empty elements and
 * neighbouring equal marks: the clean-up that needs the whole tree.
 */
function tidy(root: Element): void {
  normaliseSpaces(root);
  trimLineBreaks(root);
  prune(root);
  mergeMarks(root);
}

/** Drops spaces at the start and end of every block and doubled spaces across inline elements. */
function normaliseSpaces(root: Node): void {
  let last: Text | null = null;
  let leading = true;
  const empties: Text[] = [];
  const breakPoint = () => {
    if (last) last.data = last.data.replace(/ +$/, '');
    last = null;
    leading = true;
  };
  const visit = (node: Node) => {
    if (node.nodeType === 3) {
      const text = node as Text;
      if (leading) text.data = text.data.replace(/^ +/, '');
      if (text.data === '') {
        empties.push(text);
        return;
      }
      last = text;
      leading = text.data.endsWith(' ');
      return;
    }
    const name = nameOf(node);
    if (name === 'br') {
      breakPoint();
      return;
    }
    const inline = INLINE_OUTPUT.has(name);
    if (!inline) breakPoint();
    Array.from(node.childNodes).forEach(visit);
    if (!inline) breakPoint();
  };
  visit(root);
  empties.forEach((text) => text.remove());
}

/** A line break first or last in a block is no line. */
function trimLineBreaks(root: Element): void {
  for (const element of Array.from(root.querySelectorAll('p, h2, h3, figcaption, td, th'))) {
    while (element.firstChild && nameOf(element.firstChild) === 'br') element.firstChild.remove();
    while (element.lastChild && nameOf(element.lastChild) === 'br') element.lastChild.remove();
  }
}

const KEEP_EMPTY = new Set(['hr', 'img', 'br', 'td', 'th']);

/**
 * Removes the elements that hold nothing, from the inside out, and says what `element` holds
 * after that: text, or an image or a divider. An element holds nothing when it has no text and no
 * image or divider below it. A row, a table body and a table hold nothing when no element is left
 * in them. A cell, a line break, an image and a divider always stay.
 *
 * One walk: every element learns from its children what it holds. Reading the text of the whole
 * subtree for each element would cost the depth times the size.
 */
function prune(element: Element): { text: boolean; media: boolean } {
  let text = false;
  let media = false;
  for (const child of Array.from(element.childNodes)) {
    if (child.nodeType === 3) {
      if (/\S/.test((child as Text).data)) text = true;
      continue;
    }
    if (!isElement(child)) continue;
    const name = nameOf(child);
    const below = prune(child);
    const empty = KEEP_EMPTY.has(name)
      ? false
      : name === 'tr' || name === 'tbody' || name === 'table'
        ? child.childElementCount === 0
        : !below.text && !below.media;
    if (empty) {
      child.remove();
      continue;
    }
    if (below.text) text = true;
    if (below.media || name === 'img' || name === 'hr') media = true;
  }
  return { text, media };
}

/** `<strong>a</strong><strong>b</strong>` becomes `<strong>ab</strong>`, down the tree. */
function mergeMarks(element: Element): void {
  for (let child = element.firstChild; child; ) {
    const next: ChildNode | null = child.nextSibling;
    const previous = child.previousSibling;
    if (
      isElement(child) &&
      (MARK_ORDER as readonly string[]).includes(nameOf(child)) &&
      isElement(previous) &&
      nameOf(previous) === nameOf(child)
    ) {
      while (child.firstChild) previous.appendChild(child.firstChild);
      child.remove();
    }
    child = next;
  }
  element.normalize();
  for (const child of Array.from(element.children)) mergeMarks(child);
}
