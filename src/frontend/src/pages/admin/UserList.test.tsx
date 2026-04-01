import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import { ThemeProvider } from '@mui/material/styles';
import UserList from './UserList';
import theme from '../../theme';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const adminUser = {
  id: 'u0',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const makeUser = (overrides: Record<string, unknown> = {}) => ({
  id: 'u1',
  email: 'alice@example.com',
  display_name: 'Alice',
  role: 'participant',
  organization_id: 'org-1',
  is_active: true,
  totp_enabled: false,
  created_at: '2026-01-01T00:00:00Z',
  last_login: null,
  ...overrides,
});

// Wrapped in the real ThemeProvider: the contrast assertion below is only
// meaningful when the palette the component reads is the shipped one.
const renderList = () =>
  render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/admin/users']}>
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
            <Route path="/admin/users" element={<UserList />} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>
    </ThemeProvider>,
  );

const userListCalls = () =>
  vi.mocked(apiClient.get).mock.calls.filter(([url]) => String(url).endsWith('/users/'));

describe('UserList', () => {
  beforeEach(() => vi.resetAllMocks());

  // The admin who took over through an operator invitation reads which accounts
  // that deactivated.
  it('tells a new admin which accounts the takeover deactivated', async () => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/invitations/takeover')) {
        return Promise.resolve({
          data: {
            accepted_at: '2026-11-18T10:00:00Z',
            replaced: [
              { id: 'a1', email: 'weg@example.com', display_name: 'Ausgeschieden', is_active: false, last_login: null },
            ],
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 20 } });
    });
    renderList();

    expect(
      await screen.findByText(
        'Du hast die Verwaltung übernommen. Diese Konten sind jetzt deaktiviert: Ausgeschieden (weg@example.com).',
      ),
    ).toBeInTheDocument();
  });

  it('renders table rows for returned users', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeUser({ id: 'u1', display_name: 'Alice', email: 'alice@example.com', role: 'participant' }),
          makeUser({ id: 'u2', display_name: 'Bob', email: 'bob@example.com', role: 'coordinator' }),
        ],
        total: 2,
        skip: 0,
        limit: 20,
      },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());
    expect(screen.getByText('alice@example.com')).toBeInTheDocument();
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(screen.getByText('bob@example.com')).toBeInTheDocument();
  });

  it('role filter narrows the displayed rows client-side without a refetch', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeUser({ id: 'u1', display_name: 'Alice', role: 'participant' }),
          makeUser({ id: 'u2', display_name: 'Bob', email: 'bob@example.com', role: 'coordinator' }),
        ],
        total: 2,
        skip: 0,
        limit: 1000,
      },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());
    expect(screen.getByText('Bob')).toBeInTheDocument();

    // DataTable role filter is a MUI Select labelled "Rolle"
    await userEvent.click(screen.getByLabelText('Rolle'));
    await userEvent.click(screen.getByRole('option', { name: /spielleitung/i }));

    // Client-side narrowing: only the coordinator remains; no second fetch
    await waitFor(() => expect(screen.queryByText('Alice')).not.toBeInTheDocument());
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(userListCalls()).toHaveLength(1);
  });

  it('status filter narrows the displayed rows client-side without a refetch', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeUser({ id: 'u1', display_name: 'Alice', is_active: true }),
          makeUser({ id: 'u2', display_name: 'Bob', email: 'bob@example.com', is_active: false }),
        ],
        total: 2,
        skip: 0,
        limit: 1000,
      },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());
    expect(screen.getByText('Bob')).toBeInTheDocument();

    // DataTable status filter is a MUI Select labelled "Status"
    await userEvent.click(screen.getByLabelText('Status'));
    await userEvent.click(screen.getByRole('option', { name: /^inaktiv$/i }));

    await waitFor(() => expect(screen.queryByText('Alice')).not.toBeInTheDocument());
    expect(screen.getByText('Bob')).toBeInTheDocument();
    expect(userListCalls()).toHaveLength(1);
  });

  it('search box filters displayed rows by display_name', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeUser({ id: 'u1', display_name: 'Alice Smith', email: 'alice@example.com' }),
          makeUser({ id: 'u2', display_name: 'Bob Jones', email: 'bob@example.com' }),
        ],
        total: 2,
        skip: 0,
        limit: 20,
      },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Alice Smith')).toBeInTheDocument());

    await userEvent.type(screen.getByPlaceholderText(/name oder e-mail/i), 'Alice');

    expect(screen.getByText('Alice Smith')).toBeInTheDocument();
    expect(screen.queryByText('Bob Jones')).not.toBeInTheDocument();
  });

  it('"Person anlegen" button opens create dialog', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 20 },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Keine Personen gefunden.')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /person anlegen/i }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /person anlegen/i })).toBeInTheDocument();
  });

  // Both header actions speak of a "Person", not once of a "Benutzer" and once
  // of a "Person".
  it('header actions use one noun for a user', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 20 },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Keine Personen gefunden.')).toBeInTheDocument());

    expect(screen.getByRole('button', { name: 'Person anlegen' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Person einladen' })).toBeInTheDocument();
  });

  // A date and time with no seconds, kept on one line.
  it('renders the last login without seconds and without wrapping', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeUser({
            id: 'u9',
            display_name: 'Clara Date',
            email: 'clara@example.com',
            last_login: '2026-02-01T14:30:45Z',
          }),
        ],
        total: 1,
        skip: 0,
        limit: 20,
      },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Clara Date')).toBeInTheDocument());

    const cell = screen.getByText((text) => /2026/.test(text) && /\d{1,2}:\d{2}/.test(text));
    expect(cell.textContent).not.toMatch(/\d{1,2}:\d{2}:\d{2}/);
    expect(cell).toHaveStyle({ whiteSpace: 'nowrap' });
  });

  // WCAG 2.4.6: headings and labels describe their purpose.
  it('the four row actions of two people name the person and are unique', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeUser({ id: 'u1', display_name: 'Alice Smith', email: 'alice@example.com' }),
          makeUser({ id: 'u2', display_name: 'Bob Jones', email: 'bob@example.com' }),
        ],
        total: 2,
        skip: 0,
        limit: 20,
      },
    });
    renderList();
    await screen.findByText('Alice Smith');

    for (const row of ['Alice Smith', 'Bob Jones']) {
      const tr = screen.getByText(row).closest('tr')!;
      for (const action of ['Bearbeiten', 'Deaktivieren', 'Passwort-Link senden', 'Löschen']) {
        const buttons = within(tr)
          .getAllByRole('button')
          .filter((b) => (b.getAttribute('aria-label') ?? '').startsWith(`${action}`));
        expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual([`${action}: ${row}`]);
      }
    }
  });

  it('disables edit, delete and deactivate for the signed-in admin (self-guard)', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeUser({ id: 'u0', display_name: 'Admin', email: 'admin@example.com', role: 'org_admin' }),
        ],
        total: 1,
        skip: 0,
        limit: 1000,
      },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('admin@example.com')).toBeInTheDocument());

    // The edit IconButton for the own row is disabled: the own account is edited
    // via /profile, not the admin list
    expect(screen.getByRole('button', { name: /bearbeiten/i })).toBeDisabled();
    // The delete IconButton for the own row is disabled
    expect(screen.getByRole('button', { name: /löschen/i })).toBeDisabled();
    // The deactivate button for the own row is disabled
    expect(screen.getByRole('button', { name: /deaktivieren/i })).toBeDisabled();
  });

  it('deleted row shows restore action; restore calls the API', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [
          makeUser({
            id: 'u9',
            display_name: 'Gone Girl',
            email: 'gone@example.com',
            is_active: false,
            is_deleted: true,
            deleted_at: '2026-06-01T00:00:00Z',
          }),
        ],
        total: 1,
        skip: 0,
        limit: 20,
      },
    });
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: makeUser({ id: 'u9' }) });
    renderList();
    await waitFor(() => expect(screen.getByText('Gone Girl')).toBeInTheDocument());

    expect(screen.getByText('Gelöscht')).toBeInTheDocument();
    const restoreButton = screen.getByRole('button', { name: /wiederherstellen/i });
    await userEvent.click(restoreButton);
    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/api/orgs/test-org/users/u9/restore'),
    );
    // A restore leaves the account deactivated, and the message names the next step.
    expect(await screen.findByText(/noch deaktiviert.*Aktivieren/)).toBeInTheDocument();
  });

  it('anonymized row has restore disabled', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [
          makeUser({
            id: 'u9',
            display_name: 'Deleted User',
            email: 'deleted-user-u9@anonymized.local',
            is_active: false,
            is_deleted: true,
            deleted_at: '2026-01-01T00:00:00Z',
          }),
        ],
        total: 1,
        skip: 0,
        limit: 20,
      },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Anonymisiert')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /wiederherstellen/i })).toBeDisabled();
  });

  it('delete dialog defaults to grace mode; checkbox switches to immediate', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [makeUser({ id: 'u1', display_name: 'Alice' })], total: 1, skip: 0, limit: 20 },
    });
    vi.mocked(apiClient.delete).mockResolvedValue({ data: undefined });
    renderList();
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /löschen/i }));
    // One message per choice, never both: restorable first ...
    expect(screen.getByText(/30 Tage lang wiederherstellen/)).toBeInTheDocument();
    expect(screen.queryByText(/nicht rückgängig/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('checkbox', { name: /Art\. 17/i }));
    // ... then final, without the 30 days.
    expect(screen.getByText(/sofort dauerhaft anonymisiert/)).toBeInTheDocument();
    expect(screen.queryByText(/30 Tage lang wiederherstellen/)).not.toBeInTheDocument();
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /^löschen$/i }));
    await waitFor(() =>
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/users/u1', {
        params: { mode: 'immediate' },
      }),
    );
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 20 },
    });
    const { container } = renderList();
    await waitFor(() => expect(screen.getByText('Keine Personen gefunden.')).toBeInTheDocument());
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The outlined row action is a medium button at the theme button size, not an
  // 11 px `warning` label.
  it('has no color-contrast violations on the row action buttons', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [makeUser({ id: 'u1', display_name: 'Alice' })], total: 1, skip: 0, limit: 20 },
    });
    const { container } = renderList();
    await waitFor(() => expect(screen.getByText('Alice')).toBeInTheDocument());

    // The two assertions that actually measure: label size and label colour.
    // The axe call below is the contrast check, but under jsdom
    // `color-contrast` can never fail: elements have no layout, so axe files
    // the rule under `incomplete`, not `violations`.
    const deactivate = screen.getByRole('button', { name: /deaktivieren/i });
    const style = window.getComputedStyle(deactivate);
    expect(parseFloat(style.fontSize)).toBeGreaterThanOrEqual(14);
    expect(style.color).toBe('rgb(179, 38, 30)'); // palette.error.main

    const results = await axe(container, { runOnly: ['color-contrast'] });
    expect(results).toHaveNoViolations();
  });
});
