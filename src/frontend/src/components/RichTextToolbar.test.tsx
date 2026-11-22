import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import { useEditor, type Editor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import RichTextEditor from './RichTextEditor';
import RichTextToolbar, { type ToolDefinition } from './RichTextToolbar';
import i18n from '../i18n';

// ProseMirror scrolls the cursor into view and asks for rectangles jsdom lacks.
const emptyRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] });
Range.prototype.getClientRects = emptyRects as unknown as Range['getClientRects'];
Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
Element.prototype.getClientRects = emptyRects as unknown as Element['getClientRects'];
document.elementFromPoint = () => null;

// jsdom has no layout. The mock reports the width the test sets, as the
// toolbar's own content width, the moment the toolbar starts to observe.
let reportedWidth = 800;

class MockResizeObserver {
  private readonly callback: ResizeObserverCallback;
  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }
  observe(target: Element) {
    this.callback(
      [{ target, contentRect: { width: reportedWidth } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }
  unobserve() {}
  disconnect() {}
}

const withWidth = (width: number) => {
  reportedWidth = width;
  vi.stubGlobal('ResizeObserver', MockResizeObserver);
};

const renderEditor = (props: Partial<React.ComponentProps<typeof RichTextEditor>> = {}) =>
  render(<RichTextEditor value="<p>Hallo</p>" onChange={vi.fn()} ariaLabel="Beschreibung" orgSlug="stadt" {...props} />);

const toolbarCss = () => {
  const toolbar = screen.getByRole('toolbar');
  return Array.from(document.styleSheets)
    .flatMap((sheet) => Array.from((sheet as CSSStyleSheet).cssRules))
    .map((rule) => rule.cssText)
    .filter((text) => Array.from(toolbar.classList).some((cls) => text.includes(cls)))
    .join('\n');
};

describe('RichTextToolbar', () => {
  afterEach(async () => {
    vi.unstubAllGlobals();
    await i18n.changeLanguage('de');
  });

  describe('the toolbar pattern', () => {
    it('is a horizontal toolbar with its label and keeps the tools in order', async () => {
      renderEditor();
      const toolbar = await screen.findByRole('toolbar', { name: 'Beschreibung: Formatierung' });
      expect(toolbar).toHaveAttribute('aria-orientation', 'horizontal');
      const names = Array.from(toolbar.querySelectorAll('button')).map((b) => b.getAttribute('aria-label'));
      expect(names).toEqual([
        'Rückgängig',
        'Wiederholen',
        'Textart: Normaler Text',
        'Fett',
        'Kursiv',
        'Unterstrichen',
        'Durchgestrichen',
        'Nummerierte Liste',
        'Aufzählung',
        'Zitat',
        'Link einfügen',
        'Bild einfügen',
        'Trennlinie',
      ]);
    });

    it('is one tab stop: only one control has tabindex 0', async () => {
      renderEditor();
      const toolbar = await screen.findByRole('toolbar');
      const buttons = Array.from(toolbar.querySelectorAll('button'));
      expect(buttons.filter((b) => b.tabIndex === 0)).toHaveLength(1);
      // Undo and redo are disabled in a fresh text: the first control that can be used is the tab stop.
      expect(screen.getByRole('button', { name: 'Textart: Normaler Text' })).toHaveAttribute('tabindex', '0');
    });

    it('moves with the arrow keys, Home and End, and wraps around', async () => {
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      const style = screen.getByRole('button', { name: 'Textart: Normaler Text' });
      const bold = screen.getByRole('button', { name: 'Fett' });
      const divider = screen.getByRole('button', { name: 'Trennlinie' });

      await user.tab();
      expect(style).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(bold).toHaveFocus();
      expect(bold).toHaveAttribute('tabindex', '0');
      expect(style).toHaveAttribute('tabindex', '-1');
      await user.keyboard('{ArrowLeft}');
      expect(style).toHaveFocus();
      await user.keyboard('{ArrowLeft}');
      expect(divider).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(style).toHaveFocus();
      await user.keyboard('{End}');
      expect(divider).toHaveFocus();
      await user.keyboard('{Home}');
      expect(style).toHaveFocus();
    });

    it('leaves the toolbar into the text with Tab and returns to the last control with Shift+Tab', async () => {
      const user = userEvent.setup();
      renderEditor();
      const box = await screen.findByRole('textbox', { name: 'Beschreibung' });
      await user.tab();
      await user.keyboard('{ArrowRight}{ArrowRight}{ArrowRight}');
      expect(screen.getByRole('button', { name: 'Unterstrichen' })).toHaveFocus();
      await user.tab();
      expect(box).toHaveFocus();
      await user.tab({ shift: true });
      expect(screen.getByRole('button', { name: 'Unterstrichen' })).toHaveFocus();
    });

    it('skips a disabled control with the arrows', async () => {
      const user = userEvent.setup();
      const tool = (id: string, disabled = false): ToolDefinition => ({
        id,
        group: 0,
        label: () => id,
        icon: () => null,
        isDisabled: () => disabled,
        run: vi.fn(),
      });
      const Harness = () => {
        const editor = useEditor({ extensions: [StarterKit], content: '<p>x</p>' });
        if (!editor) return null;
        return (
          <RichTextToolbar
            editor={editor}
            disabled={false}
            allowImages={false}
            onOpenLink={vi.fn()}
            onOpenImage={vi.fn()}
            tools={[tool('a'), tool('b', true), tool('c'), tool('d', true)]}
          />
        );
      };
      render(<Harness />);
      await screen.findByRole('toolbar');
      const a = screen.getByRole('button', { name: 'a' });
      const c = screen.getByRole('button', { name: 'c' });
      await user.tab();
      expect(a).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(c).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(a).toHaveFocus();
      await user.keyboard('{End}');
      expect(c).toHaveFocus();
    });

    it('has no tab stop and no arrow target while the editor is disabled', async () => {
      const user = userEvent.setup();
      renderEditor({ disabled: true });
      const toolbar = await screen.findByRole('toolbar');
      expect(Array.from(toolbar.querySelectorAll('button')).every((b) => b.disabled)).toBe(true);
      await user.tab();
      expect(toolbar.contains(document.activeElement)).toBe(false);
    });

    it('still applies a tool and keeps the pressed state as before', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderEditor({ onChange });
      await screen.findByRole('toolbar');
      const bullets = screen.getByRole('button', { name: 'Aufzählung' });
      expect(bullets).toHaveAttribute('aria-pressed', 'false');
      await user.click(bullets);
      await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.stringContaining('<ul>')));
      await waitFor(() => expect(bullets).toHaveAttribute('aria-pressed', 'true'));
    });

    it('has no accessibility violations', async () => {
      const { container } = renderEditor();
      await screen.findByRole('toolbar');
      expect(await axe(container)).toHaveNoViolations();
    });
  });

  describe('undo and redo', () => {
    it('are disabled while there is nothing to undo or redo, and the arrow keys skip them', async () => {
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      expect(screen.getByRole('button', { name: 'Rückgängig' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Wiederholen' })).toBeDisabled();
      await user.tab();
      expect(screen.getByRole('button', { name: 'Textart: Normaler Text' })).toHaveFocus();
      await user.keyboard('{ArrowLeft}');
      expect(screen.getByRole('button', { name: 'Trennlinie' })).toHaveFocus();
      await user.keyboard('{Home}');
      expect(screen.getByRole('button', { name: 'Textart: Normaler Text' })).toHaveFocus();
    });

    it('undo takes the last change back and redo brings it again', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderEditor({ onChange });
      await screen.findByRole('toolbar');
      await user.click(screen.getByRole('button', { name: 'Aufzählung' }));
      const undo = screen.getByRole('button', { name: 'Rückgängig' });
      await waitFor(() => expect(undo).toBeEnabled());
      expect(screen.getByRole('button', { name: 'Wiederholen' })).toBeDisabled();

      await user.click(undo);
      await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.stringMatching(/^<p>Hallo<\/p>/)));
      await waitFor(() => expect(undo).toBeDisabled());
      const redo = screen.getByRole('button', { name: 'Wiederholen' });
      expect(redo).toBeEnabled();

      await user.click(redo);
      await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.stringContaining('<ul>')));
      await waitFor(() => expect(redo).toBeDisabled());
    });
  });

  describe('the text style menu', () => {
    it('shows the style at the cursor as its visible text and opens three choices', async () => {
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      const control = screen.getByRole('button', { name: 'Textart: Normaler Text' });
      expect(control).toHaveTextContent('Normaler Text');
      expect(control).toHaveAttribute('aria-haspopup', 'menu');

      await user.click(control);
      const menu = await screen.findByRole('menu', { name: 'Textart' });
      const choices = within(menu).getAllByRole('menuitemradio');
      expect(choices.map((c) => c.textContent)).toEqual(['Normaler Text', 'Überschrift', 'Zwischenüberschrift']);
      expect(choices.map((c) => c.getAttribute('aria-checked'))).toEqual(['true', 'false', 'false']);
    });

    it('sets a heading and a subheading, and the control follows the cursor', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderEditor({ onChange });
      await screen.findByRole('toolbar');
      await user.click(screen.getByRole('button', { name: /^Textart/ }));
      await user.click(await screen.findByRole('menuitemradio', { name: 'Überschrift' }));
      await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.stringMatching(/^<h2>Hallo<\/h2>/)));
      await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
      expect(screen.getByRole('button', { name: 'Textart: Überschrift' })).toHaveTextContent('Überschrift');

      await user.click(screen.getByRole('button', { name: /^Textart/ }));
      expect(await screen.findByRole('menuitemradio', { name: 'Überschrift' })).toHaveAttribute(
        'aria-checked',
        'true',
      );
      await user.click(screen.getByRole('menuitemradio', { name: 'Zwischenüberschrift' }));
      await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.stringMatching(/^<h3>Hallo<\/h3>/)));
      expect(await screen.findByRole('button', { name: 'Textart: Zwischenüberschrift' })).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: /^Textart/ }));
      await user.click(await screen.findByRole('menuitemradio', { name: 'Normaler Text' }));
      await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.stringMatching(/^<p>Hallo<\/p>/)));
    });

    it('shows a heading of the document at the cursor', async () => {
      renderEditor({ value: '<h3>Titel</h3><p>Text</p>' });
      await screen.findByRole('toolbar');
      expect(screen.getByRole('button', { name: 'Textart: Zwischenüberschrift' })).toBeInTheDocument();
    });

    it('closes with Escape and gives the focus back to the control', async () => {
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      const control = screen.getByRole('button', { name: /^Textart/ });
      await user.click(control);
      await screen.findByRole('menu');
      await user.keyboard('{Escape}');
      await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
      expect(control).toHaveFocus();
    });

    it('is named in English', async () => {
      await i18n.changeLanguage('en');
      renderEditor();
      expect(await screen.findByRole('button', { name: 'Text style: Normal text' })).toBeInTheDocument();
    });

    it('has no accessibility violations with the menu open', async () => {
      const user = userEvent.setup();
      const { baseElement } = renderEditor();
      await screen.findByRole('toolbar');
      await user.click(screen.getByRole('button', { name: /^Textart/ }));
      await screen.findByRole('menu');
      const results = await axe(baseElement, {
        rules: { region: { enabled: false }, 'aria-hidden-focus': { enabled: false } },
      });
      expect(results).toHaveNoViolations();
    });
  });

  describe('strike-through, quote and divider', () => {
    it('shows strike-through and quote with aria-pressed and sets them', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderEditor({ onChange });
      await screen.findByRole('toolbar');
      const strike = screen.getByRole('button', { name: 'Durchgestrichen' });
      const quote = screen.getByRole('button', { name: 'Zitat' });
      expect(strike).toHaveAttribute('aria-pressed', 'false');
      expect(quote).toHaveAttribute('aria-pressed', 'false');

      await user.click(quote);
      await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.stringMatching(/^<blockquote><p>Hallo<\/p><\/blockquote>/)));
      await waitFor(() => expect(quote).toHaveAttribute('aria-pressed', 'true'));

      await user.click(strike);
      await waitFor(() => expect(strike).toHaveAttribute('aria-pressed', 'true'));
    });

    it('strikes the selected text through', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      const { container } = renderEditor({ onChange });
      await screen.findByRole('toolbar');
      const element = container.querySelector('.ProseMirror') as HTMLElement & { editor: Editor };
      element.editor.commands.selectAll();
      await user.click(screen.getByRole('button', { name: 'Durchgestrichen' }));
      await waitFor(() => expect(onChange).toHaveBeenLastCalledWith('<p><s>Hallo</s></p>'));
    });

    it('inserts a divider', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderEditor({ onChange });
      await screen.findByRole('toolbar');
      const divider = screen.getByRole('button', { name: 'Trennlinie' });
      expect(divider).not.toHaveAttribute('aria-pressed');
      await user.click(divider);
      await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.stringContaining('<hr>')));
    });

    it('names the new tools in English', async () => {
      await i18n.changeLanguage('en');
      renderEditor();
      await screen.findByRole('toolbar');
      for (const name of ['Undo', 'Redo', 'Strikethrough', 'Quote', 'Divider']) {
        expect(screen.getByRole('button', { name })).toBeInTheDocument();
      }
    });
  });

  describe('a narrow toolbar', () => {
    it('shows all tools and no more menu when they fit', async () => {
      withWidth(800);
      renderEditor();
      await screen.findByRole('toolbar');
      expect(screen.queryByRole('button', { name: 'Weitere Werkzeuge' })).not.toBeInTheDocument();
      expect(screen.getAllByRole('button', { name: /./ })).toHaveLength(13);
    });

    it('moves the tools that do not fit into a more menu with the same labels, from the end of the list', async () => {
      withWidth(300);
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      const more = await screen.findByRole('button', { name: 'Weitere Werkzeuge' });
      expect(more).toHaveAttribute('aria-haspopup', 'menu');
      expect(screen.getByRole('button', { name: 'Fett' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Bild einfügen' })).not.toBeInTheDocument();

      await user.click(more);
      const menu = await screen.findByRole('menu', { name: 'Weitere Werkzeuge' });
      const items = Array.from(menu.querySelectorAll('[role^="menuitem"]')).map((i) => i.textContent);
      expect(items).toEqual(['Zitat', 'Link einfügen', 'Bild einfügen', 'Trennlinie']);
      expect(screen.getByRole('menuitemcheckbox', { name: 'Zitat' })).toHaveAttribute('aria-checked', 'false');
    });

    it('runs a tool from the menu, shows its state and closes the menu', async () => {
      withWidth(200);
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderEditor({ onChange });
      await screen.findByRole('toolbar');
      await user.click(await screen.findByRole('button', { name: 'Weitere Werkzeuge' }));
      await user.click(await screen.findByRole('menuitemcheckbox', { name: 'Aufzählung' }));
      await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
      await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.stringContaining('<ul>')));

      await user.click(screen.getByRole('button', { name: 'Weitere Werkzeuge' }));
      expect(await screen.findByRole('menuitemcheckbox', { name: 'Aufzählung' })).toHaveAttribute(
        'aria-checked',
        'true',
      );
    });

    it('puts the text style choices in the more menu when even they do not fit, as radio items', async () => {
      withWidth(100);
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      expect(screen.queryByRole('button', { name: /^Textart/ })).not.toBeInTheDocument();
      await user.click(await screen.findByRole('button', { name: 'Weitere Werkzeuge' }));
      const menu = await screen.findByRole('menu', { name: 'Weitere Werkzeuge' });
      expect(within(menu).getByRole('menuitemradio', { name: 'Normaler Text' })).toHaveAttribute('aria-checked', 'true');
      await user.click(within(menu).getByRole('menuitemradio', { name: 'Zwischenüberschrift' }));
      await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
      await user.click(screen.getByRole('button', { name: 'Weitere Werkzeuge' }));
      expect(await screen.findByRole('menuitemradio', { name: 'Zwischenüberschrift' })).toHaveAttribute(
        'aria-checked',
        'true',
      );
    });

    it('runs the plain action of the menu: the divider', async () => {
      withWidth(300);
      const user = userEvent.setup();
      const onChange = vi.fn();
      renderEditor({ onChange });
      await screen.findByRole('toolbar');
      await user.click(await screen.findByRole('button', { name: 'Weitere Werkzeuge' }));
      await user.click(await screen.findByRole('menuitem', { name: 'Trennlinie' }));
      await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(expect.stringContaining('<hr>')));
    });

    it('opens the image dialog from the menu', async () => {
      withWidth(200);
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      await user.click(await screen.findByRole('button', { name: 'Weitere Werkzeuge' }));
      await user.click(await screen.findByRole('menuitemcheckbox', { name: 'Bild einfügen' }));
      expect(await screen.findByRole('dialog', { name: 'Bild einfügen' })).toBeInTheDocument();
    });

    it('reacts to the toolbar width and gives the tools back when it grows', async () => {
      const callbacks: ResizeObserverCallback[] = [];
      class Capturing extends MockResizeObserver {
        constructor(cb: ResizeObserverCallback) {
          super(cb);
          callbacks.push(cb);
        }
      }
      reportedWidth = 800;
      vi.stubGlobal('ResizeObserver', Capturing);
      renderEditor();
      await screen.findByRole('toolbar');
      expect(screen.queryByRole('button', { name: 'Weitere Werkzeuge' })).not.toBeInTheDocument();
      act(() => {
        callbacks.forEach((cb) =>
          cb([{ contentRect: { width: 100 } } as unknown as ResizeObserverEntry], {} as ResizeObserver),
        );
      });
      expect(screen.getByRole('button', { name: 'Weitere Werkzeuge' })).toBeInTheDocument();
      act(() => {
        callbacks.forEach((cb) =>
          cb([{ contentRect: { width: 800 } } as unknown as ResizeObserverEntry], {} as ResizeObserver),
        );
      });
      expect(screen.queryByRole('button', { name: 'Weitere Werkzeuge' })).not.toBeInTheDocument();
    });

    it('keeps the more control in the arrow order and reaches it with End', async () => {
      withWidth(200);
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      const more = await screen.findByRole('button', { name: 'Weitere Werkzeuge' });
      await user.tab();
      await user.keyboard('{End}');
      expect(more).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(screen.getByRole('button', { name: 'Textart: Normaler Text' })).toHaveFocus();
    });

    it('names the more control in English', async () => {
      await i18n.changeLanguage('en');
      withWidth(100);
      renderEditor();
      expect(await screen.findByRole('button', { name: 'More tools' })).toBeInTheDocument();
    });

    it('has no accessibility violations with the menu open', async () => {
      withWidth(100);
      const user = userEvent.setup();
      const { baseElement } = renderEditor();
      await screen.findByRole('toolbar');
      await user.click(await screen.findByRole('button', { name: 'Weitere Werkzeuge' }));
      await screen.findByRole('menu');
      // The open menu is a modal: MUI hides the page behind it with aria-hidden
      // and keeps the one tab stop of the toolbar in it (aria-hidden-focus), and the
      // portal stands outside the page's landmarks (region). Both are the modal's
      // own pattern, not the toolbar's.
      const results = await axe(baseElement, {
        rules: { region: { enabled: false }, 'aria-hidden-focus': { enabled: false } },
      });
      expect(results).toHaveNoViolations();
    });
  });

  describe('the look', () => {
    it('stays in view below the fixed app header: sticky, header height as top, opaque, above the text', async () => {
      renderEditor();
      const toolbar = await screen.findByRole('toolbar');
      expect(toolbar).toHaveStyle({ position: 'sticky' });
      const css = toolbarCss();
      // 64 px below the `sm` breakpoint, 72 px from it (AppShell.tsx).
      expect(css).toMatch(/top:\s*64px/);
      expect(css).toMatch(/@media[^{]*\{[^{}]*\.[^{]*\{[^}]*top:\s*72px/);
      expect(css).toMatch(/z-index:\s*[1-9]/);
      expect(css).toMatch(/background-color:\s*(?!transparent|rgba)/);
      // sticky needs a frame that does not scroll: `overflow: clip` keeps the corners.
      const frame = toolbar.parentElement!;
      expect(frame).toHaveStyle({ overflow: 'clip' });
    });

    it('gives every control a touch target of 40 px on a coarse pointer, and keeps the mouse size', async () => {
      withWidth(100);
      renderEditor();
      const toolbar = await screen.findByRole('toolbar');
      const css = toolbarCss();
      const coarse = css.match(/@media \(pointer:\s*coarse\)\s*\{[\s\S]*/)?.[0] ?? '';
      expect(coarse).toMatch(/width:\s*40px/);
      expect(coarse).toMatch(/height:\s*40px/);
      // The mouse rule sets no fixed size outside the coarse query.
      const outside = css.replace(/@media \(pointer:\s*coarse\)\s*\{[\s\S]*/, '');
      expect(outside).not.toMatch(/\bheight:\s*40px/);
      expect(toolbar.querySelector('button')).not.toBeNull();
    });
  });
});
