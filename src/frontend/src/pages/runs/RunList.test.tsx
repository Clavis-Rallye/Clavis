import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import RunList from './RunList';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const coordUser = {
  id: 'u1',
  email: 'coord@example.com',
  display_name: 'Coordinator',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const makeRun = (overrides: Record<string, unknown> = {}) => ({
  id: 'run-1',
  organization_id: 'org-1',
  rallye_id: 'r1',
  name: 'Herbst-Rallye',
  state: 'configured',
  join_code: 'ABC123',
  scheduled_for: null,
  started_at: null,
  paused_at: null,
  ended_at: null,
  settings: {},
  max_teams: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

const renderList = (user: typeof coordUser | { id: string; email: string; display_name: string; role: 'guest' | 'participant' | 'coordinator' | 'creator' | 'org_admin'; organization_id: string; organization_slug: string } = coordUser) =>
  render(
    <MemoryRouter initialEntries={['/runs']}>
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
          <Route path="/runs" element={<RunList />} />
          <Route path="/runs/new" element={<div>Create page</div>} />
          <Route path="/runs/:runId" element={<div>Detail page</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('RunList', () => {
  beforeEach(() => vi.resetAllMocks());

  it('renders empty state when no runs', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });
    renderList();
    await waitFor(() => {
      expect(screen.getByText('Noch keine Läufe vorhanden.')).toBeInTheDocument();
    });
  });

  it('renders card for each run with state chip and join code', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeRun({ id: 'r1', name: 'Herbst-Rallye', state: 'configured', join_code: 'AAABBB' }),
          makeRun({ id: 'r2', name: 'Winter-Rallye', state: 'active', join_code: 'XXXYYY' }),
        ],
        total: 2,
        skip: 0,
        limit: 100,
      },
    });
    renderList();
    await waitFor(() => {
      expect(screen.getByText('Herbst-Rallye')).toBeInTheDocument();
    });
    expect(screen.getByText('AAABBB')).toBeInTheDocument();
    expect(screen.getByText('Winter-Rallye')).toBeInTheDocument();
    expect(screen.getByText('XXXYYY')).toBeInTheDocument();
    // State chips (also appear as filter button labels — any match is fine)
    expect(screen.getAllByText('Konfiguriert').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Aktiv').length).toBeGreaterThan(0);
  });

  it('the open button of a run carries the name of the run', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [makeRun({ id: 'r1', name: 'Herbst-Rallye', state: 'configured', join_code: 'AAABBB' })],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderList();
    expect(await screen.findByRole('button', { name: 'Öffnen: Herbst-Rallye' })).toBeInTheDocument();
  });

  it('state filter select narrows visible runs', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeRun({ id: 'r1', name: 'Alpha Run', state: 'configured', join_code: 'AAA111' }),
          makeRun({ id: 'r2', name: 'Beta Run', state: 'active', join_code: 'BBB222' }),
        ],
        total: 2,
        skip: 0,
        limit: 100,
      },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Alpha Run')).toBeInTheDocument());
    expect(screen.getByText('Beta Run')).toBeInTheDocument();

    // The state filter is a Select (label "Status"). Open it and pick "Konfiguriert".
    await userEvent.click(screen.getByRole('combobox', { name: 'Status' }));
    const options = await screen.findAllByRole('option');
    const configuredOption = options.find((o) => o.textContent === 'Konfiguriert');
    expect(configuredOption).toBeDefined();
    await userEvent.click(configuredOption!);

    expect(screen.getByText('Alpha Run')).toBeInTheDocument();
    expect(screen.queryByText('Beta Run')).not.toBeInTheDocument();
  });

  it('global search narrows visible runs', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: {
        items: [
          makeRun({ id: 'r1', name: 'Alpha Run', state: 'configured', join_code: 'AAA111' }),
          makeRun({ id: 'r2', name: 'Beta Run', state: 'active', join_code: 'BBB222' }),
        ],
        total: 2,
        skip: 0,
        limit: 100,
      },
    });
    renderList();
    await waitFor(() => expect(screen.getByText('Alpha Run')).toBeInTheDocument());

    // The search input uses the shared common:table.search label ("Suchen").
    await userEvent.type(screen.getByRole('textbox', { name: 'Suchen' }), 'Alpha');

    expect(screen.getByText('Alpha Run')).toBeInTheDocument();
    expect(screen.queryByText('Beta Run')).not.toBeInTheDocument();
  });

  it('"Lauf erstellen" button visible for coordinator', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });
    renderList();
    await waitFor(() => {
      expect(screen.getByText('Noch keine Läufe vorhanden.')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Lauf erstellen' })).toBeInTheDocument();
  });

  it('"Lauf erstellen" button absent for participant', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });
    renderList({ ...coordUser, role: 'participant' as const });
    await waitFor(() => {
      expect(screen.getByText('Noch keine Läufe vorhanden.')).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Lauf erstellen' })).not.toBeInTheDocument();
  });

  it('fetch error renders ErrorAlert with retry button', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('Network failure'));
    renderList();
    await waitFor(() => {
      expect(screen.getByText(/network failure/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /erneut versuchen/i })).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });
    const { container } = renderList();
    await waitFor(() =>
      expect(screen.getByText('Noch keine Läufe vorhanden.')).toBeInTheDocument(),
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
