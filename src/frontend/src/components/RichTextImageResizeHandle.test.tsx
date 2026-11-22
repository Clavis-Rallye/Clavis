import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { Editor } from '@tiptap/react';
import { NodeSelection } from '@tiptap/pm/state';

let editorUnderTest: Editor | null = null;
vi.mock('@tiptap/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tiptap/react')>();
  return {
    ...actual,
    useEditor: (...args: Parameters<typeof actual.useEditor>) => {
      const editor = actual.useEditor(...args);
      editorUnderTest = editor;
      return editor;
    },
  };
});

import RichTextEditor from './RichTextEditor';
import { snapImageWidth } from './RichTextImageResizeHandle';

// ProseMirror asks for rectangles jsdom lacks.
const emptyRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] });
Range.prototype.getClientRects = emptyRects as unknown as Range['getClientRects'];
Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
Element.prototype.getClientRects = emptyRects as unknown as Element['getClientRects'];
document.elementFromPoint = () => null;

const COLUMN = 1000;
const SRC = '/api/orgs/stadt/media/a.png';

describe('snapImageWidth', () => {
  it.each([
    [0, '25'],
    [10, '25'],
    [25, '25'],
    [37, '25'],
    [37.5, '50'],
    [40, '50'],
    [60, '50'],
    [62.5, '75'],
    [63, '75'],
    [74, '75'],
    [87.5, '100'],
    [90, '100'],
    [100, '100'],
    [180, '100'],
  ])('snaps %s percent to %s', (percent, expected) => {
    expect(snapImageWidth(percent)).toBe(expected);
  });
});

const rectOf = (width: number) => new DOMRect(0, 0, width, 100);

// The picture is the element with `data-image-picture`; the node view wrapper is the text column.
const mockRects = (frameShare: number) => {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    if (this.hasAttribute('data-image-picture')) return rectOf((COLUMN * frameShare) / 100);
    if (this.hasAttribute('data-node-view-wrapper')) return rectOf(COLUMN);
    return rectOf(0);
  });
};

const renderEditor = async (image: string) => {
  const onChange = vi.fn();
  render(<RichTextEditor value={`<p>Vorher</p>${image}<p>Nachher</p>`} onChange={onChange} ariaLabel="Beschreibung" orgSlug="stadt" />);
  await screen.findByRole('textbox', { name: 'Beschreibung' });
  const handle = await screen.findByRole('button', { name: 'Bildgröße ziehen' });
  const html = () => editorUnderTest!.getHTML();
  return { handle, html, onChange };
};

const pointer = (type: string, init: PointerEventInit & { target?: Window | Element }) => {
  const { target = window, ...rest } = init;
  act(() => {
    fireEvent(target, new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, ...rest }));
  });
};

// A drag from the right edge of an image that fills `startShare` percent, to `endShare` percent.
const drag = (
  handle: HTMLElement,
  startShare: number,
  endShare: number,
  pointerType: 'mouse' | 'pen' | 'touch' = 'mouse',
  end: 'pointerup' | 'pointercancel' = 'pointerup',
) => {
  const startX = (COLUMN * startShare) / 100;
  pointer('pointerdown', { target: handle, pointerType, button: 0, clientX: startX });
  pointer('pointermove', { pointerType, clientX: (COLUMN * endShare) / 100 });
  pointer(end, { pointerType, clientX: (COLUMN * endShare) / 100 });
};

beforeEach(() => {
  mockRects(50);
});

afterEach(() => {
  vi.restoreAllMocks();
  editorUnderTest = null;
});

const IMAGE = `<img src="${SRC}" alt="Das Rathaus" data-width="50">`;
const stored = (width: string) => `<img loading="lazy" src="${SRC}" alt="Das Rathaus" data-width="${width}">`;

