import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import LogViewer, { type LogRow } from './LogViewer';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';
import type { LogEntry } from '../../api/types';

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

const makeEntry = (overrides: Partial<LogEntry> = {}): LogEntry => ({
  timestamp: '2026-01-01T12:00:00Z',
  category: 'audit',
  event: 'user_login',
  actor: { display_name: 'Alice', email: 'alice@example.com' },
  target: { type: 'user', id: 'u1' },
  changes: null,
  request_id: 'req-abc-12345678',
  integrity_hash: null,
  organization_id: 'org-1',
  ...overrides,
});

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/admin/logs']}>
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
          <Route path="/admin/logs" element={<LogViewer />} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('LogViewer', () => {
  beforeEach(() => vi.resetAllMocks());

  it('renders log entries in the table', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeEntry({ event: 'user_login', category: 'audit' }),
          makeEntry({ event: 'user_logout', category: 'app' }),
        ],
        total: 2,
        skip: 0,
        limit: 50,
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('user_login')).toBeInTheDocument());
    expect(screen.getByText('user_logout')).toBeInTheDocument();
  });

  it('category filter and search go to the server, so the whole period is filtered', async () => {
    const page = (items: LogEntry[]) => ({ data: { items, total: items.length, skip: 0, limit: 25 } });
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(
        page([
          makeEntry({ event: 'user_login', category: 'audit' }),
          makeEntry({ event: 'cache_warm', category: 'app', request_id: 'req-app-1' }),
        ]),
      )
      .mockResolvedValueOnce(page([makeEntry({ event: 'cache_warm', category: 'app', request_id: 'req-app-1' })]))
      .mockResolvedValueOnce(page([makeEntry({ event: 'cache_warm', category: 'app', request_id: 'req-app-1' })]));
    renderPage();
    await waitFor(() => expect(screen.getByText('user_login')).toBeInTheDocument());

    await userEvent.click(screen.getByLabelText('Kategorie'));
    await userEvent.click(screen.getByRole('option', { name: /anwendung/i }));
    await waitFor(() => expect(screen.queryByText('user_login')).not.toBeInTheDocument());
    expect(vi.mocked(apiClient.get).mock.calls[1][1]).toMatchObject({
      params: { category: ['app'], skip: 0, limit: 25, order: 'desc' },
    });

    await userEvent.type(screen.getByLabelText('Suchen'), 'cache');
    await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(3));
    expect(vi.mocked(apiClient.get).mock.calls[2][1]).toMatchObject({
      params: { category: ['app'], search: 'cache' },
    });
    expect(screen.getByText('cache_warm')).toBeInTheDocument();
  });

  it('the CSV export carries the category, search and period of the view, without paging', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [makeEntry({ event: 'cache_warm', category: 'app' })], total: 1, skip: 0, limit: 25 },
    });
    const clicked: string[] = [];
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        clicked.push(this.href);
      });

    renderPage();
    await waitFor(() => expect(screen.getByText('cache_warm')).toBeInTheDocument());
    await userEvent.click(screen.getByLabelText('Kategorie'));
    await userEvent.click(screen.getByRole('option', { name: /anwendung/i }));
    await userEvent.type(screen.getByLabelText('Suchen'), 'cache');
    fireEvent.change(screen.getByLabelText('Von'), { target: { value: '2026-09-01T00:00' } });
    await userEvent.click(screen.getByRole('button', { name: 'Anwenden' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get).mock.lastCall?.[1]).toMatchObject({
        params: { search: 'cache', from_date: '2026-09-01T00:00' },
      }),
    );

    await userEvent.click(screen.getByRole('button', { name: /als csv exportieren/i }));

    expect(clicked).toHaveLength(1);
    const url = new URL(clicked[0], 'http://localhost');
    expect(url.pathname).toBe('/api/orgs/test-org/admin/logs');
    expect(url.searchParams.get('format')).toBe('csv');
    expect(url.searchParams.getAll('category')).toEqual(['app']);
    expect(url.searchParams.get('search')).toBe('cache');
    expect(url.searchParams.get('from_date')).toBe('2026-09-01T00:00');
    expect(url.searchParams.has('limit')).toBe(false);
    expect(url.searchParams.has('skip')).toBe(false);
    click.mockRestore();
  });

  // An admin reads and searches the event in words, the raw key stays visible,
  // and a search without a hit says so. The search runs on the server, so the
  // view sends the keys of the matching labels.
  it('shows a readable event with its key, finds it by a German word, and names an empty search', async () => {
    const purge = makeEntry({
      event: 'org.media_purged',
      request_id: 'req-purge-1',
      target: { entity: 'organization', id: '01a1f112-9c78-7990-a4aa-bfbb965b36d3' },
    });
    const page = (items: LogEntry[]) => ({ data: { items, total: items.length, skip: 0, limit: 25 } });
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(page([purge, makeEntry({ event: 'user_login', request_id: 'req-login-1' })]))
      .mockResolvedValueOnce(page([purge]))
      .mockResolvedValueOnce(page([]));
    renderPage();

    expect(await screen.findByText('Medien bereinigt')).toBeInTheDocument();
    expect(screen.getByText('org.media_purged')).toBeInTheDocument();
    expect(screen.getByText('organization · 01a1f112…')).toBeInTheDocument();

    const search = screen.getByLabelText('Suchen');
    await userEvent.type(search, 'Bereinig');
    await waitFor(() => expect(screen.queryByText('Anmeldung')).toBeNull());
    expect(screen.getByText('Medien bereinigt')).toBeInTheDocument();
    const sent = vi.mocked(apiClient.get).mock.calls[1][1] as { params: Record<string, unknown> };
    expect(sent.params.search).toBe('Bereinig');
    expect(sent.params.search_event).toEqual(expect.arrayContaining(['org.media_purged', 'retention_swept']));
    expect(sent.params.search_event).not.toContain('user_login');

    await userEvent.clear(search);
    await userEvent.type(search, 'gibt-es-nicht');
    expect(await screen.findByText('Kein Eintrag passt zur Suche oder zum Filter.')).toBeInTheDocument();
  });

  // The row of a media purge names the kind of media and the number of files,
  // in the words the purge itself uses.
  it('names the kind and the count of a media purge in its row', async () => {
    const purge = (overrides: Partial<LogRow>): LogRow => ({
      ...makeEntry({ event: 'org.media_purged', target: { entity: 'organization', id: 'org-1' } }),
      ...overrides,
    });
    const items: LogRow[] = [
      purge({ request_id: 'req-photo', media_type: 'photo', purged: 12 }),
      purge({ request_id: 'req-video', media_type: 'video', purged: 1 }),
      purge({ request_id: 'req-audio', media_type: 'audio', purged: 0 }),
      // An entry without the two values, and another event that carries them.
      purge({ request_id: 'req-bare', media_type: null, purged: null }),
      { ...makeEntry({ event: 'user_login', request_id: 'req-login' }), media_type: 'photo', purged: 3 },
    ];
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: { items, total: items.length, skip: 0, limit: 25 } });
    renderPage();

    expect(await screen.findByText('Fotos: 12 Dateien gelöscht.')).toBeInTheDocument();
    expect(screen.getByText('Videos: 1 Datei gelöscht.')).toBeInTheDocument();
    expect(screen.getByText('Audiodateien: 0 Dateien gelöscht.')).toBeInTheDocument();
    expect(screen.getAllByText('Medien bereinigt')).toHaveLength(4);
    expect(screen.getAllByText(/gelöscht\./)).toHaveLength(3);

    // The detail stands in the row of its own purge, beside actor and target.
    const row = screen.getByText('Fotos: 12 Dateien gelöscht.').closest('tr');
    expect(row).toHaveTextContent('Medien bereinigt');
    expect(row).toHaveTextContent('Alice');
  });

  it('expandable row reveals integrity_hash', async () => {
    const hash = 'deadbeefcafebabe1234';
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [makeEntry({ integrity_hash: hash, request_id: 'req-expand-test' })],
        total: 1,
        skip: 0,
        limit: 1000,
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText('user_login')).toBeInTheDocument());

    // Click the expand toggle inside the timestamp cell
    await userEvent.click(screen.getByRole('button', { name: /details anzeigen/i }));

    await waitFor(() => expect(screen.getByText(hash)).toBeInTheDocument());
  });

  it('shows empty state alert when no entries found', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 50 },
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Keine Einträge gefunden.')).toBeInTheDocument(),
    );
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 50 },
    });
    const { container } = renderPage();
    await waitFor(() =>
      expect(screen.getByText('Keine Einträge gefunden.')).toBeInTheDocument(),
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
