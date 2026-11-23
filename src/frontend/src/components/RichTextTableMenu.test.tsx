import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Editor } from '@tiptap/react';

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
import i18n from '../i18n';

// ProseMirror scrolls the cursor into view and asks for rectangles jsdom lacks.
const emptyRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] });
Range.prototype.getClientRects = emptyRects as unknown as Range['getClientRects'];
Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
Element.prototype.getClientRects = emptyRects as unknown as Element['getClientRects'];
document.elementFromPoint = () => null;

const TABLE =
  '<table><tbody><tr><th><p>A</p></th><th><p>B</p></th></tr><tr><td><p>C</p></td><td><p>D</p></td></tr></tbody></table>';
const DOCUMENT = `<p>Vorher</p>${TABLE}<p>Nachher</p>`;
const THREE =
  '<table><tbody><tr><th><p>A</p></th><th><p>B</p></th><th><p>C</p></th></tr><tr><td><p>D</p></td><td><p>E</p></td><td><p>F</p></td></tr></tbody></table>';
const SINGLE = '<table><tbody><tr><td><p>A</p></td></tr></tbody></table>';
const IMAGE = '<img src="/api/orgs/stadt/media/a.png" alt="Das Rathaus">';

const current = (): Editor => {
  if (!editorUnderTest) throw new Error('no editor yet');
  return editorUnderTest;
};

/** Puts the cursor in the cell whose text is `text`. */
const cursorIn = (text: string) => {
  const editor = current();
  let position = -1;
  editor.state.doc.descendants((node, pos) => {
    if (node.isText && node.text === text) position = pos + 1;
  });
  expect(position).toBeGreaterThanOrEqual(0);
  act(() => {
    editor.commands.setTextSelection(position);
    editor.view.focus();
  });
};

const cursorInTable = async (text = 'A', name = 'Tabelle') => {
  cursorIn(text);
  return screen.findByRole('toolbar', { name });
};

const renderEditor = async (value = DOCUMENT) => {
  const onChange = vi.fn();
  render(<RichTextEditor value={value} onChange={onChange} ariaLabel="Beschreibung" orgSlug="stadt" />);
  await screen.findByRole('textbox', { name: 'Beschreibung' });
  return { onChange, user: userEvent.setup() };
};

const lastHtml = (onChange: ReturnType<typeof vi.fn>): string => onChange.mock.calls.at(-1)?.[0] as string;

afterEach(async () => {
  editorUnderTest = null;
  await i18n.changeLanguage('de');
});

