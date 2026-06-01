import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import ChallengeLibraryList from './ChallengeLibraryList';
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

const challengeFixture = (
  id: string,
  title: string,
  usage?: Record<string, unknown>,
  usageCount?: number,
) => ({
  id,
  organization_id: 'org-1',
  challenge_type: 'text',
  title: { de: title },
  instructions: { de: '' },
  scoring_rules: { max_points: 10 },
  time_estimate_minutes: 5,
  suggested_solution: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  created_by_display_name: 'Sarah',
  usage: usage ?? {
    station_ids: [],
    station_titles: [],
    rallye_ids: [],
    rallye_titles: [],
  },
  usage_count: usageCount ?? 0,
});

const CHALLENGES_URL = '/api/orgs/test-org/challenges/';
const emptyPage = { data: { items: [], total: 0, skip: 0, limit: 25 } };

const renderList = () =>
  render(
    <MemoryRouter initialEntries={['/challenges']}>
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
          <Route path="/challenges" element={<ChallengeLibraryList />} />
          <Route path="/challenges/new" element={<div>Create page</div>} />
          <Route path="/challenges/:id" element={<div>Detail page</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('ChallengeLibraryList', () => {
  beforeEach(() => vi.resetAllMocks());

  it('renders empty state when no challenges', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(emptyPage);
    renderList();
    expect(await screen.findByText('Noch keine Aufgaben vorhanden.')).toBeInTheDocument();
  });

  it('loads the first page once with the default server query', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(emptyPage);
    renderList();
    await waitFor(() =>
      expect(apiClient.get).toHaveBeenCalledWith(CHALLENGES_URL, {
        params: { sort: 'title', order: 'asc', lang: 'de', skip: 0, limit: 25 },
      }),
    );
    expect(apiClient.get).toHaveBeenCalledTimes(1);
  });

  it('renders rows with station usage and the usage column', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [
          challengeFixture(
            'c1',
            'Rätsel',
            {
              station_ids: ['s1'],
              station_titles: [{ de: 'Rathaus' }],
              rallye_ids: [],
              rallye_titles: [],
            },
            2,
          ),
          challengeFixture('c2', 'Fotoserie'),
        ],
        total: 2,
        skip: 0,
        limit: 25,
      },
    });
    renderList();
    expect(await screen.findByText('Rätsel')).toBeInTheDocument();
    expect(screen.getByText('Rathaus')).toBeInTheDocument();
    expect(screen.getByText('Verwendungen')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
  });

  it('choosing "Quiz" sends type=quiz, resets skip to 0 and shows the total from the response', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: { items: [challengeFixture('c1', 'Rätsel')], total: 30, skip: 0, limit: 25 },
      })
      .mockResolvedValue({
        data: {
          items: [
            { ...challengeFixture('q1', 'Quiz 1'), challenge_type: 'quiz' },
            { ...challengeFixture('q2', 'Quiz 2'), challenge_type: 'quiz' },
          ],
          total: 30,
          skip: 25,
          limit: 25,
        },
      });
    renderList();
    expect(await screen.findByText('Rätsel')).toBeInTheDocument();

    // Page forward first so the skip reset is observable.
    await userEvent.click(await screen.findByRole('button', { name: 'Nächste Seite' }));
    await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(2));
    expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
      params: expect.objectContaining({ skip: 25 }),
    });

    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Typ' }));
    fireEvent.click(screen.getByRole('option', { name: 'Quiz' }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ type: 'quiz', skip: 0 }),
      }),
    );
    // The total of the filtered result drives the range caption.
    expect(screen.getByText('1–25 von 30')).toBeInTheDocument();
  });

  it('sorting by points, type or duration sends the matching sort', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [challengeFixture('c1', 'Rätsel')], total: 1, skip: 0, limit: 25 },
    });
    renderList();
    await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole('button', { name: 'Punkte' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ sort: 'points', order: 'asc' }),
      }),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Typ' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ sort: 'type', order: 'asc' }),
      }),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Dauer (Min.)' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ sort: 'duration', order: 'asc' }),
      }),
    );
  });

  it('shows the usage column with usage_count and sorts through sort=usage', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [
          challengeFixture(
            'c1',
            'Rätsel',
            {
              station_ids: [],
              station_titles: [],
              rallye_ids: ['r1', 'r2'],
              rallye_titles: [{ de: 'Altstadt' }, { de: 'Familienrallye' }],
            },
            2,
          ),
          challengeFixture('c2', 'Fotoserie'),
        ],
        total: 2,
        skip: 0,
        limit: 25,
      },
    });
    renderList();

    expect(await screen.findByText('Rätsel')).toBeInTheDocument();
    expect(screen.getByText('Verwendungen')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();

    await userEvent.click(await screen.findByRole('button', { name: 'Verwendungen' }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ sort: 'usage', order: 'asc' }),
      }),
    );
  });

  it('shows the no-match message for an active search, the empty message without one', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(emptyPage);
    renderList();
    expect(await screen.findByText('Noch keine Aufgaben vorhanden.')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'zzz');

    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ q: 'zzz' }),
      }),
    );
    expect(
      await screen.findByText('Keine Aufgabe passt zu dieser Suche oder diesem Filter.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Noch keine Aufgaben vorhanden.')).not.toBeInTheDocument();
  });

  it('delete in use shows station warning then DELETEs', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [
          challengeFixture('c1', 'Rätsel', {
            station_ids: ['s1'],
            station_titles: [{ de: 'Rathaus' }],
            rallye_ids: [],
            rallye_titles: [],
          }),
        ],
        total: 1,
        skip: 0,
        limit: 25,
      },
    });
    vi.mocked(apiClient.delete).mockResolvedValue({
      data: { station_ids: ['s1'], station_titles: [{ de: 'Rathaus' }], rallye_ids: [], rallye_titles: [] },
    });

    renderList();
    expect(await screen.findByText('Rätsel')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Archivieren: Rätsel/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/Rathaus/)).toBeInTheDocument();
    // The in-use confirmation also names the way back.
    expect(within(dialog).getByText(/Archivierte anzeigen/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: /archivieren/i }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/challenges/c1');
    });
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(emptyPage);
    const { container } = renderList();
    expect(await screen.findByText('Noch keine Aufgaben vorhanden.')).toBeInTheDocument();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // Below lg the date column is hidden, so its sort lives in the toolbar twin
  // outside the table: reachable by keyboard, with an accessible name that
  // states the sort the next activation applies.
  it('sorts by creation date through the narrow-screen control and reverses it', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [challengeFixture('c1', 'Rätsel')], total: 1, skip: 0, limit: 25 },
    });
    renderList();
    expect(await screen.findByText('Rätsel')).toBeInTheDocument();

    const narrowSort = screen.getByRole('button', { name: 'Datum: Aufsteigend sortieren' });
    expect(narrowSort.closest('table')).toBeNull();

    await userEvent.click(narrowSort);
    await waitFor(() =>
      expect(apiClient.get).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ sort: 'created_at', order: 'asc' }),
      }),
    );

    await userEvent.click(screen.getByRole('button', { name: 'Datum: Absteigend sortieren' }));
    await waitFor(() =>
      expect(apiClient.get).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ sort: 'created_at', order: 'desc' }),
      }),
    );
  });

  // Archiving a row while "Show archived" is on must not make it vanish.
  it('archiving with "Show archived" on keeps the row visible and marks it archived', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: { items: [challengeFixture('c1', 'Rätsel')], total: 1, skip: 0, limit: 25 },
      })
      .mockResolvedValueOnce({
        data: { items: [challengeFixture('c1', 'Rätsel')], total: 1, skip: 0, limit: 25 },
      })
      .mockResolvedValueOnce({
        data: {
          items: [{ ...challengeFixture('c1', 'Rätsel'), is_deleted: true }],
          total: 1,
          skip: 0,
          limit: 25,
        },
      });
    vi.mocked(apiClient.delete).mockResolvedValueOnce({ data: {} });

    renderList();
    expect(await screen.findByText('Rätsel')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(2));
    expect(vi.mocked(apiClient.get)).toHaveBeenNthCalledWith(2, CHALLENGES_URL, {
      params: expect.objectContaining({ include_deleted: true }),
    });

    await userEvent.click(screen.getByRole('button', { name: /Archivieren: Rätsel/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /archivieren/i }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/challenges/c1');
    });
    // Row stays visible with the archived chip instead of vanishing.
    await waitFor(() => expect(vi.mocked(apiClient.get)).toHaveBeenCalledTimes(3));
    expect(screen.getByText('Rätsel')).toBeInTheDocument();
    expect(screen.getByText('Archiviert')).toBeInTheDocument();
  });

  it('archiving with "Show archived" off removes the row from the list', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({
        data: { items: [challengeFixture('c1', 'Rätsel')], total: 1, skip: 0, limit: 25 },
      })
      .mockResolvedValueOnce({
        data: { items: [], total: 0, skip: 0, limit: 25 },
      });
    vi.mocked(apiClient.delete).mockResolvedValueOnce({ data: {} });

    renderList();
    expect(await screen.findByText('Rätsel')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Archivieren: Rätsel/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /archivieren/i }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/challenges/c1');
    });
    await waitFor(() => expect(screen.queryByText('Rätsel')).not.toBeInTheDocument());
  });

  it('reloads after an archive delete with the current search, filter, sort and page', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: {
        items: [{ ...challengeFixture('q1', 'Quiz 1'), challenge_type: 'quiz' }],
        total: 40,
        skip: 0,
        limit: 25,
      },
    });
    vi.mocked(apiClient.delete).mockResolvedValue({ data: {} });

    renderList();
    expect(await screen.findByText('Quiz 1')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'quiz');
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ q: 'quiz' }),
      }),
    );
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Typ' }));
    fireEvent.click(screen.getByRole('option', { name: 'Quiz' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ type: 'quiz' }),
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Punkte' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ sort: 'points', order: 'asc' }),
      }),
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Nächste Seite' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ skip: 25 }),
      }),
    );

    await userEvent.click(await screen.findByRole('button', { name: /Archivieren: Quiz 1/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /archivieren/i }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/challenges/q1');
    });
    // The reload repeats the current search, filter, sort and page.
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: {
          q: 'quiz',
          type: 'quiz',
          sort: 'points',
          order: 'asc',
          lang: 'de',
          skip: 25,
          limit: 25,
        },
      }),
    );
  });

  it('permanent delete reloads from the server with the current search, sort and page', async () => {
    const archivedPage = {
      data: {
        items: [{ ...challengeFixture('c1', 'Rätsel'), is_deleted: true }],
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
    vi.mocked(apiClient.delete).mockResolvedValueOnce({ data: {} });

    renderList();
    expect(await screen.findByText('Rätsel')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'rätsel');
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ q: 'rätsel' }),
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Punkte' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ sort: 'points', order: 'asc' }),
      }),
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Nächste Seite' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ skip: 25 }),
      }),
    );

    await userEvent.click(await screen.findByRole('button', { name: 'Endgültig löschen: Rätsel' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Endgültig löschen' }));

    await waitFor(() => {
      expect(apiClient.delete).toHaveBeenCalledWith('/api/orgs/test-org/challenges/c1/permanent');
    });
    // The reload repeats the current search, sort and page.
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: { q: 'rätsel', sort: 'points', order: 'asc', lang: 'de', skip: 25, limit: 25 },
      }),
    );
    await waitFor(() => expect(screen.queryByText('Rätsel')).not.toBeInTheDocument());
  });

  it('restoring reloads with the current search, sort and page and drops the archived mark', async () => {
    const archivedRow = { ...challengeFixture('c1', 'Rätsel'), is_deleted: true };
    const archivedPage = { data: { items: [archivedRow], total: 40, skip: 0, limit: 25 } };
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce(emptyPage) // initial load, archive view off
      .mockResolvedValueOnce(archivedPage) // switch on
      .mockResolvedValueOnce(archivedPage) // search settled
      .mockResolvedValueOnce(archivedPage) // sort
      .mockResolvedValueOnce(archivedPage) // page forward
      .mockResolvedValue({ data: { items: [challengeFixture('c1', 'Rätsel')], total: 40, skip: 25, limit: 25 } });
    vi.mocked(apiClient.post).mockResolvedValue({ data: challengeFixture('c1', 'Rätsel') });

    renderList();
    expect(await screen.findByText('Noch keine Aufgaben vorhanden.')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('switch', { name: 'Archivierte anzeigen' }));
    expect(await screen.findByText('Rätsel')).toBeInTheDocument();
    expect(screen.getByText('Archiviert')).toBeInTheDocument();

    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'rätsel');
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ q: 'rätsel' }),
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Punkte' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ sort: 'points', order: 'asc' }),
      }),
    );
    await userEvent.click(await screen.findByRole('button', { name: 'Nächste Seite' }));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: expect.objectContaining({ skip: 25 }),
      }),
    );

    await userEvent.click(await screen.findByRole('button', { name: 'Wiederherstellen: Rätsel' }));
    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith('/api/orgs/test-org/challenges/c1/restore'),
    );
    // The reload repeats the current search, sort, page and archive view,
    // so the restored row reappears without its archived mark.
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenLastCalledWith(CHALLENGES_URL, {
        params: {
          q: 'rätsel',
          sort: 'points',
          order: 'asc',
          include_deleted: true,
          lang: 'de',
          skip: 25,
          limit: 25,
        },
      }),
    );
    await waitFor(() => expect(screen.queryByText('Archiviert')).not.toBeInTheDocument());
    expect(screen.getByText('Rätsel')).toBeInTheDocument();
  });
});
