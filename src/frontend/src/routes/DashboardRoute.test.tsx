import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import DashboardRoute from './DashboardRoute';
import { AuthContext } from '../auth/AuthContext';
import { getMyRunHistory } from '../api/me';

vi.mock('../api/me', () => ({ getMyRunHistory: vi.fn() }));
vi.mock('../pages/Dashboard', () => ({ default: () => <div>staff-dashboard</div> }));

const Where = () => <div>at:{useLocation().pathname}</div>;

const entry = (state: string, runId: string, teamId: string) => ({
  run_id: runId,
  run_name: 'Lauf',
  rallye_title: {},
  team_id: teamId,
  team_name: 'Team',
  started_at: null,
  ended_at: null,
  state,
  placement: null,
  total_teams: null,
  total_points: 0,
});

const renderAs = (role: 'participant' | 'guest' | 'coordinator') =>
  render(
    <AuthContext.Provider
      value={{
        user: {
          id: 'u1',
          email: role === 'guest' ? null : 'p@example.com',
          display_name: 'P',
          role,
          organization_id: 'o1',
          organization_slug: 'org',
        },
        isAuthenticated: true,
        isLoading: false,
        login: vi.fn(),
        logout: vi.fn(),
        checkAuth: vi.fn(),
        needsTwoFactorSetup: () => false,
      }}
    >
      <MemoryRouter initialEntries={['/dashboard']}>
        <Routes>
          <Route path="/dashboard" element={<DashboardRoute />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );

/**
 * A participant who signed in, registered or opened an unknown route must not
 * land on the staff dashboard, also while their team is in a running run.
 */
describe('DashboardRoute', () => {
  beforeEach(() => {
    vi.mocked(getMyRunHistory).mockReset();
    sessionStorage.clear();
  });

  it('sends a participant whose team plays into that run, with the team for the shell', async () => {
    vi.mocked(getMyRunHistory).mockResolvedValueOnce({
      items: [entry('ended', 'run-old', 'team-old'), entry('active', 'run-1', 'team-1')],
    } as never);
    renderAs('participant');

    expect(await screen.findByText('at:/runs/run-1/participant/stations')).toBeInTheDocument();
    expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-1');
  });

  it('counts a paused run as still playing', async () => {
    vi.mocked(getMyRunHistory).mockResolvedValueOnce({ items: [entry('paused', 'run-2', 'team-2')] } as never);
    renderAs('guest');

    expect(await screen.findByText('at:/runs/run-2/participant/stations')).toBeInTheDocument();
  });

  // An ended run is a history to look at, not "Noch in keinem Lauf".
  it('sends a participant whose runs have all ended to the run history', async () => {
    vi.mocked(getMyRunHistory).mockResolvedValueOnce({ items: [entry('ended', 'run-old', 'team-old')] } as never);
    renderAs('participant');

    expect(await screen.findByText('at:/run-history')).toBeInTheDocument();
    expect(screen.queryByText('staff-dashboard')).not.toBeInTheDocument();
  });

  // A membership in a run that has not started is no history to look at.
  it('sends a participant whose only run has not started to /no-runs', async () => {
    vi.mocked(getMyRunHistory).mockResolvedValueOnce({ items: [entry('configured', 'run-new', 'team-new')] } as never);
    renderAs('participant');

    expect(await screen.findByText('at:/no-runs')).toBeInTheDocument();
  });

  it('sends a participant who was never in a run to /no-runs', async () => {
    vi.mocked(getMyRunHistory).mockResolvedValueOnce({ items: [] } as never);
    renderAs('participant');

    expect(await screen.findByText('at:/no-runs')).toBeInTheDocument();
  });

  it('falls back to /no-runs when the history cannot be read', async () => {
    vi.mocked(getMyRunHistory).mockRejectedValueOnce(new Error('offline'));
    renderAs('participant');

    expect(await screen.findByText('at:/no-runs')).toBeInTheDocument();
  });

  it('keeps the staff dashboard for staff', async () => {
    renderAs('coordinator');

    expect(await screen.findByText('staff-dashboard')).toBeInTheDocument();
    expect(getMyRunHistory).not.toHaveBeenCalled();
  });
});
