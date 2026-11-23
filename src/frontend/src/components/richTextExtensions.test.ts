import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { Editor } from '@tiptap/core';
import Document from '@tiptap/extension-document';
import Paragraph from '@tiptap/extension-paragraph';
import Text from '@tiptap/extension-text';
import { IMAGE_WIDTHS, richTextImageExtensions, richTextLink, richTextTableExtensions } from './richTextExtensions';
import { snapImageWidth } from './RichTextImageResizeHandle';

let editor: Editor | null = null;

const make = (content: string) => {
  editor = new Editor({
    extensions: [Document, Paragraph, Text, ...richTextImageExtensions(), ...richTextTableExtensions()],
    content,
  });
  return editor;
};

const pressKey = (e: Editor, key: string, shiftKey = false) => {
  const event = new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true });
  e.view.dom.dispatchEvent(event);
  return event.defaultPrevented;
};

/** Text of the block the cursor stands in. */
const cursorBlock = (e: Editor) => e.state.selection.$from.parent.textContent;
const cursorInTable = (e: Editor) => e.isActive('table');

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

describe('image node: the resize handle', () => {
  it('offers the same sizes as the contract', () => {
    const sizes = [25, 50, 75, 100];
    expect(IMAGE_WIDTHS.map(Number)).toEqual(sizes);
    expect(sizes.map((size) => snapImageWidth(size))).toEqual([...IMAGE_WIDTHS]);
  });

  it('writes the same HTML with the node view registered', () => {
    const html = `<figure data-width="75" data-align="center"><img loading="lazy" src="${SRC}" alt="Rathaus"><figcaption>Das Rathaus im Mai</figcaption></figure>`;
    const written = make(html).getHTML();
    expect(written).toBe(html);
    expect(written).not.toContain('style');
    expect(written).not.toContain('resize');
  });
});

const STORED_TABLE =
  '<table><tbody><tr><th><p>Tag</p></th><th><p></p></th><th><p></p></th></tr>' +
  '<tr><td><p></p></td><td><p>Preis</p></td><td><p></p></td></tr>' +
  '<tr><td><p></p></td><td><p></p></td><td><p></p></td></tr></tbody></table>';

describe('table: reading and writing', () => {
  it('writes an inserted 3 by 3 table with a header row and no attribute', () => {
    const e = make('<p></p>');
    e.commands.insertTable({ rows: 3, cols: 3, withHeaderRow: true });
    e.commands.insertContent('Tag');
    for (let i = 0; i < 4; i += 1) e.commands.goToNextCell();
    e.commands.insertContent('Preis');
    const html = e.getHTML();
    expect(html).toBe(STORED_TABLE);
    expect(html).not.toMatch(/style|colgroup|class|colspan|rowspan|colwidth|align/);
  });

  it('round-trips a stored table unchanged', () => {
    expect(make(STORED_TABLE).getHTML()).toBe(STORED_TABLE);
  });

  it('drops spans, widths, alignment, style and class of a pasted table', () => {
    const pasted =
      '<table class="MsoTable" style="width:300px"><colgroup><col width="100"></colgroup><thead><tr>' +
      '<th colspan="2" style="text-align:right" align="right">A</th></tr></thead><tbody><tr>' +
      '<td rowspan="2" colwidth="80" class="x">B</td><td style="color:red">C</td></tr></tbody></table>';
    const html = make(pasted).getHTML();
    expect(html).not.toMatch(/style|colgroup|class|colspan|rowspan|colwidth|align|width/);
    expect(html).toMatch(/^<table><tbody><tr><th><p>A<\/p><\/th>/);
    expect(html).toContain('<td><p>B</p></td><td><p>C</p></td>');
  });

  it('turns column resizing off', () => {
    const e = make(STORED_TABLE);
    expect(e.view.dom.querySelector('colgroup')).toBeNull();
    expect(e.view.dom.querySelector('table')?.getAttribute('style')).toBeNull();
  });
});

