import Image from '@tiptap/extension-image';
import { Table, TableCell, TableHeader, TableRow } from '@tiptap/extension-table';
import Link from '@tiptap/extension-link';
import { findParentNode, PasteRule, type Extensions, type PasteRuleMatch } from '@tiptap/core';
import { TextSelection, type Transaction } from '@tiptap/pm/state';
import { ReactNodeViewRenderer } from '@tiptap/react';
import { RichTextImageNodeView } from './RichTextImageResizeHandle';

/** Values the contract allows for `data-width` and `data-align`. */
export const IMAGE_WIDTHS = ['25', '50', '75', '100'] as const;
export const IMAGE_ALIGNS = ['left', 'center', 'right'] as const;

const pick = (list: readonly string[], value: string | null | undefined): string | null =>
  value != null && list.includes(value) ? value : null;

const readImg = (img: Element | null, allowBase64: boolean) => {
  const src = img?.getAttribute('src');
  if (!img || !src) return null;
  if (!allowBase64 && src.startsWith('data:')) return null;
  return { src, alt: img.getAttribute('alt') };
};

/**
 * The image node: a block atom that stores a size (`width`), a position
 * (`align`) and an optional plain-text `caption`. Without a caption it is a
 * bare `img`; with a caption it is a `figure` that holds the `img` and a
 * `figcaption`, and the size and position sit on the `figure`.
 */
export const RichTextImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      // `width` of the plain image extension is a pixel size; here it is one
      // of the contract's steps, stored as `data-width`.
      width: {
        default: null,
        parseHTML: (element: HTMLElement) => pick(IMAGE_WIDTHS, element.getAttribute('data-width')),
        renderHTML: (attributes: Record<string, unknown>) => {
          const value = pick(IMAGE_WIDTHS, attributes.width as string | null);
          return value ? { 'data-width': value } : {};
        },
      },
      align: {
        default: null,
        parseHTML: (element: HTMLElement) => pick(IMAGE_ALIGNS, element.getAttribute('data-align')),
        renderHTML: (attributes: Record<string, unknown>) => {
          const value = pick(IMAGE_ALIGNS, attributes.align as string | null);
          return value ? { 'data-align': value } : {};
        },
      },
      caption: {
        default: null,
        parseHTML: () => null,
        rendered: false,
      },
    };
  },

  // The editor shows the image in a frame with a resize handle. The frame and
  // the handle exist in the editor only: `renderHTML` below writes the HTML.
  addNodeView() {
    return ReactNodeViewRenderer(RichTextImageNodeView);
  },

  parseHTML() {
    const allowBase64 = this.options.allowBase64;
    return [
      {
        tag: 'figure',
        priority: 60,
        getAttrs: (element: HTMLElement | string) => {
          if (typeof element === 'string') return false;
          const img = readImg(element.querySelector('img'), allowBase64);
          if (!img) return false;
          const caption = element.querySelector('figcaption')?.textContent?.trim() ?? '';
          return {
            ...img,
            width: pick(IMAGE_WIDTHS, element.getAttribute('data-width')),
            align: pick(IMAGE_ALIGNS, element.getAttribute('data-align')),
            caption: caption === '' ? null : caption,
          };
        },
      },
      {
        tag: allowBase64 ? 'img[src]' : 'img[src]:not([src^="data:"])',
      },
    ];
  },

  renderHTML({ node, HTMLAttributes }) {
    const caption = typeof node.attrs.caption === 'string' ? node.attrs.caption.trim() : '';
    if (caption === '') {
      return ['img', { ...this.options.HTMLAttributes, ...HTMLAttributes }];
    }
    const { 'data-width': dataWidth, 'data-align': dataAlign, ...imgAttributes } = HTMLAttributes;
    const figureAttributes: Record<string, string> = {};
    if (dataWidth) figureAttributes['data-width'] = dataWidth;
    if (dataAlign) figureAttributes['data-align'] = dataAlign;
    return [
      'figure',
      figureAttributes,
      ['img', { ...this.options.HTMLAttributes, ...imgAttributes }],
      ['figcaption', {}, caption],
    ];
  },
});

/** The configured image node of the rich-text editor. */
export const richTextImage = RichTextImage.configure({
  inline: false,
  allowBase64: false,
  HTMLAttributes: { loading: 'lazy' },
});

/**
 * The link mark. TipTap's own paste rule links every address-like text of a paste
 * and replaces the link mark the text already had, so a pasted link named
 * "fuggerei.de" lost its target. This rule finds the same text with the same
 * checks and does nothing on a range that already carries a link.
 */
