import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Editor } from '@tiptap/react';
import { NodeSelection } from '@tiptap/pm/state';

// The tests select an image through the editor's commands: a click on an image
// needs coordinates, which jsdom does not have.
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

// ProseMirror scrolls the cursor into view and asks for rectangles jsdom lacks.
const emptyRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] });
Range.prototype.getClientRects = emptyRects as unknown as Range['getClientRects'];
Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
Element.prototype.getClientRects = emptyRects as unknown as Element['getClientRects'];
document.elementFromPoint = () => null;

const IMAGE = '<img src="/api/orgs/stadt/media/a.png" alt="Das Rathaus" data-width="50" data-align="center">';
const DOCUMENT = `<p>Vorher</p>${IMAGE}<p>Nachher</p>`;

const current = (): Editor => {
  if (!editorUnderTest) throw new Error('no editor yet');
  return editorUnderTest;
};

const selectImage = async () => {
  const editor = current();
  let position = -1;
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name === 'image') position = pos;
  });
  expect(position).toBeGreaterThanOrEqual(0);
  act(() => {
    editor.commands.setNodeSelection(position);
  });
  return screen.findByRole('toolbar', { name: 'Bild' });
};

const renderEditor = async (value = DOCUMENT) => {
  const onChange = vi.fn();
  render(<RichTextEditor value={value} onChange={onChange} ariaLabel="Beschreibung" orgSlug="stadt" />);
  await screen.findByRole('textbox', { name: 'Beschreibung' });
  return { onChange, user: userEvent.setup() };
};

afterEach(() => {
  editorUnderTest = null;
});

