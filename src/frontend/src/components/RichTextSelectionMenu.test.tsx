import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Editor } from '@tiptap/react';

// The tests set the selection through the editor's commands: a drag over text
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

// The test reads the options the menu hands to the bubble menu.
let bubbleOptions: Record<string, unknown> | undefined;
vi.mock('@tiptap/react/menus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tiptap/react/menus')>();
  const { createElement } = await import('react');
  return {
    ...actual,
    BubbleMenu: (props: React.ComponentProps<typeof actual.BubbleMenu>) => {
      bubbleOptions = props.options as Record<string, unknown> | undefined;
      return createElement(actual.BubbleMenu, props);
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

const MENU = { name: 'Auswahl formatieren' };
const TEXT = '<p>Hallo Welt und mehr</p>';
const IMAGE = '<img src="/api/orgs/stadt/media/a.png" alt="Das Rathaus">';
const TABLE = '<table><tbody><tr><td><p>Zelle eins</p></td><td><p>Zelle zwei</p></td></tr></tbody></table>';

const current = (): Editor => {
  if (!editorUnderTest) throw new Error('no editor yet');
  return editorUnderTest;
};

const renderEditor = async (value = TEXT, props: { disabled?: boolean } = {}) => {
  const onChange = vi.fn();
  render(<RichTextEditor value={value} onChange={onChange} ariaLabel="Beschreibung" orgSlug="stadt" {...props} />);
  await screen.findByRole('textbox', { name: 'Beschreibung' });
  return { onChange, user: userEvent.setup() };
};

const lastHtml = (onChange: ReturnType<typeof vi.fn>): string => onChange.mock.calls.at(-1)?.[0] as string;

/** Selects the first occurrence of `text` and puts the focus in the editor. */
const select = (text: string) => {
  const editor = current();
  let from = -1;
  editor.state.doc.descendants((node, pos) => {
    if (from < 0 && node.isText) {
      const index = (node.text ?? '').indexOf(text);
      if (index >= 0) from = pos + index;
    }
  });
  expect(from).toBeGreaterThanOrEqual(0);
  act(() => {
    editor.commands.setTextSelection({ from, to: from + text.length });
    editor.view.focus();
  });
};

const menuIn = async () => screen.findByRole('toolbar', MENU);

afterEach(() => {
  editorUnderTest = null;
  bubbleOptions = undefined;
  vi.restoreAllMocks();
  window.matchMedia = undefined as unknown as typeof window.matchMedia;
});

describe('the selection menu', () => {
  it('is not shown for an empty selection', async () => {
    await renderEditor();
    act(() => {
      current().commands.setTextSelection(3);
      current().view.focus();
    });
    expect(screen.queryByRole('toolbar', MENU)).not.toBeInTheDocument();
  });

  it('shows bold, italic, underline, strike-through and link for selected text, all not pressed', async () => {
    await renderEditor();
    select('Welt');
    const menu = await menuIn();
    const buttons = within(menu).getAllByRole('button');
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([
      'Fett',
      'Kursiv',
      'Unterstrichen',
      'Durchgestrichen',
      'Link einfügen',
    ]);
    expect(buttons.map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'false', 'false', 'false']);
  });

  it('applies a mark and shows its state, and the focus stays in the editor', async () => {
    const { onChange, user } = await renderEditor();
    select('Welt');
    const menu = await menuIn();
    await user.click(within(menu).getByRole('button', { name: 'Fett' }));
    expect(lastHtml(onChange)).toContain('<strong>Welt</strong>');
    expect(within(menu).getByRole('button', { name: 'Fett' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('textbox', { name: 'Beschreibung' })).toHaveFocus();
    await user.click(within(menu).getByRole('button', { name: 'Fett' }));
    expect(lastHtml(onChange)).not.toContain('<strong>');
    await user.click(within(menu).getByRole('button', { name: 'Durchgestrichen' }));
    expect(lastHtml(onChange)).toContain('<s>Welt</s>');
  });

  it('does not take the focus on a press', async () => {
    await renderEditor();
    select('Welt');
    const menu = await menuIn();
    const pressed = fireEvent.mouseDown(within(menu).getByRole('button', { name: 'Kursiv' }));
    expect(pressed).toBe(false);
    for (const button of within(menu).getAllByRole('button')) expect(button.tabIndex).toBe(-1);
  });

  it('opens the link dialog and links the selection', async () => {
    const { onChange, user } = await renderEditor();
    select('Welt');
    const menu = await menuIn();
    await user.click(within(menu).getByRole('button', { name: 'Link einfügen' }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByRole('textbox'), 'https://www.example.org');
    await user.click(within(dialog).getByRole('button', { name: /einfügen/i }));
    expect(lastHtml(onChange)).toContain('href="https://www.example.org"');
  });

  it('removes the link when the selection is inside one', async () => {
    const { onChange, user } = await renderEditor('<p>Hallo <a href="https://www.example.org">Welt</a> und mehr</p>');
    select('Welt');
    const menu = await menuIn();
    const button = within(menu).getByRole('button', { name: 'Link entfernen' });
    expect(button).toHaveAttribute('aria-pressed', 'true');
    await user.click(button);
    expect(lastHtml(onChange)).not.toContain('<a ');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('is not shown for a selected image', async () => {
    await renderEditor(`<p>Vorher</p>${IMAGE}<p>Nachher</p>`);
    let position = -1;
    current().state.doc.descendants((node, pos) => {
      if (node.type.name === 'image') position = pos;
    });
    act(() => {
      current().commands.setNodeSelection(position);
      current().view.focus();
    });
    await screen.findByRole('toolbar', { name: 'Bild' });
    expect(screen.queryByRole('toolbar', MENU)).not.toBeInTheDocument();
  });

  it('is not shown for a selection in a table, where the table menu shows', async () => {
    await renderEditor(`<p>Vorher</p>${TABLE}<p>Nachher</p>`);
    select('eins');
    await screen.findByRole('toolbar', { name: 'Tabelle' });
    expect(screen.queryByRole('toolbar', MENU)).not.toBeInTheDocument();
    select('Vorher');
    expect(await menuIn()).toBeInTheDocument();
    expect(screen.queryByRole('toolbar', { name: 'Tabelle' })).not.toBeInTheDocument();
  });

  it('is not shown while the editor is disabled', async () => {
    await renderEditor(TEXT, { disabled: true });
    select('Welt');
    expect(screen.queryByRole('toolbar', MENU)).not.toBeInTheDocument();
  });

  it('hides with Escape and keeps the selection, and shows again for a new selection', async () => {
    const { user } = await renderEditor();
    select('Welt');
    await menuIn();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('toolbar', MENU)).not.toBeInTheDocument();
    const { from, to } = current().state.selection;
    expect(to - from).toBe(4);
    expect(screen.getByRole('textbox', { name: 'Beschreibung' })).toHaveFocus();
    select('Hallo');
    expect(await menuIn()).toBeInTheDocument();
  });

  it('sits above the selection with a mouse', async () => {
    await renderEditor();
    select('Welt');
    const above = await menuIn();
    expect(parseFloat(above.parentElement?.style.top ?? 'NaN')).toBeLessThan(0);
  });

  it('sits below the selection on a touch screen', async () => {
    window.matchMedia = ((query: string) => ({
      matches: query.includes('pointer: coarse'),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      onchange: null,
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia;
    await renderEditor();
    select('Welt');
    const below = await menuIn();
    expect(parseFloat(below.parentElement?.style.top ?? 'NaN')).toBeGreaterThan(0);
  });
  describe('placement next to the editor\'s toolbar', () => {
    const touch = () => {
      window.matchMedia = ((query: string) => ({
        matches: query.includes('pointer: coarse'),
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        onchange: null,
        dispatchEvent: () => false,
      })) as unknown as typeof window.matchMedia;
    };
    const editorToolbar = () => screen.getByRole('toolbar', { name: /^Beschreibung/ });
    const flipOf = () => {
      const flip = bubbleOptions?.flip as (state: unknown) => {
        boundary: unknown[];
        rootBoundary: string;
        padding: { top: number };
      };
      expect(flip).toBeTypeOf('function');
      return flip({});
    };

    it('keeps the menu above the selection and clear of the toolbar on a fine pointer', async () => {
      await renderEditor();
      expect(bubbleOptions).toMatchObject({ placement: 'top', offset: 8 });
      vi.spyOn(editorToolbar(), 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 72, 600, 48));
      expect(flipOf()).toEqual({ boundary: [], rootBoundary: 'viewport', padding: { top: 120 } });
    });

    it('reads the toolbar edge anew each time, so a scroll moves the padding', async () => {
      await renderEditor();
      const rect = vi.spyOn(editorToolbar(), 'getBoundingClientRect');
      rect.mockReturnValue(new DOMRect(0, 72, 600, 48));
      expect(flipOf().padding.top).toBe(120);
      rect.mockReturnValue(new DOMRect(0, 10, 600, 48));
      expect(flipOf().padding.top).toBe(58);
    });

    it('does not take the selection menu itself for the editor\'s toolbar', async () => {
      await renderEditor();
      select('Welt');
      const menu = await menuIn();
      vi.spyOn(editorToolbar(), 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 72, 600, 48));
      vi.spyOn(menu, 'getBoundingClientRect').mockReturnValue(new DOMRect(0, 500, 200, 40));
      expect(flipOf().padding.top).toBe(120);
    });

    it('keeps the menu below the selection on a touch screen', async () => {
      touch();
      await renderEditor();
      expect(bubbleOptions).toMatchObject({ placement: 'bottom', offset: 8 });
      expect(bubbleOptions?.flip).toBeUndefined();
    });
  });
});