describe('the table menu', () => {
  it('is not shown while the cursor is outside a table', async () => {
    await renderEditor();
    expect(screen.queryByRole('toolbar', { name: 'Tabelle' })).not.toBeInTheDocument();
    act(() => {
      current().commands.setTextSelection(3);
    });
    expect(screen.queryByRole('toolbar', { name: 'Tabelle' })).not.toBeInTheDocument();
  });

  it('is not shown for a document that starts with a table until the editor has the focus', async () => {
    await renderEditor(TABLE);
    expect(screen.queryByRole('toolbar', { name: 'Tabelle' })).not.toBeInTheDocument();
    act(() => {
      current().view.focus();
    });
    expect(await screen.findByRole('toolbar', { name: 'Tabelle' })).toBeInTheDocument();
  });

  it('shows the eight controls with the cursor in a table', async () => {
    await renderEditor();
    const menu = await cursorInTable();
    expect(within(menu).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Zeile darüber',
      'Zeile darunter',
      'Spalte links',
      'Spalte rechts',
      'Zeile löschen',
      'Spalte löschen',
      'Kopfzeile',
      'Tabelle löschen',
    ]);
  });

  it('shows the header row as pressed and switches it off and on', async () => {
    const { onChange, user } = await renderEditor();
    const menu = await cursorInTable();
    const header = within(menu).getByRole('button', { name: 'Kopfzeile' });
    expect(header).toHaveAttribute('aria-pressed', 'true');
    await user.click(header);
    expect(lastHtml(onChange)).toBe(
      '<p>Vorher</p><table><tbody><tr><td><p>A</p></td><td><p>B</p></td></tr><tr><td><p>C</p></td><td><p>D</p></td></tr></tbody></table><p>Nachher</p>',
    );
    await waitFor(() => expect(header).toHaveAttribute('aria-pressed', 'false'));
    await user.click(header);
    expect(lastHtml(onChange)).toContain('<th><p>A</p></th><th><p>B</p></th>');
    await waitFor(() => expect(header).toHaveAttribute('aria-pressed', 'true'));
  });

  it.each([
    ['Zeile darüber', '<tr><td><p></p></td><td><p></p></td></tr><tr><th><p>A</p>'],
    ['Zeile darunter', '</tr><tr><td><p></p></td><td><p></p></td></tr><tr><td><p>C</p>'],
    ['Spalte links', '<th><p></p></th><th><p>A</p></th><th><p>B</p></th>'],
    ['Spalte rechts', '<th><p>A</p></th><th><p></p></th><th><p>B</p></th>'],
  ])('adds with %s', async (name, fragment) => {
    const { onChange, user } = await renderEditor();
    const menu = await cursorInTable();
    await user.click(within(menu).getByRole('button', { name }));
    const html = lastHtml(onChange);
    expect(html).toContain(fragment);
    expect(html).not.toMatch(/<(table|tbody|tr|th|td)\s/);
  });

  it('removes the row of the cursor', async () => {
    const { onChange, user } = await renderEditor();
    const menu = await cursorInTable('C');
    await user.click(within(menu).getByRole('button', { name: 'Zeile löschen' }));
    expect(lastHtml(onChange)).toBe(
      '<p>Vorher</p><table><tbody><tr><th><p>A</p></th><th><p>B</p></th></tr></tbody></table><p>Nachher</p>',
    );
  });

  it('removes the column of the cursor', async () => {
    const { onChange, user } = await renderEditor();
    const menu = await cursorInTable('B');
    await user.click(within(menu).getByRole('button', { name: 'Spalte löschen' }));
    expect(lastHtml(onChange)).toBe(
      '<p>Vorher</p><table><tbody><tr><th><p>A</p></th></tr><tr><td><p>C</p></td></tr></tbody></table><p>Nachher</p>',
    );
  });

  it('removes the table and the menu goes away', async () => {
    const { onChange, user } = await renderEditor();
    const menu = await cursorInTable();
    await user.click(within(menu).getByRole('button', { name: 'Tabelle löschen' }));
    expect(lastHtml(onChange)).toBe('<p>Vorher</p><p>Nachher</p>');
    await waitFor(() => expect(screen.queryByRole('toolbar', { name: 'Tabelle' })).not.toBeInTheDocument());
  });

  it('disables remove row and remove column in a table of one cell', async () => {
    await renderEditor(SINGLE);
    const menu = await cursorInTable();
    expect(within(menu).getByRole('button', { name: 'Zeile löschen' })).toBeDisabled();
    expect(within(menu).getByRole('button', { name: 'Spalte löschen' })).toBeDisabled();
    expect(within(menu).getByRole('button', { name: 'Zeile darüber' })).toBeEnabled();
  });

  it('enables the remove buttons again after a row and a column are added', async () => {
    const { user } = await renderEditor(SINGLE);
    const menu = await cursorInTable();
    await user.click(within(menu).getByRole('button', { name: 'Zeile darunter' }));
    await waitFor(() => expect(within(menu).getByRole('button', { name: 'Zeile löschen' })).toBeEnabled());
    expect(within(menu).getByRole('button', { name: 'Spalte löschen' })).toBeDisabled();
    await user.click(within(menu).getByRole('button', { name: 'Spalte rechts' }));
    await waitFor(() => expect(within(menu).getByRole('button', { name: 'Spalte löschen' })).toBeEnabled());
  });

  it('does not take the focus when it shows', async () => {
    await renderEditor();
    screen.getByRole('textbox', { name: 'Beschreibung' }).focus();
    const menu = await cursorInTable();
    expect(menu.contains(document.activeElement)).toBe(false);
  });

  it('never shows with the image menu: an image selected in a cell shows the image menu', async () => {
    await renderEditor(`<table><tbody><tr><td>${IMAGE}</td><td><p>B</p></td></tr></tbody></table>`);
    await cursorInTable('B');
    let position = -1;
    current().state.doc.descendants((node, pos) => {
      if (node.type.name === 'image') position = pos;
    });
    act(() => {
      current().commands.setNodeSelection(position);
    });
    await screen.findByRole('toolbar', { name: 'Bild' });
    await waitFor(() => expect(screen.queryByRole('toolbar', { name: 'Tabelle' })).not.toBeInTheDocument());
    act(() => {
      current().commands.setTextSelection(position + 6);
    });
    await screen.findByRole('toolbar', { name: 'Tabelle' });
    await waitFor(() => expect(screen.queryByRole('toolbar', { name: 'Bild' })).not.toBeInTheDocument());
  });

  describe('the limit of three columns', () => {
    const NOTE = 'Höchstens drei Spalten';

    it('disables column left and column right at three columns and says why', async () => {
      await renderEditor(THREE);
      const menu = await cursorInTable();
      const left = within(menu).getByRole('button', { name: 'Spalte links' });
      const right = within(menu).getByRole('button', { name: 'Spalte rechts' });
      expect(left).toBeDisabled();
      expect(right).toBeDisabled();
      expect(within(menu).getByText(NOTE)).toBeVisible();
      expect(left).toHaveAccessibleDescription(NOTE);
      expect(right).toHaveAccessibleDescription(NOTE);
      expect(within(menu).getByRole('button', { name: 'Zeile darüber' })).not.toHaveAttribute('aria-describedby');
      expect(within(menu).getAllByRole('button')).toHaveLength(8);
    });

    it('gives the disabled buttons no tooltip', async () => {
      await renderEditor(THREE);
      const menu = await cursorInTable();
      expect(within(menu).getByRole('button', { name: 'Spalte links' })).not.toHaveAttribute('title');
      expect(within(menu).getByRole('button', { name: 'Spalte rechts' })).not.toHaveAttribute('title');
    });

    it('shows the note in English with the English locale', async () => {
      await i18n.changeLanguage('en');
      await renderEditor(THREE);
      const menu = await cursorInTable('A', 'Table');
      expect(within(menu).getByText('At most three columns')).toBeVisible();
      expect(within(menu).getByRole('button', { name: 'Column right' })).toHaveAccessibleDescription(
        'At most three columns',
      );
    });

    it('enables both and shows no note in a table of two columns, and a column added gives three', async () => {
      const { onChange, user } = await renderEditor();
      const menu = await cursorInTable();
      const left = within(menu).getByRole('button', { name: 'Spalte links' });
      const right = within(menu).getByRole('button', { name: 'Spalte rechts' });
      expect(left).toBeEnabled();
      expect(right).toBeEnabled();
      expect(within(menu).queryByText(NOTE)).not.toBeInTheDocument();
      expect(left).not.toHaveAttribute('aria-describedby');
      expect(right).not.toHaveAttribute('aria-describedby');
      await user.click(right);
      expect(lastHtml(onChange)).toContain('<th><p>A</p></th><th><p></p></th><th><p>B</p></th>');
      await waitFor(() => expect(within(menu).getByRole('button', { name: 'Spalte rechts' })).toBeDisabled());
      expect(within(menu).getByText(NOTE)).toBeVisible();
    });

    it('enables both again after a column is removed at three columns', async () => {
      const { user } = await renderEditor(THREE);
      const menu = await cursorInTable('C');
      expect(within(menu).getByRole('button', { name: 'Spalte rechts' })).toBeDisabled();
      await user.click(within(menu).getByRole('button', { name: 'Spalte löschen' }));
      await waitFor(() => expect(within(menu).getByRole('button', { name: 'Spalte rechts' })).toBeEnabled());
      expect(within(menu).getByRole('button', { name: 'Spalte links' })).toBeEnabled();
      expect(within(menu).queryByText(NOTE)).not.toBeInTheDocument();
    });

    it('does not add a column at the limit even if the click runs', async () => {
      const { onChange } = await renderEditor(THREE);
      const menu = await cursorInTable();
      const right = within(menu).getByRole('button', { name: 'Spalte rechts' });
      onChange.mockClear();
      act(() => {
        right.click();
      });
      expect(onChange).not.toHaveBeenCalled();
      expect(current().getHTML()).not.toContain('<th><p></p></th>');
    });

    it('starts Alt+F10 on the first enabled item and skips the disabled ones with the arrows', async () => {
      const { user } = await renderEditor(THREE);
      screen.getByRole('textbox', { name: 'Beschreibung' }).focus();
      const menu = await cursorInTable();
      await user.keyboard('{Alt>}{F10}{/Alt}');
      expect(within(menu).getByRole('button', { name: 'Zeile darüber' })).toHaveFocus();
      await user.keyboard('{ArrowRight}{ArrowRight}');
      expect(within(menu).getByRole('button', { name: 'Zeile löschen' })).toHaveFocus();
    });
  });

  describe('keyboard', () => {
    it('moves the focus into the menu with Alt+F10, moves with the arrows, and returns with Escape', async () => {
      const { user } = await renderEditor(SINGLE);
      screen.getByRole('textbox', { name: 'Beschreibung' }).focus();
      const menu = await cursorInTable();
      await user.keyboard('{Alt>}{F10}{/Alt}');
      expect(within(menu).getByRole('button', { name: 'Zeile darüber' })).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(within(menu).getByRole('button', { name: 'Zeile darunter' })).toHaveFocus();
      // The disabled controls are skipped.
      await user.keyboard('{End}');
      expect(within(menu).getByRole('button', { name: 'Tabelle löschen' })).toHaveFocus();
      await user.keyboard('{ArrowLeft}');
      expect(within(menu).getByRole('button', { name: 'Kopfzeile' })).toHaveFocus();
      await user.keyboard('{ArrowLeft}');
      expect(within(menu).getByRole('button', { name: 'Spalte rechts' })).toHaveFocus();
      await user.keyboard('{Home}');
      expect(within(menu).getByRole('button', { name: 'Zeile darüber' })).toHaveFocus();
      await user.keyboard('{Escape}');
      expect(screen.getByRole('textbox', { name: 'Beschreibung' })).toHaveFocus();
      expect(current().isActive('table')).toBe(true);
      expect(screen.getByRole('toolbar', { name: 'Tabelle' })).toBeInTheDocument();
    });

    it('returns to the cell it came from', async () => {
      const { user } = await renderEditor();
      screen.getByRole('textbox', { name: 'Beschreibung' }).focus();
      await cursorInTable('D');
      const before = current().state.selection.from;
      await user.keyboard('{Alt>}{F10}{/Alt}');
      await user.keyboard('{Escape}');
      expect(current().state.selection.from).toBe(before);
    });

    it('has one tab stop', async () => {
      await renderEditor();
      const menu = await cursorInTable();
      expect(within(menu).getAllByRole('button').filter((b) => b.tabIndex === 0)).toHaveLength(1);
    });

    it('does nothing with Alt+F10 outside a table', async () => {
      const { user } = await renderEditor();
      const box = screen.getByRole('textbox', { name: 'Beschreibung' });
      box.focus();
      await user.keyboard('{Alt>}{F10}{/Alt}');
      expect(box).toHaveFocus();
    });
  });
});