describe('table: the keyboard never traps', () => {
  it('moves between cells with Tab and Shift+Tab', () => {
    const e = make(STORED_TABLE);
    e.commands.setTextSelection(3);
    expect(cursorBlock(e)).toBe('Tag');
    expect(pressKey(e, 'Tab')).toBe(true);
    expect(cursorInTable(e)).toBe(true);
    expect(pressKey(e, 'Tab', true)).toBe(true);
    expect(cursorBlock(e)).toBe('Tag');
  });

  it('leaves the table with Tab in the last cell, into the paragraph after it, and adds no row', () => {
    const e = make(`${STORED_TABLE}<p>Danach</p>`);
    // Put the cursor in the last cell.
    let lastCell = 0;
    e.state.doc.descendants((node, pos) => {
      if (node.type.name === 'tableCell') lastCell = pos;
    });
    e.commands.setTextSelection(lastCell + 2);
    expect(cursorInTable(e)).toBe(true);
    expect(pressKey(e, 'Tab')).toBe(true);
    expect(cursorInTable(e)).toBe(false);
    expect(cursorBlock(e)).toBe('Danach');
    expect(e.getHTML()).toBe(`${STORED_TABLE}<p>Danach</p>`);
  });

  it('makes a paragraph after the table when there is none', () => {
    const e = make(STORED_TABLE);
    let lastCell = 0;
    e.state.doc.descendants((node, pos) => {
      if (node.type.name === 'tableCell') lastCell = pos;
    });
    e.commands.setTextSelection(lastCell + 2);
    expect(pressKey(e, 'Tab')).toBe(true);
    expect(cursorInTable(e)).toBe(false);
    expect(e.state.doc.lastChild?.type.name).toBe('paragraph');
    expect(e.state.selection.$from.parent).toBe(e.state.doc.lastChild);
    expect(e.getHTML()).toBe(`${STORED_TABLE}<p></p>`);
  });

  it('leaves the table with Shift+Tab in the first cell, into the text before it', () => {
    const e = make(`<p>Davor</p>${STORED_TABLE}`);
    e.commands.setTextSelection(e.state.doc.firstChild!.nodeSize + 4);
    expect(cursorInTable(e)).toBe(true);
    expect(cursorBlock(e)).toBe('Tag');
    expect(pressKey(e, 'Tab', true)).toBe(true);
    expect(cursorInTable(e)).toBe(false);
    expect(cursorBlock(e)).toBe('Davor');
    expect(e.state.selection.from).toBe('Davor'.length + 1);
  });

  it('makes a paragraph before the table when it is the first thing in the text', () => {
    const e = make(STORED_TABLE);
    e.commands.setTextSelection(3);
    expect(cursorInTable(e)).toBe(true);
    expect(pressKey(e, 'Tab', true)).toBe(true);
    expect(cursorInTable(e)).toBe(false);
    expect(e.state.doc.firstChild?.type.name).toBe('paragraph');
    expect(e.state.selection.$from.parent).toBe(e.state.doc.firstChild);
  });

  it('leaves Tab alone outside a table', () => {
    const e = make('<p>Text</p>');
    e.commands.setTextSelection(2);
    expect(pressKey(e, 'Tab')).toBe(false);
  });
});

describe('link: paste and typing', () => {
  // jsdom has no ClipboardEvent, which `view.pasteHTML` and TipTap's paste rules create.
  beforeAll(() => {
    vi.stubGlobal('ClipboardEvent', class extends Event {});
  });
  afterAll(() => {
    vi.unstubAllGlobals();
  });

  const makeLinked = () => {
    editor = new Editor({ extensions: [Document, Paragraph, Text, richTextLink], content: '<p></p>' });
    return editor;
  };
  const hrefs = (e: Editor) =>
    Array.from(new DOMParser().parseFromString(e.getHTML(), 'text/html').querySelectorAll('a')).map((a) =>
      a.getAttribute('href'),
    );

  it('configures the link as before, with https as the default protocol', () => {
    expect(richTextLink.options).toMatchObject({
      openOnClick: false,
      autolink: true,
      defaultProtocol: 'https',
      HTMLAttributes: { rel: 'noopener noreferrer', target: '_blank' },
    });
  });

  it('keeps the href of a pasted link whose text is an address', () => {
    const e = makeLinked();
    e.view.pasteHTML('<p>Mehr auf <a href="https://www.augsburg.de/">augsburg.de</a>.</p>');
    expect(hrefs(e)).toEqual(['https://www.augsburg.de/']);
  });

  it('keeps the deep href of a pasted link', () => {
    const e = makeLinked();
    e.view.pasteHTML('<p><a href="https://www.fuggerei.de/de/besuch">fuggerei.de</a></p>');
    expect(hrefs(e)).toEqual(['https://www.fuggerei.de/de/besuch']);
  });

  it('keeps a pasted mailto link', () => {
    const e = makeLinked();
    e.view.pasteHTML('<p><a href="mailto:info@fuggerei.de">info@fuggerei.de</a></p>');
    expect(hrefs(e)).toEqual(['mailto:info@fuggerei.de']);
  });

  it('links an address in pasted text and gives it https', () => {
    const e = makeLinked();
    e.view.pasteText('Mehr auf augsburg.de');
    expect(hrefs(e)).toEqual(['https://augsburg.de']);
  });

  it('links an address in pasted HTML that has no link yet', () => {
    const e = makeLinked();
    e.view.pasteHTML('<p>Seite: www.augsburg.de</p>');
    expect(hrefs(e)).toEqual(['https://www.augsburg.de']);
  });

  it('links the addresses around a pasted link that already has a target', () => {
    const e = makeLinked();
    e.view.pasteHTML('<p>a.de <a href="https://www.fuggerei.de/de/besuch">fuggerei.de</a> b.de</p>');
    expect(hrefs(e)).toEqual(['https://a.de', 'https://www.fuggerei.de/de/besuch', 'https://b.de']);
  });

  it('gives an address typed with a space https', () => {
    const e = makeLinked();
    e.commands.focus();
    for (const char of 'augsburg.de ') {
      const { from, to } = e.state.selection;
      const handled = e.view.someProp('handleTextInput', (f) => f(e.view, from, to, char, () => e.state.tr.insertText(char, from, to)));
      if (!handled) e.view.dispatch(e.state.tr.insertText(char, from, to));
    }
    expect(hrefs(e)).toEqual(['https://augsburg.de']);
  });
});
