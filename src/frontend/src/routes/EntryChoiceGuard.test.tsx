import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';

vi.mock('../services/publicRuns', () => ({ fetchMyTeam: vi.fn() }));
vi.mock('../components/ClavisCircularProgress', () => ({
  default: () => <div data-testid="loading-indicator" />,
}));

import EntryChoiceGuard from './EntryChoiceGuard';
import { AuthContext } from '../auth/AuthContext';
import { fetchMyTeam, type PublicRunMyTeam } from '../services/publicRuns';

const mockFetchMyTeam = vi.mocked(fetchMyTeam);

const myTeam = (overrides: Partial<PublicRunMyTeam> = {}): PublicRunMyTeam => ({
  run_id: 'run-1',
  team_id: 'team-1',
  team_name: null,
  team_share_code: 'SHARE01',
  is_creator: false,
  ...overrides,
});

interface AuthState {
  isAuthenticated: boolean;
  isLoading?: boolean;
}

function withAuth(state: AuthState, children: React.ReactNode) {
  const value = {
    user: state.isAuthenticated
      ? {
          id: 'u1',
          email: 'a@example.com',
          display_name: 'A',
          role: 'participant' as const,
          organization_id: 'o1',
          organization_slug: 'slug',
        }
      : null,
    isAuthenticated: state.isAuthenticated,
    isLoading: state.isLoading ?? false,
    login: vi.fn(),
    logout: vi.fn(),
    checkAuth: vi.fn(),
    needsTwoFactorSetup: vi.fn().mockReturnValue(false),
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

function renderGuard(path: string, auth: AuthState) {
  return render(
    withAuth(
      auth,
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/auth-choice"
            element={
              <EntryChoiceGuard>
                <div data-testid="auth-choice">auth-choice</div>
              </EntryChoiceGuard>
            }
          />
          <Route
            path="/join/:code/auth-choice"
            element={
              <EntryChoiceGuard>
                <div data-testid="auth-choice-join">auth-choice-join</div>
              </EntryChoiceGuard>
            }
          />
          <Route path="/dashboard" element={<div data-testid="dashboard">dashboard</div>} />
          <Route
            path="/join/:code/team"
            element={<div data-testid="team">team</div>}
          />
          <Route
            path="/join/:code/team/invite"
            element={<div data-testid="team-invite">invite</div>}
          />
          <Route
            path="/join/:code/team/waiting"
            element={<div data-testid="team-waiting">waiting</div>}
          />
          <Route
            path="/runs/:runId/participant/stations"
            element={<div data-testid="stations">stations</div>}
          />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

describe('<EntryChoiceGuard>', () => {
  beforeEach(() => {
    mockFetchMyTeam.mockReset();
    mockFetchMyTeam.mockResolvedValue({
      kind: 'ok',
      data: myTeam({ team_id: null, team_share_code: null }),
    });
    sessionStorage.clear();
  });

  it('renders children for unauthenticated users', () => {
    renderGuard('/auth-choice', { isAuthenticated: false });
    expect(screen.getByTestId('auth-choice')).toBeInTheDocument();
  });

  it('renders children while auth is still loading (avoids flicker)', () => {
    renderGuard('/auth-choice', { isAuthenticated: false, isLoading: true });
    expect(screen.getByTestId('auth-choice')).toBeInTheDocument();
  });

  it('redirects an authenticated user with no run code to /dashboard', async () => {
    renderGuard('/auth-choice', { isAuthenticated: true });
    await waitFor(() =>
      expect(screen.getByTestId('dashboard')).toBeInTheDocument(),
    );
  });

  it('sends a member of a fully-named team straight into the run', async () => {
    mockFetchMyTeam.mockResolvedValue({
      kind: 'ok',
      data: myTeam({ team_name: 'Schnelle Füchse' }),
    });
    renderGuard('/join/ABC123/auth-choice', { isAuthenticated: true });
    await waitFor(() =>
      expect(screen.getByTestId('stations')).toBeInTheDocument(),
    );
  });

  // A member of a still-unnamed team must not see the
  // entry-choice screen again.
  it('sends the creator of an unnamed team to the invite step', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'ok', data: myTeam({ is_creator: true }) });
    renderGuard('/join/ABC123/auth-choice', { isAuthenticated: true });
    await waitFor(() =>
      expect(screen.getByTestId('team-invite')).toBeInTheDocument(),
    );
  });

  it('sends a joiner in an unnamed team to the waiting step', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'ok', data: myTeam({ is_creator: false }) });
    renderGuard('/join/ABC123/auth-choice', { isAuthenticated: true });
    await waitFor(() =>
      expect(screen.getByTestId('team-waiting')).toBeInTheDocument(),
    );
  });

  it('falls back to the team flow for an authenticated user with no team', async () => {
    renderGuard('/join/ABC123/auth-choice', { isAuthenticated: true });
    await waitFor(() => expect(screen.getByTestId('team')).toBeInTheDocument());
  });
});

// A slow my-team lookup must show a progress indicator,
// but only once the wait outlasts a short delay, so a fast connection never
// flashes a spinner.
describe('<EntryChoiceGuard> loading indicator', () => {
  beforeEach(() => {
    mockFetchMyTeam.mockReset();
    sessionStorage.clear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows no indicator before the delay while my-team is still resolving', async () => {
    mockFetchMyTeam.mockReturnValue(new Promise(() => {}));
    renderGuard('/join/ABC123/auth-choice', { isAuthenticated: true });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument();
  });

  it('shows the indicator after the delay while my-team is still resolving', async () => {
    mockFetchMyTeam.mockReturnValue(new Promise(() => {}));
    renderGuard('/join/ABC123/auth-choice', { isAuthenticated: true });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
  });
});
