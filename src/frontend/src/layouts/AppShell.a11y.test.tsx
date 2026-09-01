import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import theme from '../theme';
import AppShell from './AppShell';
import { AuthContext } from '../auth/AuthContext';
import apiClient from '../api/client';
// The skip link hands focus to #main-content. Its outline suppression is a plain
// rule of index.css, which no component imports. The test reads the shipped text
// (?inline returns it as a string), because jsdom never cascades it.
import indexCss from '../index.css?inline';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const COLLAPSED_STORAGE_KEY = 'clavis_sidebar_collapsed';

const adminUser = {
  id: 'u1',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

/**
 * Renders the admin shell with all three nav groups visible. The org fetch is
 * awaited so the resulting state update happens inside `act`.
 */
async function renderShell() {
  const result = render(
    <MemoryRouter initialEntries={['/runs']}>
      <ThemeProvider theme={theme}>
        <AuthContext.Provider
          value={{
            user: adminUser,
            isAuthenticated: true,
            isLoading: false,
            login: vi.fn(),
            logout: vi.fn(),
            checkAuth: vi.fn(),
            needsTwoFactorSetup: () => false,
          }}
        >
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/runs" element={<div>Runs Content</div>} />
            </Route>
          </Routes>
        </AuthContext.Provider>
      </ThemeProvider>
    </MemoryRouter>,
  );

  await screen.findByText('Test Org');
  return result;
}

describe('<AppShell> a11y', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { name: 'Test Org', languages: ['de', 'en'] },
    });
  });

  afterEach(() => {
    localStorage.removeItem(COLLAPSED_STORAGE_KEY);
  });

  it('expanded sidebar: no list or listitem violation', async () => {
    const { container } = await renderShell();

    const results = await axe(container, { runOnly: ['list', 'listitem'] });
    expect(results).toHaveNoViolations();
  });

  it('collapsed sidebar: no list or listitem violation', async () => {
    localStorage.setItem(COLLAPSED_STORAGE_KEY, 'true');
    const { container } = await renderShell();

    const results = await axe(container, { runOnly: ['list', 'listitem'] });
    expect(results).toHaveNoViolations();
  });

  it('expanded sidebar: no axe violations at all', async () => {
    const { container } = await renderShell();

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // A screen reader announces one list per group, with its item count. Each
  // group therefore needs its own `ul` holding exactly its items as `li`.
  it('every nav group is one list whose items are its only list items', async () => {
    await renderShell();

    const nav = screen.getByRole('navigation');
    const lists = within(nav).getAllByRole('list');
    expect(lists).toHaveLength(3);

    const itemCounts = lists.map((list) => within(list).getAllByRole('listitem').length);
    expect(itemCounts).toEqual([2, 3, 3]);
  });

  // Each group button is named "<group>: Gruppe ein- oder ausblenden".
  it('names every group button after its group', async () => {
    await renderShell();

    const nav = screen.getByRole('navigation');
    for (const title of ['Spielleitung', 'Erstellung', 'Verwaltung']) {
      expect(
        within(nav).getByRole('button', { name: `${title}: Gruppe ein- oder ausblenden` }),
      ).toBeInTheDocument();
    }
  });

  it('every group header keeps aria-expanded', async () => {
    await renderShell();

    const nav = screen.getByRole('navigation');
    for (const title of ['Spielleitung', 'Erstellung', 'Verwaltung']) {
      expect(within(nav).getByText(title).closest('[aria-expanded]')).toHaveAttribute(
        'aria-expanded',
        'true',
      );
    }
  });

  // Collapsed groups are separated by a divider between the lists, never inside one.
  it('collapsed sidebar: a divider separates the groups, not the lists', async () => {
    localStorage.setItem(COLLAPSED_STORAGE_KEY, 'true');
    await renderShell();

    const nav = screen.getByRole('navigation');
    const lists = within(nav).getAllByRole('list');
    expect(lists).toHaveLength(3);
    for (const list of lists) {
      expect(within(list).queryAllByRole('separator')).toHaveLength(0);
    }

    expect(within(nav).getAllByRole('separator')).toHaveLength(2);
  });

  // `main` is a flex child. Its default min-width auto would let it grow to a
  // wide table's width, so staff pages would overflow a 390 px screen instead of
  // letting the table's own container scroll.
  it('main may shrink below its content width (min-width 0)', async () => {
    await renderShell();

    const main = screen.getByText('Runs Content').closest('main')!;
    expect(main).toHaveAttribute('id', 'main-content');
    expect(window.getComputedStyle(main).minWidth).toBe('0px');
  });

  // Below sm the main area pads with 2 so wide pages keep more of the
  // narrow screen. jsdom never applies @media rules in its cascade (all
  // breakpoint styles are media-wrapped), so the responsive padding is
  // asserted against the generated CSS of main's own class.
  it('main pads with 2 below sm and 3 from sm up', async () => {
    await renderShell();

    const main = screen.getByText('Runs Content').closest('main')!;
    const ownCss = Array.from(document.styleSheets)
      .flatMap((sheet) => Array.from((sheet as CSSStyleSheet).cssRules))
      .map((rule) => rule.cssText)
      .filter((text) => Array.from(main.classList).some((cls) => text.includes(cls)))
      .join('\n');

    expect(ownCss).toMatch(/@media \(min-width:\s*0px\)[\s\S]*padding:\s*16px/);
    expect(ownCss).toMatch(/@media \(min-width:\s*600px\)[\s\S]*padding:\s*24px/);
  });

  // The temporary drawer's paper is a dialog and carries the name of the
  // navigation inside it. The permanent drawer is no dialog and stays without one.
  it('the open navigation drawer on a narrow screen is a dialog named Hauptnavigation', async () => {
    await renderShell();

    await userEvent.click(screen.getByRole('button', { name: 'Navigation öffnen' }));

    expect(screen.getByRole('dialog', { name: 'Hauptnavigation' })).toBeInTheDocument();
  });

  // main takes the focus from the skip link and draws no outline of its own.
  it('main carries tabindex -1 and the outline suppression of index.css', async () => {
    await renderShell();

    const main = screen.getByText('Runs Content').closest('main')!;
    expect(main).toHaveAttribute('id', 'main-content');
    expect(main).toHaveAttribute('tabindex', '-1');

    expect(indexCss).toMatch(
      /#main-content:focus,\s*#main-content:focus-visible\s*\{\s*outline:\s*none;\s*\}/,
    );
  });

  // The header button shows the welcome line and the organization. A voice
  // command with the visible words must find it, so its name starts with the
  // visible text (WCAG 2.5.3) and then says where the button leads. It carries
  // no description, so a screen reader does not say "Dashboard" twice. The axe
  // rule label-content-name-mismatch finds nothing in jsdom even on a wrong
  // name, so the name is asserted here and the rule runs in a real browser.
  it('the header button is named by the text it shows, then where it leads, with no description', async () => {
    await renderShell();

    const button = screen.getByRole('button', { name: /Willkommen zurück, Admin/ });
    expect(button).toHaveAccessibleName(/^\S.*Willkommen zurück, Admin\s+Test Org\s*,? Zum Dashboard$/s);
    expect(button).not.toHaveAttribute('aria-label');
    expect(button).not.toHaveAttribute('aria-describedby');
    expect(button).toHaveAccessibleDescription('');
  });
});
