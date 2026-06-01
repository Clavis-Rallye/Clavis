import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import StationLibraryList from './StationLibraryList';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const user = {
  id: 'u1',
  email: 'admin@example.com',
  display_name: 'Admin',
  role: 'org_admin' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const stationFixture = (
  id: string,
  title: string,
  usage?: Record<string, unknown>,
  usageCount?: number,
) => ({
  id,
  organization_id: 'org-1',
  title: { de: title },
  description: { de: '' },
  address: 'Rathausplatz 1',
  location_lat: null,
  location_lng: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  created_by_display_name: 'Sarah',
  usage: usage ?? {
    rallye_ids: [],
    rallye_titles: [],
  },
  usage_count: usageCount ?? 0,
});

const STATIONS_URL = '/api/orgs/test-org/stations/';
const emptyPage = { data: { items: [], total: 0, skip: 0, limit: 25 } };

const renderList = () =>
  render(
    <MemoryRouter initialEntries={['/stations']}>
      <AuthContext.Provider
        value={{
          user,
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <Routes>
          <Route path="/stations" element={<StationLibraryList />} />
          <Route path="/stations/new" element={<div>Create page</div>} />
          <Route path="/stations/:id" element={<div>Detail page</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('StationLibraryList', () => {
  beforeEach(() => vi.resetAllMocks());

  it('renders empty state when no stations', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(emptyPage);
    renderList();
    expect(await screen.findByText('Noch keine Stationen vorhanden.')).toBeInTheDocument();
  });

  it('loads the first page once with the default server query', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(emptyPage);
    renderList();
    await waitFor(() =>
      expect(apiClient.get).toHaveBeenCalledWith(STATIONS_URL, {
        params: { sort: 'title', order: 'asc', lang: 'de', skip: 0, limit: 25 },
      }),
    );
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it('sends a typed query after the debounce', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(emptyPage);
    renderList();
    await waitFor(() => expect(apiClient.get).toHaveBeenCalledTimes(1));

    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'Rathaus');

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ q: 'Rathaus', skip: 0 }),
      }),
    );
    expect(apiClient.get).toHaveBeenCalledTimes(2);
  });

  it('shows the no-match message for an active search, the empty message without one', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(emptyPage);
    renderList();
    expect(await screen.findByText('Noch keine Stationen vorhanden.')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'zzz');

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ q: 'zzz' }),
      }),
    );
    expect(await screen.findByText('Keine Station passt zu dieser Suche.')).toBeInTheDocument();
    expect(screen.queryByText('Noch keine Stationen vorhanden.')).not.toBeInTheDocument();
  });

  it('renders rows with usage and unused label', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [
          stationFixture(
            's1',
            'Rathaus',
            {
              rallye_ids: ['r1'],
              rallye_titles: [{ de: 'Augsburg Tour' }],
            },
            1,
          ),
          stationFixture('s2', 'Fuggerei'),
        ],
        total: 2,
        skip: 0,
        limit: 25,
      },
    });
    renderList();

    expect(await screen.findByText('Rathaus')).toBeInTheDocument();
    expect(screen.getByText('Augsburg Tour')).toBeInTheDocument();
    expect(screen.getByText('Nicht verwendet')).toBeInTheDocument();
  });

  it('shows the usage column with usage_count and sorts through sort=usage', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [
          stationFixture(
            's1',
            'Rathaus',
            {
              rallye_ids: ['r1', 'r2'],
              rallye_titles: [{ de: 'Altstadt' }, { de: 'Familienrallye' }],
            },
            2,
          ),
          stationFixture('s2', 'Fuggerei'),
        ],
        total: 2,
        skip: 0,
        limit: 25,
      },
    });
    renderList();

    expect(await screen.findByText('Rathaus')).toBeInTheDocument();
    expect(screen.getByText('Verwendungen')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();

    await userEvent.click(await screen.findByRole('button', { name: 'Verwendungen' }));

    await waitFor(() =>
      expect(apiClient.get).toHaveBeenCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ sort: 'usage', order: 'asc' }),
      }),
    );
  });

  it('delete in use shows rallye warning then DELETEs', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [
          stationFixture(
            's1',
            'Rathaus',
            {
              rallye_ids: ['r1'],
              rallye_titles: [{ de: 'Augsburg Tour' }],
            },
            1,
          ),
        ],
        total: 1,
        skip: 0,
        limit: 25,
      },
    });
    vi.mocked(apiClient.delete).mockResolvedValue({
      data: {
        rallye_ids: ['r1'],
        rallye_titles: [{ de: 'Augsburg Tour' }],
      },
    });

    renderList();
    expect(await screen.findByText('Rathaus')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Archivieren: Rathaus/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Augsburg Tour/)).toBeInTheDocument();
    // The in-use confirmation also names the way back.
    expect(within(dialog).getByText(/Archivierte anzeigen/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /archivieren/i }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/stations/s1');
    });
  });

  // A row duplicates its station, and the copy
  // opens at once.
  it('duplicates a station from its row and opens the copy', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [stationFixture('s1', 'Rathaus')], total: 1, skip: 0, limit: 25 },
    });
    vi.mocked(apiClient.post).mockResolvedValueOnce({ data: { id: 's1-copy' } });
    renderList();

    await userEvent.click(await screen.findByRole('button', { name: 'Duplizieren: Rathaus' }));

    expect(apiClient.post).toHaveBeenCalledWith('/api/orgs/test-org/stations/s1/duplicate');
    expect(await screen.findByText('Detail page')).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(emptyPage);
    const { container } = renderList();
    expect(await screen.findByText('Noch keine Stationen vorhanden.')).toBeInTheDocument();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // Below lg the date column is hidden, so its sort lives in
  // the toolbar twin outside the table: reachable by keyboard, with an
  // accessible name that states the sort the next activation applies.
  it('sorts by creation date through the narrow-screen control and reverses it', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [stationFixture('s1', 'Rathaus')], total: 1, skip: 0, limit: 25 },
    });
    renderList();
    expect(await screen.findByText('Rathaus')).toBeInTheDocument();

    const narrowSort = screen.getByRole('button', { name: 'Datum: Aufsteigend sortieren' });
    expect(narrowSort.closest('table')).toBeNull();

    await userEvent.click(narrowSort);
    await waitFor(() =>
      expect(apiClient.get).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ sort: 'created_at', order: 'asc' }),
      }),
    );

    // Reversal by keyboard: focus the twin, press Enter.
    screen.getByRole('button', { name: 'Datum: Absteigend sortieren' }).focus();
    await userEvent.keyboard('{Enter}');
    await waitFor(() =>
      expect(apiClient.get).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ sort: 'created_at', order: 'desc' }),
      }),
    );
  });

  // Archiving a row while "Show archived" is on must not make it vanish.
  it('archiving with "Show archived" on keeps the row visible and marks it archived', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: { items: [stationFixture('s1', 'Rathaus')], total: 1, skip: 0, limit: 25 },
      })
      .mockResolvedValueOnce({
        data: { items: [stationFixture('s1', 'Rathaus')], total: 1, skip: 0, limit: 25 },
      })
      .mockResolvedValueOnce({
        data: {
          items: [{ ...stationFixture('s1', 'Rathaus'), is_deleted: true }],
          total: 1,
          skip: 0,
          limit: 25,
        },
      });
    vi.mocked(apiClient.delete).mockResolvedValue({ data: {} });

    renderList();
    expect(await screen.findByText('Rathaus')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(2));
    expect(vi.mocked(apiClient.get)).toHaveBeenNthCalledWith(2, STATIONS_URL, {
      params: expect.objectContaining({ include_deleted: true }),
    });

    await userEvent.click(screen.getByRole('button', { name: /Archivieren: Rathaus/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /archivieren/i }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/stations/s1');
    });
    // Row stays visible with the archived chip instead of vanishing.
    await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(3));
    expect(screen.getByText('Rathaus')).toBeInTheDocument();
    expect(screen.getByText('Archiviert')).toBeInTheDocument();
  });

  it('archiving with "Show archived" off removes the row from the list', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: { items: [stationFixture('s1', 'Rathaus')], total: 1, skip: 0, limit: 25 },
      })
      .mockResolvedValueOnce({
        data: { items: [], total: 0, skip: 0, limit: 25 },
      });
    vi.mocked(apiClient.delete).mockResolvedValue({ data: {} });

    renderList();
    expect(await screen.findByText('Rathaus')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Archivieren: Rathaus/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /archivieren/i }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/stations/s1');
    });
    await waitFor(() => expect(screen.queryByText('Rathaus')).not.toBeInTheDocument());
    // The list says what happened.
    expect(await screen.findByText('Station archiviert.')).toBeInTheDocument();
  });

  // The search also matches the address, so the row shows it.
  it('shows the address of a station under its name, and nothing for a station without one', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [
          stationFixture('s1', 'Rathaus'),
          { ...stationFixture('s2', 'Fuggerei'), address: null },
        ],
        total: 2,
        skip: 0,
        limit: 25,
      },
    });
    renderList();

    const name = await screen.findByText('Rathaus');
    const cell = name.closest('td')!;
    expect(within(cell).getByText('Rathausplatz 1')).toBeInTheDocument();
    expect(screen.getAllByText('Rathausplatz 1')).toHaveLength(1);
  });

  it('reloads after an archive delete with the current search, sort and page', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [stationFixture('s1', 'Rathaus')], total: 40, skip: 0, limit: 25 },
    });
    vi.mocked(apiClient.delete).mockResolvedValue({ data: {} });

    renderList();
    expect(await screen.findByText('Rathaus')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'Rathaus');
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ q: 'Rathaus' }),
      }),
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Verwendungen' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ sort: 'usage', order: 'asc' }),
      }),
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Nächste Seite' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ skip: 25 }),
      }),
    );

    const callsBeforeDelete = vi.mocked(apiClient.get).mock.calls.length;
    await userEvent.click(screen.getByRole('button', { name: /Archivieren: Rathaus/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /archivieren/i }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/stations/s1');
    });
    // The reload repeats the current search, sort and page. Asserting
    // the call count grew rules out the handler simply reusing the last
    // matching request without actually reloading.
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(callsBeforeDelete + 1),
    );
    expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
      params: { q: 'Rathaus', sort: 'usage', order: 'asc', lang: 'de', skip: 25, limit: 25 },
    });
  });

  it('permanent delete reloads from the server with the current search, sort and page', async () => {
    const archivedPage = {
      data: {
        items: [{ ...stationFixture('s1', 'Rathaus'), is_deleted: true }],
        total: 40,
        skip: 0,
        limit: 25,
      },
    };
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(archivedPage) // initial load
      .mockResolvedValueOnce(archivedPage) // search settled
      .mockResolvedValueOnce(archivedPage) // sort
      .mockResolvedValueOnce(archivedPage) // page forward
      // Reload after permanent delete: the row is gone but the total keeps
      // page 2 valid, so the table does not emit a corrected page on top.
      .mockResolvedValue({ data: { items: [], total: 40, skip: 25, limit: 25 } });
    vi.mocked(apiClient.delete).mockResolvedValue({ data: {} });

    renderList();
    expect(await screen.findByText('Rathaus')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'Rathaus');
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ q: 'Rathaus' }),
      }),
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Verwendungen' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ sort: 'usage', order: 'asc' }),
      }),
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Nächste Seite' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ skip: 25 }),
      }),
    );

    const callsBeforeDelete = vi.mocked(apiClient.get).mock.calls.length;
    await userEvent.click(await screen.findByRole('button', { name: 'Endgültig löschen: Rathaus' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Endgültig löschen' }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/stations/s1/permanent');
    });
    // The reload repeats the current search, sort and page. Asserting
    // the call count grew rules out the handler simply reusing the last
    // matching request without actually reloading.
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(callsBeforeDelete + 1),
    );
    expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
      params: { q: 'Rathaus', sort: 'usage', order: 'asc', lang: 'de', skip: 25, limit: 25 },
    });
    await waitFor(() => expect(screen.queryByText('Rathaus')).not.toBeInTheDocument());
  });

  it('restoring reloads with the current search, sort, page and archive view and drops the archived mark', async () => {
    const archivedRow = { ...stationFixture('s1', 'Rathaus'), is_deleted: true };
    const archivedPage = { data: { items: [archivedRow], total: 40, skip: 0, limit: 25 } };
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(emptyPage) // initial load, archive view off
      .mockResolvedValueOnce(archivedPage) // switch on
      .mockResolvedValueOnce(archivedPage) // search settled
      .mockResolvedValueOnce(archivedPage) // sort
      .mockResolvedValueOnce(archivedPage) // page forward
      // Reload after restore: the row reappears without its archived state.
      .mockResolvedValue({
        data: { items: [stationFixture('s1', 'Rathaus')], total: 40, skip: 25, limit: 25 },
      });
    vi.mocked(apiClient.post).mockResolvedValue({ data: stationFixture('s1', 'Rathaus') });

    renderList();
    expect(await screen.findByText('Noch keine Stationen vorhanden.')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    expect(await screen.findByText('Rathaus')).toBeInTheDocument();
    expect(screen.getByText('Archiviert')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'Rathaus');
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ q: 'Rathaus' }),
      }),
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Verwendungen' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ sort: 'usage', order: 'asc' }),
      }),
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Nächste Seite' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
        params: expect.objectContaining({ skip: 25 }),
      }),
    );

    const callsBeforeRestore = vi.mocked(apiClient.get).mock.calls.length;
    await userEvent.click(await screen.findByRole('button', { name: 'Wiederherstellen: Rathaus' }));
    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/api/orgs/test-org/stations/s1/restore'),
    );
    // The reload repeats the current search, sort, page and archive view,
    // so the restored row reappears without its archived mark. Asserting the
    // call count grew rules out the handler simply reusing the last matching
    // request without actually reloading.
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(callsBeforeRestore + 1),
    );
    expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(STATIONS_URL, {
      params: {
        q: 'Rathaus',
        sort: 'usage',
        order: 'asc',
        include_deleted: true,
        lang: 'de',
        skip: 25,
        limit: 25,
      },
    });
    await waitFor(() => expect(screen.queryByText('Archiviert')).not.toBeInTheDocument());
    expect(screen.getByText('Rathaus')).toBeInTheDocument();
  });
});