describe('the image menu', () => {
  it('is not shown while no image is selected', async () => {
    await renderEditor();
    expect(screen.queryByRole('toolbar', { name: 'Bild' })).not.toBeInTheDocument();
    act(() => {
      current().commands.setTextSelection(3);
    });
    expect(screen.queryByRole('toolbar', { name: 'Bild' })).not.toBeInTheDocument();
  });

  it('shows the sizes, the positions, the description and remove for a selected image', async () => {
    await renderEditor();
    const menu = await selectImage();
    const sizes = within(menu).getByRole('group', { name: 'Größe' });
    expect(within(sizes).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual([
      'Klein',
      'Mittel',
      'Groß',
      'Volle Breite',
      'Originalgröße',
    ]);
    const positions = within(menu).getByRole('group', { name: 'Position' });
    expect(within(positions).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Links',
      'Mitte',
      'Rechts',
    ]);
    expect(within(menu).getByRole('button', { name: 'Beschreibung und Bildunterschrift' })).toBeInTheDocument();
    expect(within(menu).getByRole('button', { name: 'Bild entfernen' })).toBeInTheDocument();
  });

  it('shows the current size and position as pressed', async () => {
    await renderEditor();
    const menu = await selectImage();
    const pressed = within(menu)
      .getAllByRole('button', { pressed: true })
      .map((b) => b.getAttribute('aria-label') ?? b.textContent);
    expect(pressed).toEqual(['Mittel', 'Mitte']);
  });

  it('shows original size and left for an image without a size and a position', async () => {
    await renderEditor('<img src="/api/orgs/stadt/media/a.png" alt="Das Rathaus">');
    const menu = await selectImage();
    const pressed = within(menu)
      .getAllByRole('button', { pressed: true })
      .map((b) => b.getAttribute('aria-label') ?? b.textContent);
    expect(pressed).toEqual(['Originalgröße', 'Links']);
  });

  it.each([
    ['Klein', '25'],
    ['Groß', '75'],
    ['Volle Breite', '100'],
  ])('sets the size %s and shows it as pressed', async (name, width) => {
    const { onChange, user } = await renderEditor();
    const menu = await selectImage();
    await user.click(within(menu).getByRole('button', { name }));
    expect(current().getAttributes('image').width).toBe(width);
    expect(onChange).toHaveBeenLastCalledWith(expect.stringContaining(`data-width="${width}"`));
    await waitFor(() => expect(within(menu).getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true'));
    expect(within(menu).getByRole('button', { name: 'Mittel' })).toHaveAttribute('aria-pressed', 'false');
    // The image stays selected, so the next change works at once.
    expect(current().state.selection).toBeInstanceOf(NodeSelection);
  });

  it('sets the original size by removing the width', async () => {
    const { onChange, user } = await renderEditor();
    const menu = await selectImage();
    await user.click(within(menu).getByRole('button', { name: 'Originalgröße' }));
    expect(current().getAttributes('image').width).toBeNull();
    expect(onChange).toHaveBeenLastCalledWith(expect.not.stringContaining('data-width'));
  });

  it.each([
    ['Links', 'left'],
    ['Rechts', 'right'],
  ])('sets the position %s', async (name, align) => {
    const { onChange, user } = await renderEditor();
    const menu = await selectImage();
    await user.click(within(menu).getByRole('button', { name }));
    expect(current().getAttributes('image').align).toBe(align);
    expect(onChange).toHaveBeenLastCalledWith(expect.stringContaining(`data-align="${align}"`));
    await waitFor(() => expect(within(menu).getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true'));
  });

  it('removes the image and puts the focus back into the text', async () => {
    const { onChange, user } = await renderEditor();
    const menu = await selectImage();
    await user.click(within(menu).getByRole('button', { name: 'Bild entfernen' }));
    expect(onChange).toHaveBeenLastCalledWith('<p>Vorher</p><p>Nachher</p>');
    expect(screen.queryByRole('toolbar', { name: 'Bild' })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Beschreibung' })).toHaveFocus());
  });

  it('does not take the focus when it shows', async () => {
    await renderEditor();
    const box = screen.getByRole('textbox', { name: 'Beschreibung' });
    box.focus();
    const menu = await selectImage();
    expect(menu.contains(document.activeElement)).toBe(false);
  });

  describe('keyboard', () => {
    it('moves the focus into the menu with Alt+F10, moves with the arrows, and returns with Escape', async () => {
      const { user } = await renderEditor();
      screen.getByRole('textbox', { name: 'Beschreibung' }).focus();
      const menu = await selectImage();
      const [first, second] = within(menu).getAllByRole('button');
      await user.keyboard('{Alt>}{F10}{/Alt}');
      expect(first).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(second).toHaveFocus();
      await user.keyboard('{ArrowLeft}{ArrowLeft}');
      expect(within(menu).getByRole('button', { name: 'Bild entfernen' })).toHaveFocus();
      await user.keyboard('{Home}');
      expect(first).toHaveFocus();
      await user.keyboard('{End}');
      expect(within(menu).getByRole('button', { name: 'Bild entfernen' })).toHaveFocus();
      await user.keyboard('{Escape}');
      expect(screen.getByRole('textbox', { name: 'Beschreibung' })).toHaveFocus();
      expect(current().state.selection).toBeInstanceOf(NodeSelection);
      expect(screen.getByRole('toolbar', { name: 'Bild' })).toBeInTheDocument();
    });

    it('has one tab stop', async () => {
      await renderEditor();
      const menu = await selectImage();
      const stops = within(menu)
        .getAllByRole('button')
        .filter((b) => b.tabIndex === 0);
      expect(stops).toHaveLength(1);
    });

    it('does nothing with Alt+F10 while no image is selected', async () => {
      const { user } = await renderEditor();
      const box = screen.getByRole('textbox', { name: 'Beschreibung' });
      box.focus();
      await user.keyboard('{Alt>}{F10}{/Alt}');
      expect(box).toHaveFocus();
    });
  });

  describe('the description and caption dialog', () => {
    it('opens from the menu with the values of the image and saves a caption', async () => {
      const { onChange, user } = await renderEditor();
      const menu = await selectImage();
      await user.click(within(menu).getByRole('button', { name: 'Beschreibung und Bildunterschrift' }));
      const dialog = await screen.findByRole('dialog', { name: 'Beschreibung und Bildunterschrift' });
      expect(within(dialog).getByRole('textbox', { name: /Alternativtext/ })).toHaveValue('Das Rathaus');
      await user.type(within(dialog).getByRole('textbox', { name: /Bildunterschrift/ }), 'Am Markt');
      await user.click(within(dialog).getByRole('radio', { name: 'Volle Breite' }));
      await user.click(within(dialog).getByRole('button', { name: 'Speichern' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(current().getAttributes('image')).toMatchObject({ caption: 'Am Markt', width: '100', align: 'center' });
      const html = onChange.mock.calls[onChange.mock.calls.length - 1][0] as string;
      expect(html).toContain('<figure data-width="100" data-align="center">');
      expect(html).toContain('<figcaption>Am Markt</figcaption>');
      await waitFor(() => expect(screen.getByRole('textbox', { name: 'Beschreibung' })).toHaveFocus());
    });

    it('turns an emptied caption back into a bare image', async () => {
      const figure =
        '<figure data-width="50"><img src="/api/orgs/stadt/media/a.png" alt="Das Rathaus"><figcaption>Am Markt</figcaption></figure>';
      const { onChange, user } = await renderEditor(`<p>Vorher</p>${figure}`);
      const menu = await selectImage();
      await user.click(within(menu).getByRole('button', { name: 'Beschreibung und Bildunterschrift' }));
      const dialog = await screen.findByRole('dialog');
      await user.clear(within(dialog).getByRole('textbox', { name: /Bildunterschrift/ }));
      await user.click(within(dialog).getByRole('button', { name: 'Speichern' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      const html = onChange.mock.calls[onChange.mock.calls.length - 1][0] as string;
      expect(html).not.toContain('figure');
      expect(html).not.toContain('figcaption');
      expect(html).toContain('<img');
      expect(html).toContain('data-width="50"');
    });

    it('does not change the image when the alternative text is empty', async () => {
      const { onChange, user } = await renderEditor();
      const menu = await selectImage();
      await user.click(within(menu).getByRole('button', { name: 'Beschreibung und Bildunterschrift' }));
      const dialog = await screen.findByRole('dialog');
      await user.clear(within(dialog).getByRole('textbox', { name: /Alternativtext/ }));
      await user.click(within(dialog).getByRole('button', { name: 'Speichern' }));
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(onChange).not.toHaveBeenCalled();
      expect(current().getAttributes('image').alt).toBe('Das Rathaus');
    });
  });

  describe('the toolbar button', () => {
    it('is the insert button while no image is selected', async () => {
      const { user } = await renderEditor();
      await user.click(screen.getByRole('button', { name: 'Bild einfügen' }));
      expect(await screen.findByRole('dialog', { name: 'Bild einfügen' })).toBeInTheDocument();
    });

    it('is "edit image" with an image selected and opens the settings dialog', async () => {
      const { user } = await renderEditor();
      await selectImage();
      const button = await screen.findByRole('button', { name: 'Bild bearbeiten' });
      expect(screen.queryByRole('button', { name: 'Bild einfügen' })).not.toBeInTheDocument();
      await user.click(button);
      expect(await screen.findByRole('dialog', { name: 'Beschreibung und Bildunterschrift' })).toBeInTheDocument();
    });
  });
});

describe('the image in the editor', () => {
  it('carries its size and position as attributes the styles use', async () => {
    await renderEditor();
    const image = screen.getByRole('textbox', { name: 'Beschreibung' }).querySelector('img');
    expect(image).toHaveAttribute('data-width', '50');
    expect(image).toHaveAttribute('data-align', 'center');
  });

  it('shows the size as a share of the text column and the position as margins', async () => {
    await renderEditor();
    const image = screen.getByRole('textbox', { name: 'Beschreibung' }).querySelector('img') as HTMLElement;
    const style = getComputedStyle(image);
    expect(style.width).toBe('50%');
    expect(style.marginLeft).toBe('auto');
    expect(style.marginRight).toBe('auto');
  });

  it('shows a caption below the image in a smaller, muted text', async () => {
    await renderEditor(
      '<figure data-width="75" data-align="right"><img src="/api/orgs/stadt/media/a.png" alt="Das Rathaus"><figcaption>Am Markt</figcaption></figure>',
    );
    const box = screen.getByRole('textbox', { name: 'Beschreibung' });
    const figure = box.querySelector('figure') as HTMLElement;
    const caption = within(figure).getByText('Am Markt');
    expect(caption.tagName).toBe('FIGCAPTION');
    expect(figure.firstElementChild?.tagName).toBe('IMG');
    expect(figure.lastElementChild).toBe(caption);
    expect(getComputedStyle(figure).width).toBe('75%');
    expect(getComputedStyle(figure).marginLeft).toBe('auto');
    expect(getComputedStyle(figure).marginRight).toBe('0px');
    expect(getComputedStyle(caption).fontSize).toBe('14px');
  });
});