describe('image resize handle', () => {
  it('has a label and is not part of the stored HTML', async () => {
    const { handle, html } = await renderEditor(IMAGE);
    expect(handle).toHaveAttribute('aria-label', 'Bildgröße ziehen');
    expect(html()).toBe(`<p>Vorher</p>${stored('50')}<p>Nachher</p>`);
  });

  it.each([
    [40, '50'],
    [60, '50'],
    [90, '100'],
    [10, '25'],
  ])('a drag to %s percent stores %s', async (target, expected) => {
    const { handle, html } = await renderEditor(IMAGE);
    drag(handle, 50, target);
    expect(html()).toBe(`<p>Vorher</p>${stored(expected)}<p>Nachher</p>`);
  });

  it('a drag to 70 percent stores 75', async () => {
    const { handle, html } = await renderEditor(IMAGE);
    drag(handle, 50, 70);
    expect(html()).toContain('data-width="75"');
  });

  it('works with a pen', async () => {
    const { handle, html } = await renderEditor(IMAGE);
    drag(handle, 50, 100, 'pen');
    expect(html()).toContain('data-width="100"');
  });

  it('sizes an image without a width and stores no style or pixel size', async () => {
    mockRects(30);
    const { handle, html } = await renderEditor(`<img src="${SRC}" alt="Das Rathaus">`);
    drag(handle, 30, 80);
    const written = html();
    expect(written).toBe(`<p>Vorher</p>${stored('75')}<p>Nachher</p>`);
    expect(written).not.toContain('style');
  });

  it('sizes a figure with a caption on the figure', async () => {
    const { handle, html } = await renderEditor(
      `<figure data-width="50"><img src="${SRC}" alt="A"><figcaption>Unter dem Bild</figcaption></figure>`,
    );
    drag(handle, 50, 100);
    expect(html()).toBe(
      `<p>Vorher</p><figure data-width="100"><img loading="lazy" src="${SRC}" alt="A"><figcaption>Unter dem Bild</figcaption></figure><p>Nachher</p>`,
    );
  });

  it('follows a centred image on both sides', async () => {
    const { handle, html } = await renderEditor(`<img src="${SRC}" alt="Das Rathaus" data-width="50" data-align="center">`);
    // The pointer moves 125 pixels, the image grows by 250 to 75 percent.
    drag(handle, 50, 62.5);
    expect(html()).toContain('data-width="75"');
    expect(html()).toContain('data-align="center"');
  });

  it('shows the share while dragging', async () => {
    const { handle } = await renderEditor(IMAGE);
    const frame = handle.parentElement!.querySelector('[data-image-picture]') as HTMLElement;
    pointer('pointerdown', { target: handle, pointerType: 'mouse', button: 0, clientX: 500 });
    pointer('pointermove', { pointerType: 'mouse', clientX: 640 });
    expect(frame).toHaveStyle({ width: '64%' });
    pointer('pointerup', { pointerType: 'mouse', clientX: 640 });
    await waitFor(() => expect(handle.parentElement!.querySelector('[data-image-picture]')).toHaveAttribute('data-width', '75'));
    expect(handle.parentElement!.querySelector('[data-image-picture]')).toHaveStyle({ width: '75%' });
  });

  it('ends cleanly when the pointer is released on the window', async () => {
    const { handle, html } = await renderEditor(IMAGE);
    pointer('pointerdown', { target: handle, pointerType: 'mouse', button: 0, clientX: 500 });
    pointer('pointermove', { pointerType: 'mouse', clientX: 900 });
    pointer('pointerup', { target: window, pointerType: 'mouse', clientX: 5000 });
    expect(html()).toContain('data-width="100"');
    // A later move does nothing.
    pointer('pointermove', { pointerType: 'mouse', clientX: 100 });
    expect(html()).toContain('data-width="100"');
  });

  it('changes nothing when the drag is cancelled', async () => {
    const { handle, html } = await renderEditor(IMAGE);
    drag(handle, 50, 100, 'mouse', 'pointercancel');
    expect(html()).toBe(`<p>Vorher</p>${stored('50')}<p>Nachher</p>`);
    expect(handle.parentElement!.querySelector('[data-image-picture]')).toHaveStyle({ width: '50%' });
  });

  it('changes nothing on a click without a move', async () => {
    const { handle, html, onChange } = await renderEditor(IMAGE);
    onChange.mockClear();
    pointer('pointerdown', { target: handle, pointerType: 'mouse', button: 0, clientX: 500 });
    pointer('pointerup', { pointerType: 'mouse', clientX: 500 });
    expect(html()).toBe(`<p>Vorher</p>${stored('50')}<p>Nachher</p>`);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('changes nothing when the drag ends on the width it started with', async () => {
    const { handle, html, onChange } = await renderEditor(IMAGE);
    onChange.mockClear();
    drag(handle, 50, 55);
    expect(html()).toBe(`<p>Vorher</p>${stored('50')}<p>Nachher</p>`);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('ignores a touch', async () => {
    const { handle, html } = await renderEditor(IMAGE);
    drag(handle, 50, 100, 'touch');
    expect(html()).toBe(`<p>Vorher</p>${stored('50')}<p>Nachher</p>`);
  });

  it('ignores a button other than the primary one', async () => {
    const { handle, html } = await renderEditor(IMAGE);
    pointer('pointerdown', { target: handle, pointerType: 'mouse', button: 2, clientX: 500 });
    pointer('pointermove', { pointerType: 'mouse', clientX: 1000 });
    pointer('pointerup', { pointerType: 'mouse', clientX: 1000 });
    expect(html()).toBe(`<p>Vorher</p>${stored('50')}<p>Nachher</p>`);
  });

  it('takes the pointer on the handle', async () => {
    const { handle } = await renderEditor(IMAGE);
    const capture = vi.fn();
    const release = vi.fn();
    handle.setPointerCapture = capture;
    handle.releasePointerCapture = release;
    pointer('pointerdown', { target: handle, pointerType: 'mouse', button: 0, pointerId: 7, clientX: 500 });
    expect(capture).toHaveBeenCalledWith(7);
    pointer('pointerup', { pointerType: 'mouse', pointerId: 7, clientX: 500 });
    expect(release).toHaveBeenCalledWith(7);
  });

  it('leaves the image selectable and the rest of the node view alone', async () => {
    await renderEditor(IMAGE);
    const editor = editorUnderTest!;
    let position = -1;
    editor.state.doc.descendants((node, pos) => {
      if (node.type.name === 'image') position = pos;
    });
    act(() => {
      editor.commands.setNodeSelection(position);
    });
    expect(editor.state.selection).toBeInstanceOf(NodeSelection);
    expect(screen.getByRole('textbox', { name: 'Beschreibung' }).querySelector('img')).not.toBeNull();
  });
});
