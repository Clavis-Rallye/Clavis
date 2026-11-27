import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import theme from '../theme';
import AppShell from './AppShell';
import { AuthContext } from '../auth/AuthContext';
import apiClient from '../api/client';
import {
  __resetOperatorNoticeForTests,
  type OperatorNotice as OperatorNoticeData,
} from '../config/operatorNotice';

// Mocks and render helper as in AppShell.a11y.test.tsx.
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const adminUser = {
  id: 'u1',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const operatorNotice: OperatorNoticeData = {
  version: 1,
  logos: [{ src: 'programme.svg', alt: { de: 'Logo des Programms' }, height: 48 }],
  text: { de: 'Dieses Angebot wird gefoerdert.' },
  links: [{ label: 'example.org/programme', url: 'https://example.org/programme' }],
};

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

describe('<AppShell> operator notice (spec 095, C11)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { name: 'Test Org', languages: ['de', 'en'] },
    });
  });

  afterEach(() => {
    __resetOperatorNoticeForTests(null);
  });

  it('shows the notice in a contentinfo landmark after main, hidden in print', async () => {
    __resetOperatorNoticeForTests(operatorNotice);
    await renderShell();

    const main = screen.getByRole('main');
    const footer = screen.getByRole('contentinfo');
    const region = within(footer).getByRole('region', { name: 'Hinweis des Betreibers' });

    expect(main).not.toContainElement(region);
    expect(main).not.toContainElement(footer);
    // The footer follows main, so the notice comes after the page content.
    expect(main.nextElementSibling).toBe(footer);
    expect(footer).toHaveClass('print-hidden');
    expect(within(region).getByRole('img', { name: 'Logo des Programms' })).toBeInTheDocument();
  });

  it('keeps main as the target of the skip link', async () => {
    __resetOperatorNoticeForTests(operatorNotice);
    await renderShell();

    const main = screen.getByText('Runs Content').closest('main')!;
    expect(main).toHaveAttribute('id', 'main-content');
    expect(main).toHaveAttribute('tabindex', '-1');
    expect(window.getComputedStyle(main).minWidth).toBe('0px');
    expect(screen.getByRole('link', { name: 'Zum Hauptinhalt springen' })).toHaveAttribute(
      'href',
      '#main-content',
    );
  });

  it('renders no contentinfo landmark without a notice', async () => {
    await renderShell();

    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.queryByRole('contentinfo')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Hinweis des Betreibers' })).not.toBeInTheDocument();
  });

  it('shows a notice that arrives after the first render', async () => {
    await renderShell();
    expect(screen.queryByRole('contentinfo')).not.toBeInTheDocument();

    act(() => __resetOperatorNoticeForTests(operatorNotice));

    expect(
      within(screen.getByRole('contentinfo')).getByRole('region', { name: 'Hinweis des Betreibers' }),
    ).toBeInTheDocument();
  });

  it('has no axe violations with a notice', async () => {
    __resetOperatorNoticeForTests(operatorNotice);
    const { container } = await renderShell();

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
