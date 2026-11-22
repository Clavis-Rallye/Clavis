import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import { useEditor } from '@tiptap/react';
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
let reportedWidth = 600;

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
    it('is a horizontal toolbar with its label and keeps the eight tools in order', async () => {
      renderEditor();
      const toolbar = await screen.findByRole('toolbar', { name: 'Beschreibung: Formatierung' });
      expect(toolbar).toHaveAttribute('aria-orientation', 'horizontal');
      const names = Array.from(toolbar.querySelectorAll('button')).map((b) => b.getAttribute('aria-label'));
      expect(names).toEqual([
        'Fett',
        'Kursiv',
        'Unterstrichen',
        'Überschrift',
        'Nummerierte Liste',
        'Aufzählung',
        'Link einfügen',
        'Bild einfügen',
      ]);
    });

    it('is one tab stop: only one control has tabindex 0', async () => {
      renderEditor();
      const toolbar = await screen.findByRole('toolbar');
      const buttons = Array.from(toolbar.querySelectorAll('button'));
      expect(buttons.filter((b) => b.tabIndex === 0)).toHaveLength(1);
      expect(buttons[0]).toHaveAttribute('tabindex', '0');
      expect(buttons.slice(1).every((b) => b.getAttribute('tabindex') === '-1')).toBe(true);
    });

    it('moves with the arrow keys, Home and End, and wraps around', async () => {
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      const bold = screen.getByRole('button', { name: 'Fett' });
      const italic = screen.getByRole('button', { name: 'Kursiv' });
      const image = screen.getByRole('button', { name: 'Bild einfügen' });

      await user.tab();
      expect(bold).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(italic).toHaveFocus();
      expect(italic).toHaveAttribute('tabindex', '0');
      expect(bold).toHaveAttribute('tabindex', '-1');
      await user.keyboard('{ArrowLeft}');
      expect(bold).toHaveFocus();
      await user.keyboard('{ArrowLeft}');
      expect(image).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(bold).toHaveFocus();
      await user.keyboard('{End}');
      expect(image).toHaveFocus();
      await user.keyboard('{Home}');
      expect(bold).toHaveFocus();
    });

    it('leaves the toolbar into the text with Tab and returns to the last control with Shift+Tab', async () => {
      const user = userEvent.setup();
      renderEditor();
      const box = await screen.findByRole('textbox', { name: 'Beschreibung' });
      await user.tab();
      await user.keyboard('{ArrowRight}{ArrowRight}');
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

  describe('a narrow toolbar', () => {
    it('shows all tools and no more menu when they fit', async () => {
      withWidth(600);
      renderEditor();
      await screen.findByRole('toolbar');
      expect(screen.queryByRole('button', { name: 'Weitere Werkzeuge' })).not.toBeInTheDocument();
      expect(screen.getAllByRole('button', { name: /./ })).toHaveLength(8);
    });

    it('moves the tools that do not fit into a more menu with the same labels', async () => {
      withWidth(100);
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      const more = await screen.findByRole('button', { name: 'Weitere Werkzeuge' });
      expect(more).toHaveAttribute('aria-haspopup', 'menu');
      expect(screen.getByRole('button', { name: 'Fett' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Bild einfügen' })).not.toBeInTheDocument();

      await user.click(more);
      const menu = await screen.findByRole('menu', { name: 'Weitere Werkzeuge' });
      const items = Array.from(menu.querySelectorAll('[role="menuitemcheckbox"]')).map((i) => i.textContent);
      expect(items).toEqual([
        'Überschrift',
        'Nummerierte Liste',
        'Aufzählung',
        'Link einfügen',
        'Bild einfügen',
      ]);
      expect(screen.getByRole('menuitemcheckbox', { name: 'Aufzählung' })).toHaveAttribute('aria-checked', 'false');
    });

    it('runs a tool from the menu, shows its state and closes the menu', async () => {
      withWidth(100);
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

    it('opens the image dialog from the menu', async () => {
      withWidth(100);
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
      reportedWidth = 600;
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
          cb([{ contentRect: { width: 600 } } as unknown as ResizeObserverEntry], {} as ResizeObserver),
        );
      });
      expect(screen.queryByRole('button', { name: 'Weitere Werkzeuge' })).not.toBeInTheDocument();
    });

    it('keeps the more control in the arrow order and reaches it with End', async () => {
      withWidth(100);
      const user = userEvent.setup();
      renderEditor();
      await screen.findByRole('toolbar');
      const more = await screen.findByRole('button', { name: 'Weitere Werkzeuge' });
      await user.tab();
      await user.keyboard('{End}');
      expect(more).toHaveFocus();
      await user.keyboard('{ArrowRight}');
      expect(screen.getByRole('button', { name: 'Fett' })).toHaveFocus();
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
