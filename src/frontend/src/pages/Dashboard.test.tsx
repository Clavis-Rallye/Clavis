import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import Dashboard from './Dashboard';
import { AuthContext } from '../auth/AuthContext';
import apiClient from '../api/client';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

const makeUser = (role: 'org_admin' | 'coordinator' | 'participant' | 'guest', id = 'u1') => ({
  id,
  email: 'user@example.com',
  display_name: 'Test User',
  role,
  organization_id: 'org-1',
  organization_slug: 'test-org',
});

const orgAdminApiResponses = () => {
  // Dashboard uses Promise.allSettled([listUsers, listRallyes, listRuns, listLogs])
  // listUsers → GET /api/orgs/test-org/users/
  // listRallyes → GET /api/orgs/test-org/rallyes
  // listRuns → GET /api/orgs/test-org/runs
  // listLogs → GET /api/orgs/test-org/admin/logs
  vi.mocked(apiClient.get)
    .mockResolvedValueOnce({ data: { items: [], total: 5, skip: 0, limit: 1 } })   // users
    .mockResolvedValueOnce({ data: { items: [], total: 3, skip: 0, limit: 100 } }) // rallyes
    .mockResolvedValueOnce({ data: { items: [], total: 7, skip: 0, limit: 100 } }) // runs
    .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 5 } });  // logs
};

const coordinatorApiResponses = () => {
  // listRuns, then listSubmissions + listScores per active run
  vi.mocked(apiClient.get).mockResolvedValueOnce({
    data: {
      items: [
        {
          id: 'run-1',
          organization_id: 'org-1',
          rallye_id: 'r1',
          name: 'Aktiver Lauf',
          state: 'active',
          join_code: 'AABBCC',
          scheduled_for: null,
          started_at: null,
          paused_at: null,
          ended_at: null,
          settings: {},
          max_teams: null,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        },
      ],
      total: 1,
      skip: 0,
      limit: 100,
    },
  })
    .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } }) // submissions for run-1
    .mockResolvedValueOnce({ data: { items: [], total: 0, skip: 0, limit: 100 } }); // scores for run-1
};

