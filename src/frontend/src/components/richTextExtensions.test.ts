import { afterEach, describe, expect, it } from 'vitest';
import { Editor } from '@tiptap/core';
import Document from '@tiptap/extension-document';
import Paragraph from '@tiptap/extension-paragraph';
import Text from '@tiptap/extension-text';
import { richTextImageExtensions } from './richTextExtensions';

let editor: Editor | null = null;

const make = (content: string) => {
  editor = new Editor({
    extensions: [Document, Paragraph, Text, ...richTextImageExtensions()],
    content,
  });
  return editor;
};

afterEach(() => {
  editor?.destroy();
  editor = null;
});

const SRC = '/api/orgs/stadt/media/content/rathaus.jpg';

describe('image node: reading and writing', () => {
  it('round-trips a 0.9.1 image byte for byte', () => {
    const html = `<img loading="lazy" src="${SRC}" alt="Das Rathaus">`;
    expect(make(html).getHTML()).toBe(html);
  });

  it('round-trips a bare image with size and position', () => {
    const html = `<img loading="lazy" src="${SRC}" alt="Rathaus" data-width="50" data-align="right">`;
    const e = make(html);
    expect(e.getJSON().content?.[0].attrs).toMatchObject({ width: '50', align: 'right', caption: null });
    expect(e.getHTML()).toBe(html);
  });

  it('round-trips a figure with a caption, size and position', () => {
    const html = `<figure data-width="75" data-align="center"><img loading="lazy" src="${SRC}" alt="Rathaus"><figcaption>Das Rathaus im Mai</figcaption></figure>`;
    const e = make(html);
    expect(e.getJSON().content?.[0].attrs).toMatchObject({ width: '75', align: 'center', caption: 'Das Rathaus im Mai' });
    expect(e.getHTML()).toBe(html);
  });

  it('round-trips a figure with a caption and without size', () => {
    const html = `<figure><img loading="lazy" src="${SRC}" alt="Rathaus"><figcaption>Nur Text</figcaption></figure>`;
    expect(make(html).getHTML()).toBe(html);
  });

  it('reads a figure without a stored loading attribute and writes it back', () => {
    const e = make(`<figure data-width="25"><img src="${SRC}" alt="A"><figcaption>B</figcaption></figure>`);
    expect(e.getHTML()).toBe(
      `<figure data-width="25"><img loading="lazy" src="${SRC}" alt="A"><figcaption>B</figcaption></figure>`,
    );
  });

  it('keeps the image a block atom', () => {
    const e = make(`<p>Vorher</p><img src="${SRC}" alt="A"><p>Nachher</p>`);
    expect(e.getJSON().content?.map((n) => n.type)).toEqual(['paragraph', 'image', 'paragraph']);
  });

  it.each([
    ['data-width="33"', 'data-width'],
    ['data-width="50;x"', 'data-width'],
    ['data-align="justify"', 'data-align'],
  ])('ignores a value outside the list on a bare image (%s)', (attribute, name) => {
    const html = make(`<img src="${SRC}" alt="A" ${attribute}>`).getHTML();
    expect(html).not.toContain(name);
  });

  it('ignores values outside the lists on a figure', () => {
    const html = make(
      `<figure data-width="33" data-align="justify"><img src="${SRC}" alt="A"><figcaption>B</figcaption></figure>`,
    ).getHTML();
    expect(html).toBe(`<figure><img loading="lazy" src="${SRC}" alt="A"><figcaption>B</figcaption></figure>`);
  });

  it('keeps the valid one of two attributes when the other is outside its list', () => {
    const html = make(`<img src="${SRC}" alt="A" data-width="33" data-align="left">`).getHTML();
    expect(html).toBe(`<img loading="lazy" src="${SRC}" alt="A" data-align="left">`);
  });

  it('reads a figure with an empty caption as a bare image', () => {
    const html = make(
      `<figure data-width="50"><img src="${SRC}" alt="A"><figcaption>  </figcaption></figure>`,
    ).getHTML();
    expect(html).toBe(`<img loading="lazy" src="${SRC}" alt="A" data-width="50">`);
  });

  it('drops a data: source', () => {
    const e = make('<img src="data:image/png;base64,AAAA" alt="A">');
    expect(e.getHTML()).not.toContain('<img');
  });
});

describe('image node: commands', () => {
  it('turns a bare image into a figure with a caption and back with an empty one', () => {
    const e = make(`<img src="${SRC}" alt="A" data-width="50" data-align="left">`);
    e.commands.setNodeSelection(0);
    e.commands.updateAttributes('image', { caption: 'Unter dem Bild' });
    expect(e.getHTML()).toBe(
      `<figure data-width="50" data-align="left"><img loading="lazy" src="${SRC}" alt="A"><figcaption>Unter dem Bild</figcaption></figure>`,
    );
    e.commands.setNodeSelection(0);
    e.commands.updateAttributes('image', { caption: '' });
    expect(e.getHTML()).toBe(`<img loading="lazy" src="${SRC}" alt="A" data-width="50" data-align="left">`);
  });

  it('sets size and position with updateAttributes', () => {
    const e = make(`<img src="${SRC}" alt="A">`);
    e.commands.setNodeSelection(0);
    e.commands.updateAttributes('image', { width: '75', align: 'center' });
    expect(e.getHTML()).toBe(`<img loading="lazy" src="${SRC}" alt="A" data-width="75" data-align="center">`);
  });

  it('keeps setImage working', () => {
    const e = make('<p></p>');
    e.commands.setImage({ src: SRC, alt: 'A' });
    expect(e.getHTML()).toContain(`<img loading="lazy" src="${SRC}" alt="A">`);
  });
});
