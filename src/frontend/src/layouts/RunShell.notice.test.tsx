import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import RunShell from './RunShell';
import { AuthContext } from '../auth/AuthContext';
import apiClient from '../api/client';
import { useWebSocket } from '../hooks/useWebSocket';
import { __resetNoticeStoreForTests } from '../hooks/useUnreadNotices';
import {
  __resetOperatorNoticeForTests,
  type OperatorNotice as OperatorNoticeData,
} from '../config/operatorNotice';

// Mocks as in RunShell.test.tsx: no backend, no socket, no ticking clock.
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

vi.mock('../components/CountdownTimer', () => ({
  default: ({ targetTime }: { targetTime: string }) => (
    <div data-testid="header-countdown">{targetTime}</div>
  ),
}));

const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const operatorNotice: OperatorNoticeData = {
  version: 1,
  logos: [{ src: 'programme.svg', alt: { de: 'Logo des Programms' }, height: 48 }],
  text: { de: 'Dieses Angebot wird gefoerdert.' },
  links: [{ label: 'example.org/programme', url: 'https://example.org/programme' }],
};

// The menu of 0.9.2: profile, history, language, three legal pages, logout.
const MENU_ITEMS_WITHOUT_NOTICE = 7;

const renderShell = () =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/participant']}>
      <AuthContext.Provider
        value={{
          user: authUser,
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <Routes>
          <Route path="/runs/:runId/participant" element={<RunShell />}>
            <Route index element={<div>Task view</div>} />
          </Route>
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

async function openMenu() {
  await userEvent.click(screen.getByRole('button', { name: 'Mehr' }));
  return screen.findByRole('menu');
}

describe('RunShell operator notice (spec 095, C12)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    __resetNoticeStoreForTests();
    vi.mocked(apiClient.get).mockRejectedValue(new Error('offline'));
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  afterEach(() => {
    __resetOperatorNoticeForTests(null);
  });

  it('without a notice the menu has no notice entry and its 0.9.2 entries', async () => {
    renderShell();

    const menu = await openMenu();
    expect(within(menu).getAllByRole('menuitem')).toHaveLength(MENU_ITEMS_WITHOUT_NOTICE);
    expect(
      within(menu).queryByRole('menuitem', { name: 'Hinweis des Betreibers' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Hinweis des Betreibers' })).not.toBeInTheDocument();
  });

  it('with a notice nothing of it renders in the task view', async () => {
    __resetOperatorNoticeForTests(operatorNotice);
    renderShell();

    expect(await screen.findByText('Task view')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Hinweis des Betreibers' })).not.toBeInTheDocument();
    expect(screen.queryByRole('img', { name: 'Logo des Programms' })).not.toBeInTheDocument();
  });

  it('with a notice the entry sits after the legal entries, before the logout', async () => {
    __resetOperatorNoticeForTests(operatorNotice);
    renderShell();

    const menu = await openMenu();
    const items = within(menu).getAllByRole('menuitem');
    expect(items).toHaveLength(MENU_ITEMS_WITHOUT_NOTICE + 1);
    const names = items.map((item) => item.textContent);
    expect(names.slice(-3)).toEqual(['Impressum', 'Hinweis des Betreibers', 'Abmelden']);
    // The divider above the logout stays between the entry and the logout.
    const entry = items[items.length - 2];
    expect(entry.nextElementSibling).toHaveRole('separator');
  });

  it('the entry opens a dialog with the notice, and closing it returns the focus', async () => {
    __resetOperatorNoticeForTests(operatorNotice);
    renderShell();
    const menuButton = screen.getByRole('button', { name: 'Mehr' });

    const menu = await openMenu();
    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Hinweis des Betreibers' }));

    const dialog = await screen.findByRole('dialog', { name: 'Hinweis des Betreibers' });
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
    const region = within(dialog).getByRole('region', { name: 'Hinweis des Betreibers' });
    expect(within(region).getByRole('img', { name: 'Logo des Programms' })).toHaveAttribute(
      'src',
      '/operator/programme.svg',
    );
    expect(within(region).getByText('Dieses Angebot wird gefoerdert.')).toBeInTheDocument();
    expect(within(region).getByRole('link', { name: 'example.org/programme' })).toHaveAttribute(
      'href',
      'https://example.org/programme',
    );

    await userEvent.click(within(dialog).getByRole('button', { name: 'Schließen' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.queryByRole('region', { name: 'Hinweis des Betreibers' })).not.toBeInTheDocument();
    await waitFor(() => expect(menuButton).toHaveFocus());
  });

  it('a notice that arrives after the first render adds the entry', async () => {
    renderShell();
    __resetOperatorNoticeForTests(operatorNotice);

    const menu = await openMenu();
    expect(
      within(menu).getByRole('menuitem', { name: 'Hinweis des Betreibers' }),
    ).toBeInTheDocument();
  });
});