const renderDashboard = (role: 'org_admin' | 'coordinator' | 'participant' | 'guest', userId = 'u1') =>
  render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <AuthContext.Provider
        value={{
          user: makeUser(role, userId),
          isAuthenticated: true,
          isLoading: false,
          login: vi.fn(),
          logout: vi.fn(),
          checkAuth: vi.fn(),
          needsTwoFactorSetup: () => false,
        }}
      >
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/admin/logs" element={<div>Log Viewer</div>} />
          <Route path="/runs/:runId" element={<div>Run detail</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('Dashboard', () => {
  beforeEach(() => vi.resetAllMocks());

  it('org_admin sees stat cards (Personen, Rallyes, Läufe)', async () => {
    orgAdminApiResponses();
    renderDashboard('org_admin');
    await waitFor(() => expect(screen.getByText('Personen')).toBeInTheDocument());
    expect(screen.getByText('Rallyes')).toBeInTheDocument();
    expect(screen.getByText('Läufe')).toBeInTheDocument();
  });

  it('org_admin sees recent audit events panel', async () => {
    orgAdminApiResponses();
    renderDashboard('org_admin');
    await waitFor(() =>
      expect(screen.getByText('Aktuelle Audit-Ereignisse')).toBeInTheDocument(),
    );
  });

  it('shows a readable label for a known audit event and keeps the raw key', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: { items: [], total: 5, skip: 0, limit: 1 } })
      .mockResolvedValueOnce({ data: { items: [], total: 3, skip: 0, limit: 100 } })
      .mockResolvedValueOnce({ data: { items: [], total: 7, skip: 0, limit: 100 } })
      .mockResolvedValueOnce({
        data: {
          items: [
            { timestamp: '2026-02-01T14:30:45Z', event: 'user_login', category: 'audit' },
            { timestamp: '2026-02-01T14:31:45Z', event: 'run.started', category: 'audit' },
          ],
          total: 2,
          skip: 0,
          limit: 5,
        },
      });
    renderDashboard('org_admin');

    const login = await screen.findByText('Anmeldung');
    expect(login).toHaveAttribute('title', 'user_login');

    const runStarted = screen.getByText('Lauf gestartet');
    expect(runStarted).toHaveAttribute('title', 'run.started');
  });

  // Both keys need a readable label. One of them holds a dot, like the run events.
  it('names the events user_deactivated and demo_content.loaded', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: { items: [], total: 5, skip: 0, limit: 1 } })
      .mockResolvedValueOnce({ data: { items: [], total: 3, skip: 0, limit: 100 } })
      .mockResolvedValueOnce({ data: { items: [], total: 7, skip: 0, limit: 100 } })
      .mockResolvedValueOnce({
        data: {
          items: [
            { timestamp: '2026-02-01T14:30:45Z', event: 'user_deactivated', category: 'audit' },
            { timestamp: '2026-02-01T14:31:45Z', event: 'demo_content.loaded', category: 'audit' },
          ],
          total: 2,
          skip: 0,
          limit: 5,
        },
      });
    renderDashboard('org_admin');

    expect(await screen.findByText('Person deaktiviert')).toHaveAttribute('title', 'user_deactivated');
    expect(screen.getByText('Demo-Inhalte geladen')).toHaveAttribute('title', 'demo_content.loaded');
    expect(screen.queryByText('user_deactivated')).toBeNull();
    expect(screen.queryByText('demo_content.loaded')).toBeNull();
  });

  it('falls back to the raw key for an unknown audit event', async () => {
    vi.mocked(apiClient.get)
      .mockResolvedValueOnce({ data: { items: [], total: 5, skip: 0, limit: 1 } })
      .mockResolvedValueOnce({ data: { items: [], total: 3, skip: 0, limit: 100 } })
      .mockResolvedValueOnce({ data: { items: [], total: 7, skip: 0, limit: 100 } })
      .mockResolvedValueOnce({
        data: {
          items: [
            { timestamp: '2026-02-01T14:30:45Z', event: 'something_new', category: 'audit' },
          ],
          total: 1,
          skip: 0,
          limit: 5,
        },
      });
    renderDashboard('org_admin');

    const fallback = await screen.findByText('something_new');
    expect(fallback).toHaveAttribute('title', 'something_new');
  });

  it('coordinator sees active-runs panel', async () => {
    coordinatorApiResponses();
    renderDashboard('coordinator');
    await waitFor(() => expect(screen.getByText('Aktive Läufe')).toBeInTheDocument());
  });

  it('run row shows the translated state and opens the run on click', async () => {
    coordinatorApiResponses();
    renderDashboard('coordinator');
    await waitFor(() => expect(screen.getByText('Aktiver Lauf')).toBeInTheDocument());
    expect(screen.getByText('Aktiv')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /Aktiver Lauf/ }));
    await waitFor(() => expect(screen.getByText('Run detail')).toBeInTheDocument());
  });

  it('coordinator sees the "Zu den Läufen" button in the header', async () => {
    coordinatorApiResponses();
    renderDashboard('coordinator');
    expect(screen.getByRole('button', { name: 'Zu den Läufen' })).toBeInTheDocument();
  });

  it('org_admin sees the "Zu den Läufen" button in the header', async () => {
    orgAdminApiResponses();
    renderDashboard('org_admin');
    expect(screen.getByRole('button', { name: 'Zu den Läufen' })).toBeInTheDocument();
  });

  it('participant does not see the "Zu den Läufen" button', () => {
    renderDashboard('participant');
    expect(screen.queryByRole('button', { name: 'Zu den Läufen' })).not.toBeInTheDocument();
  });

  it('coordinator sees unscored-submissions card', async () => {
    coordinatorApiResponses();
    renderDashboard('coordinator');
    await waitFor(() =>
      expect(screen.getByText('Benötigt Aufmerksamkeit')).toBeInTheDocument(),
    );
  });

  it('participant sees default placeholder cards', () => {
    renderDashboard('participant');
    expect(screen.getByText('Deine Rallyes')).toBeInTheDocument();
    expect(screen.getByText('Aktive Läufe')).toBeInTheDocument();
    expect(screen.getByText('Teams')).toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    orgAdminApiResponses();
    const { container } = renderDashboard('org_admin');
    await waitFor(() => expect(screen.getByText('Personen')).toBeInTheDocument());
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The dashboard is the first page after the sign-in. A person who took over the
  // administration reads there once which accounts that deactivated.
  // The people page keeps the notice.
  describe('takeover notice', () => {
    const notice = {
      accepted_at: '2026-11-18T10:00:00Z',
      replaced: [
        { id: 'a1', email: 'weg@example.com', display_name: 'Ausgeschieden', is_active: false, last_login: null },
      ],
    };
    const text =
      'Du hast die Verwaltung übernommen. Diese Konten sind jetzt deaktiviert: Ausgeschieden (weg@example.com).';
    const mockWithTakeover = (data: unknown) =>
      vi.mocked(apiClient.get).mockImplementation((url: string) =>
        Promise.resolve({
          data: url.endsWith('/invitations/takeover') ? data : { items: [], total: 0, skip: 0, limit: 100 },
        }),
      );

    beforeEach(() => window.localStorage.clear());

    it('shows the deactivated accounts on the first visit and not on the next', async () => {
      mockWithTakeover(notice);
      const first = renderDashboard('org_admin');
      expect(await screen.findByText(text)).toBeInTheDocument();
      first.unmount();

      renderDashboard('org_admin');
      await waitFor(() =>
        expect(
          vi.mocked(apiClient.get).mock.calls.filter(([url]) => String(url).endsWith('/invitations/takeover')),
        ).toHaveLength(2),
      );
      await screen.findByText('Personen');
      expect(screen.queryByText(text)).toBeNull();
    });

    // Two admins in one browser: what B has seen must not make A's notice new again.
    it('keeps the notice seen for account A after account B saw its own', async () => {
      const takeoverCalls = () =>
        vi.mocked(apiClient.get).mock.calls.filter(([url]) => String(url).endsWith('/invitations/takeover'));
      mockWithTakeover(notice);

      const a1 = renderDashboard('org_admin', 'admin-a');
      expect(await screen.findByText(text)).toBeInTheDocument();
      a1.unmount();

      const b = renderDashboard('org_admin', 'admin-b');
      expect(await screen.findByText(text)).toBeInTheDocument();
      b.unmount();

      renderDashboard('org_admin', 'admin-a');
      await waitFor(() => expect(takeoverCalls()).toHaveLength(3));
      await screen.findByText('Personen');
      expect(screen.queryByText(text)).toBeNull();
    });

    it('shows a later takeover of the same account again', async () => {
      mockWithTakeover(notice);
      const first = renderDashboard('org_admin');
      expect(await screen.findByText(text)).toBeInTheDocument();
      first.unmount();

      mockWithTakeover({ ...notice, accepted_at: '2026-12-01T09:00:00Z' });
      renderDashboard('org_admin');
      expect(await screen.findByText(text)).toBeInTheDocument();
    });

    it('shows nothing when nobody was replaced', async () => {
      mockWithTakeover({ accepted_at: null, replaced: [] });
      renderDashboard('org_admin');
      await screen.findByText('Personen');
      expect(screen.queryByText(/Verwaltung übernommen/)).toBeNull();
    });

    it('does not ask for a coordinator', async () => {
      mockWithTakeover(notice);
      renderDashboard('coordinator');
      await waitFor(() => expect(apiClient.get).toHaveBeenCalled());
      expect(
        vi.mocked(apiClient.get).mock.calls.some(([url]) => String(url).endsWith('/invitations/takeover')),
      ).toBe(false);
      expect(screen.queryByText(text)).toBeNull();
    });
  });
});
