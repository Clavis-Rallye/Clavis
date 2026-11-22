import { lazy, StrictMode, Suspense } from 'react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import RichTextEditor from './RichTextEditor';
import { AuthContext } from '../auth/AuthContext';
import i18n from '../i18n';
import apiClient, { ApiError } from '../api/client';

// ProseMirror scrolls the cursor into view and asks for rectangles jsdom lacks.
const emptyRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] });
Range.prototype.getClientRects = emptyRects as unknown as Range['getClientRects'];
Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
Element.prototype.getClientRects = emptyRects as unknown as Element['getClientRects'];
document.elementFromPoint = () => null;

const LazyRichTextEditor = lazy(() => Promise.resolve({ default: RichTextEditor }));

const orgUser = {
  id: 'u0',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'stadt',
};

const withOrgAuth = (children: ReactNode) => (
  <AuthContext.Provider
    value={{
      user: orgUser,
      isAuthenticated: true,
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
      checkAuth: vi.fn(),
      needsTwoFactorSetup: () => false,
    }}
  >
    {children}
  </AuthContext.Provider>
);

describe('RichTextEditor', () => {
  it('renders the toolbar with the image tool when an organisation is known', async () => {
    render(<RichTextEditor value="" onChange={vi.fn()} ariaLabel="Beschreibung" orgSlug="stadt" />);
    expect(await screen.findByRole('textbox', { name: 'Beschreibung' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Fett' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Bild einfügen' })).toBeInTheDocument();
  });

  it('hides the image tool without an organisation', async () => {
    render(<RichTextEditor value="" onChange={vi.fn()} ariaLabel="Beschreibung" />);
    await screen.findByRole('textbox', { name: 'Beschreibung' });
    expect(screen.queryByRole('button', { name: 'Bild einfügen' })).not.toBeInTheDocument();
  });

  it('hides the image tool when allowImages is false, even with an organisation session open', async () => {
    render(
      withOrgAuth(
        <RichTextEditor value="" onChange={vi.fn()} ariaLabel="Beschreibung" allowImages={false} />,
      ),
    );
    await screen.findByRole('textbox', { name: 'Beschreibung' });
    expect(screen.queryByRole('button', { name: 'Bild einfügen' })).not.toBeInTheDocument();
  });

  it('converts legacy plain text into paragraphs on load', async () => {
    render(
      <RichTextEditor value={'Erste Zeile\nZweite Zeile\n\nAbsatz zwei'} onChange={vi.fn()} ariaLabel="Beschreibung" />,
    );
    const box = await screen.findByRole('textbox', { name: 'Beschreibung' });
    await waitFor(() => expect(box.querySelectorAll('p')).toHaveLength(2));
    expect(box.querySelector('br')).not.toBeNull();
    expect(box.textContent).toContain('Absatz zwei');
  });

  it('opens the image dialog and refuses to insert without alt text', async () => {
    const user = userEvent.setup();
    render(<RichTextEditor value="" onChange={vi.fn()} ariaLabel="Beschreibung" orgSlug="stadt" />);
    await user.click(await screen.findByRole('button', { name: 'Bild einfügen' }));
    const dialog = await screen.findByRole('dialog', { name: 'Bild einfügen' });
    expect(dialog).toBeInTheDocument();
    const insert = screen.getByRole('button', { name: 'Einfügen' });
    expect(insert).toBeDisabled();
    const input = screen.getByTestId('rich-text-image-input') as HTMLInputElement;
    await user.upload(input, new File([new Uint8Array([137, 80, 78, 71])], 'rathaus.png', { type: 'image/png' }));
    expect(insert).toBeEnabled();
    await user.click(insert);
    expect(await screen.findByText('Bitte beschreibe das Bild in einem Satz.')).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Bild einfügen' })).toBeInTheDocument();
  });

  // A creator reads why the server refused an image in a
  // text. The reason comes from the error code, not from the server's text.
  describe('a refused image upload', () => {
    afterEach(async () => {
      vi.restoreAllMocks();
      await i18n.changeLanguage('de');
    });

    const uploadRefused = async (error: ApiError, labels: { image: string; alt: RegExp; insert: string }) => {
      const post = vi.spyOn(apiClient, 'post').mockRejectedValue(error);
      const user = userEvent.setup();
      render(<RichTextEditor value="" onChange={vi.fn()} ariaLabel="Beschreibung" orgSlug="stadt" />);
      await user.click(await screen.findByRole('button', { name: labels.image }));
      await screen.findByRole('dialog', { name: labels.image });
      await user.upload(
        screen.getByTestId('rich-text-image-input'),
        new File(['no picture'], 'rathaus.png', { type: 'image/png' }),
      );
      await user.type(screen.getByRole('textbox', { name: labels.alt }), 'Rathaus');
      await user.click(screen.getByRole('button', { name: labels.insert }));
      await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
      return user;
    };

    const de = { image: 'Bild einfügen', alt: /Alternativtext/, insert: 'Einfügen' };

    it('says in German that the file is no readable picture, outside the closed dialog', async () => {
      const user = await uploadRefused(
        new ApiError(415, 'The uploaded file claims to be an image but cannot be decoded as one', 'IMAGE_NOT_DECODABLE'),
        de,
      );
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Diese Datei ist kein lesbares Bild. Bitte wähle ein anderes Bild.',
      );
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(screen.queryByText(/cannot be decoded/)).not.toBeInTheDocument();

      // The next try starts clean: no reason left, no file of the last try.
      await user.click(screen.getByRole('button', { name: 'Bild einfügen' }));
      await screen.findByRole('dialog', { name: 'Bild einfügen' });
      expect(screen.queryByText(/kein lesbares Bild/)).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Einfügen' })).toBeDisabled();
    });

    it('says in English that the file is no readable picture', async () => {
      await i18n.changeLanguage('en');
      const image = i18n.t('common:richTextEditor.image');
      await uploadRefused(new ApiError(415, 'cannot be decoded', 'IMAGE_NOT_DECODABLE'), {
        image,
        alt: /alt text/i,
        insert: i18n.t('common:richTextEditor.imageDialog.insert'),
      });
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'This file is not a readable picture. Please choose another image.',
      );
    });

    it('says that the image is too large', async () => {
      await uploadRefused(new ApiError(413, 'Image exceeds the decode size limit', 'IMAGE_TOO_LARGE'), de);
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Das Bild ist zu groß. Bitte wähle ein Bild mit geringerer Auflösung.',
      );
      expect(screen.queryByText(/decode size limit/)).not.toBeInTheDocument();
    });

    it('leaves a failure without a known code to the dialog, which stays open', async () => {
      await uploadRefused(new ApiError(500, 'Internal Server Error'), de);
      expect(
        await screen.findByText('Das Bild konnte nicht hochgeladen werden. Bitte versuche es erneut.'),
      ).toBeInTheDocument();
      expect(screen.getByRole('dialog', { name: 'Bild einfügen' })).toBeInTheDocument();
    });
  });

  it('survives effect replay under a Suspense boundary with a lazy wrapper in StrictMode', async () => {
    const onChange = vi.fn();
    const { rerender, unmount } = render(
      <StrictMode>
        <Suspense fallback={null}>
          <LazyRichTextEditor value="<p>Hallo</p>" onChange={onChange} ariaLabel="Beschreibung" />
        </Suspense>
      </StrictMode>,
    );

    const box = await screen.findByRole('textbox', { name: 'Beschreibung' });
    expect(box.textContent).toContain('Hallo');

    rerender(
      <StrictMode>
        <Suspense fallback={null}>
          <LazyRichTextEditor value="<p>Neu</p>" onChange={onChange} ariaLabel="Beschreibung" />
        </Suspense>
      </StrictMode>,
    );

    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Beschreibung' }).textContent).toContain('Neu'),
    );

    expect(() => unmount()).not.toThrow();
  });

  it('has no accessibility violations', async () => {
    const { container } = render(
      <RichTextEditor value="<p>Hallo</p>" onChange={vi.fn()} ariaLabel="Beschreibung" orgSlug="stadt" />,
    );
    await screen.findByRole('textbox', { name: 'Beschreibung' });
    expect(await axe(container)).toHaveNoViolations();
  });

  // The frame around the editor draws the focus
  // frame of the other outlined fields while the focus is inside it. The rule
  // rides on the frame's own class (jsdom cascades no stylesheet), matching the
  // pattern of AppShell's a11y tests.
  it('draws the outlined fields\' focus frame with focus-within, without a layout shift', async () => {
    render(<RichTextEditor value="" onChange={vi.fn()} ariaLabel="Beschreibung" />);
    await screen.findByRole('textbox', { name: 'Beschreibung' });

    const frame = screen.getByRole('toolbar').parentElement!;
    const frameCss = Array.from(document.styleSheets)
      .flatMap((sheet) => Array.from((sheet as CSSStyleSheet).cssRules))
      .map((rule) => rule.cssText)
      .filter((text) => Array.from(frame.classList).some((cls) => text.includes(cls)))
      .join('\n');

    // The focused outlined field of theme.ts: border colour #1D47A4, 2 px
    // (jsdom's cssText serialises the border colour as rgb()).
    expect(frameCss).toMatch(
      /:focus-within[^{]*\{[^}]*border-color:\s*(?:#1D47A4|rgb\(29,\s*71,\s*164\))/i,
    );
    // The second pixel is an inset shadow, so the border box keeps its size
    // and nothing shifts when the focus enters.
    expect(frameCss).toMatch(/:focus-within[^{]*\{[^}]*box-shadow:\s*inset 0 0 0 1px #1D47A4/i);
  });

  describe('code and code blocks are off', () => {
    const typeInto = async (keys: string) => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      render(<RichTextEditor value="" onChange={onChange} ariaLabel="Beschreibung" orgSlug="stadt" />);
      const box = await screen.findByRole('textbox', { name: 'Beschreibung' });
      await user.click(box);
      await user.keyboard(keys);
      return { box, onChange, user };
    };

    it('keeps a word between backticks as plain text', async () => {
      const { box } = await typeInto('so `ls` ok');
      expect(box.querySelector('code')).toBeNull();
      expect(box.textContent).toBe('so `ls` ok');
    });

    it('keeps three backticks and a space as plain text', async () => {
      const { box } = await typeInto('```{ }x');
      expect(box.querySelector('pre')).toBeNull();
      expect(box.querySelector('code')).toBeNull();
      expect(box.textContent).toBe('``` x');
    });

    it('makes nothing of Mod+E', async () => {
      const { box } = await typeInto('wort{Control>}e{/Control}x');
      expect(box.querySelector('code')).toBeNull();
      expect(box.textContent).toBe('wortx');
    });

    it('makes nothing of Mod+Alt+C', async () => {
      const { box } = await typeInto('wort{Control>}{Alt>}c{/Alt}{/Control}');
      expect(box.querySelector('pre')).toBeNull();
      expect(box.querySelector('code')).toBeNull();
    });
  });
});