export const richTextLink = Link.extend({
  addPasteRules() {
    // The rule of TipTap's Link: the same linkify search, the same
    // `isAllowedUri` and `shouldAutoLink` checks, the same mark attributes.
    const [linkRule] = this.parent?.() ?? [];
    const { type } = this;
    return [
      new PasteRule({
        find: (text: string, event?: ClipboardEvent | null) =>
          // linkify writes `http://` before an address without a scheme.
          (linkRule.find as (text: string, event?: ClipboardEvent | null) => PasteRuleMatch[])(text, event).map((match) => {
            const href = (match.data as { href?: string } | undefined)?.href;
            if (!href || !href.startsWith('http://') || /^http:\/\//i.test(match.text)) return match;
            return { ...match, data: { ...match.data, href: `${this.options.defaultProtocol}://${href.slice(7)}` } };
          }),
        handler: (props) => {
          // Returns nothing and not `null`: `null` would drop the other matches of the paste.
          if (props.state.doc.rangeHasMark(props.range.from, props.range.to, type)) return undefined;
          return linkRule.handler(props);
        },
      }),
    ];
  },
}).configure({
  openOnClick: false,
  autolink: true,
  defaultProtocol: 'https',
  HTMLAttributes: { rel: 'noopener noreferrer', target: '_blank' },
});

/** The extensions that go beyond the base kit, exported for headless tests. */
export const richTextImageExtensions = (): Extensions => [richTextImage];

/** Cell attributes of the table extension, all switched off: the stored cell has none. */
const plainCellAttributes = () => ({
  colspan: { default: 1, parseHTML: () => 1, rendered: false },
  rowspan: { default: 1, parseHTML: () => 1, rendered: false },
  colwidth: { default: null, parseHTML: () => null, rendered: false },
  align: { default: null, parseHTML: () => null, rendered: false },
});

/**
 * Puts the cursor in a textblock next to the table: the existing one when the
 * neighbour is a textblock, else a new empty paragraph.
 */
const moveOutOfTable = (tr: Transaction, tablePos: number, tableSize: number, forward: boolean): void => {
  const paragraph = tr.doc.type.schema.nodes.paragraph;
  const edge = forward ? tablePos + tableSize : tablePos;
  const neighbour = forward ? tr.doc.resolve(edge).nodeAfter : tr.doc.resolve(edge).nodeBefore;
  if (neighbour?.isTextblock) {
    tr.setSelection(TextSelection.create(tr.doc, forward ? edge + 1 : edge - 1));
    return;
  }
  tr.insert(edge, paragraph.create());
  tr.setSelection(TextSelection.create(tr.doc, edge + 1));
};

/**
 * The table node: no wrapper, no `colgroup`, no `style`, no attribute, so the
 * stored HTML is `<table><tbody><tr>…</tr></tbody></table>`. Column resizing is
 * off. Tab in the last cell leaves the table, it does not add a row.
 */
export const RichTextTable = Table.extend({
  // The default view writes a `colgroup` and a width into the editor's DOM.
  addNodeView() {
    return null;
  },

  renderHTML() {
    return ['table', {}, ['tbody', 0]];
  },

  addKeyboardShortcuts() {
    const leave = (forward: boolean) => () => {
      const { editor } = this;
      if (forward ? editor.commands.goToNextCell() : editor.commands.goToPreviousCell()) return true;
      const table = findParentNode((node) => node.type.name === 'table')(editor.state.selection);
      if (!table) return false;
      const { tr } = editor.state;
      moveOutOfTable(tr, table.pos, table.node.nodeSize, forward);
      editor.view.dispatch(tr.scrollIntoView());
      return true;
    };
    return { ...this.parent?.(), Tab: leave(true), 'Shift-Tab': leave(false) };
  },
}).configure({ resizable: false, allowTableNodeSelection: false });

export const RichTextTableRow = TableRow;

/** A header cell and a data cell without span, width and alignment. */
export const RichTextTableHeader = TableHeader.extend({ addAttributes: plainCellAttributes });
export const RichTextTableCell = TableCell.extend({ addAttributes: plainCellAttributes });

/** The table nodes of the rich-text editor, exported for headless tests. */
export const richTextTableExtensions = (): Extensions => [
  RichTextTable,
  RichTextTableRow,
  RichTextTableHeader,
  RichTextTableCell,
];
