import Image from '@tiptap/extension-image';
import type { Extensions } from '@tiptap/core';
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

/** The extensions that go beyond the base kit, exported for headless tests. */
export const richTextImageExtensions = (): Extensions => [richTextImage];
