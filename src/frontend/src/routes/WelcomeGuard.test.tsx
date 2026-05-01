import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';

vi.mock('../services/publicRuns', () => ({ fetchMyTeam: vi.fn(), fetchByCode: vi.fn() }));
vi.mock('../components/ClavisCircularProgress', () => ({
  default: () => <div data-testid="loading-indicator" />,
}));

import WelcomeGuard, { getNextStep } from './WelcomeGuard';
import { AuthContext } from '../auth/AuthContext';
import { fetchByCode, fetchMyTeam, type PublicRunMyTeam } from '../services/publicRuns';

const mockFetchMyTeam = vi.mocked(fetchMyTeam);
const mockFetchByCode = vi.mocked(fetchByCode);

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
            path="/"
            element={
              <WelcomeGuard>
                <div data-testid="welcome">welcome</div>
              </WelcomeGuard>
            }
          />
          <Route
            path="/join/:code"
            element={
              <WelcomeGuard>
                <div data-testid="welcome-join">welcome-join</div>
              </WelcomeGuard>
            }
          />
          <Route path="/dashboard" element={<div data-testid="dashboard">dashboard</div>} />
          <Route
            path="/join/:code/profile"
            element={<div data-testid="join-profile">profile</div>}
          />
          <Route
            path="/join/:code/team/invite"
            element={<div data-testid="team-invite">invite</div>}
          />
          <Route
            path="/join/:code/team/waiting"
            element={<div data-testid="team-waiting">waiting</div>}
          />
        </Routes>
      </MemoryRouter>,
    ),
  );
}

describe('<WelcomeGuard>', () => {
  beforeEach(() => {
    mockFetchMyTeam.mockReset();
    mockFetchByCode.mockReset();
    // Default: an active run of an active organization.
    mockFetchByCode.mockResolvedValue({
      run_id: 'run-1',
      rallye_name: 'Rallye',
      organization_slug: 'slug',
    });
    // Default: the user has no team in this run, so the guard keeps the
    // normal Welcome → ProfileSetup path.
    mockFetchMyTeam.mockResolvedValue({
      kind: 'ok',
      data: myTeam({ team_id: null, team_share_code: null }),
    });
    sessionStorage.clear();
  });

  it('redirects authenticated users on `/` to /dashboard', async () => {
    renderGuard('/', { isAuthenticated: true });
    await waitFor(() =>
      expect(screen.getByTestId('dashboard')).toBeInTheDocument(),
    );
  });

  it('redirects authenticated users on /join/:code to the profile-setup step', async () => {
    renderGuard('/join/ABC123', { isAuthenticated: true });
    await waitFor(() =>
      expect(screen.getByTestId('join-profile')).toBeInTheDocument(),
    );
  });

  // The browser-back chain starts here, so the team check
  // has to happen before the redirect into profile-setup.
  it('sends the creator of an unnamed team to the invite step', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'ok', data: myTeam({ is_creator: true }) });
    renderGuard('/join/ABC123', { isAuthenticated: true });
    await waitFor(() =>
      expect(screen.getByTestId('team-invite')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('join-profile')).not.toBeInTheDocument();
  });

  it('sends a joiner in an unnamed team to the waiting step', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'ok', data: myTeam({ is_creator: false }) });
    renderGuard('/join/ABC123', { isAuthenticated: true });
    await waitFor(() =>
      expect(screen.getByTestId('team-waiting')).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('join-profile')).not.toBeInTheDocument();
  });

  it('keeps the profile-setup path for a member of a named team', async () => {
    mockFetchMyTeam.mockResolvedValue({
      kind: 'ok',
      data: myTeam({ team_name: 'Schnelle Füchse', is_creator: true }),
    });
    renderGuard('/join/ABC123', { isAuthenticated: true });
    await waitFor(() =>
      expect(screen.getByTestId('join-profile')).toBeInTheDocument(),
    );
  });

  // A signed-in visitor gets the refusal too, before any redirect.
  describe('a run of an archived organization', () => {
    const MESSAGE =
      'Dieser Lauf ist nicht mehr verfügbar, weil seine Organisation archiviert wurde.';
    beforeEach(() => {
      mockFetchByCode.mockImplementation(async (_code, _signal, refusal) => {
        refusal?.onOrgArchived?.();
        return null;
      });
      // The team lookup answers 404 for such a run.
      mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    });

    it('says so instead of redirecting a signed-in visitor who opens the link', async () => {
      renderGuard('/join/ARCH01', { isAuthenticated: true });
      expect(await screen.findByText(MESSAGE)).toBeInTheDocument();
      expect(screen.queryByTestId('join-profile')).not.toBeInTheDocument();
      expect(screen.queryByTestId('welcome-join')).not.toBeInTheDocument();
    });

    it('says so for a code typed on the entry page, which lands on the same route', async () => {
      renderGuard('/join/ARCH01?lang=de', { isAuthenticated: true });
      expect(await screen.findByText(MESSAGE)).toBeInTheDocument();
      expect(screen.queryByTestId('join-profile')).not.toBeInTheDocument();
    });

    it('keeps the redirect for an unknown code', async () => {
      mockFetchByCode.mockResolvedValue(null);
      renderGuard('/join/NOPE99', { isAuthenticated: true });
      expect(await screen.findByTestId('join-profile')).toBeInTheDocument();
    });
  });

  it('asks my-team nothing for an unauthenticated visitor', () => {
    renderGuard('/join/ABC123', { isAuthenticated: false });
    expect(mockFetchMyTeam).not.toHaveBeenCalled();
  });

  it('renders Welcome for unauthenticated users on `/`', () => {
    renderGuard('/', { isAuthenticated: false });
    expect(screen.getByTestId('welcome')).toBeInTheDocument();
  });

  it('renders Welcome for unauthenticated users on /join/:code', () => {
    renderGuard('/join/ABC', { isAuthenticated: false });
    expect(screen.getByTestId('welcome-join')).toBeInTheDocument();
  });

  it('renders children while auth is still loading (avoids flicker)', () => {
    renderGuard('/', { isAuthenticated: false, isLoading: true });
    expect(screen.getByTestId('welcome')).toBeInTheDocument();
  });
});

describe('getNextStep helper', () => {
  it('returns /join/:code/profile', () => {
    expect(getNextStep('XYZ')).toBe('/join/XYZ/profile');
  });
});

// A slow my-team lookup must show a progress indicator,
// but only once the wait outlasts a short delay, so a fast connection never
// flashes a spinner.
describe('<WelcomeGuard> loading indicator', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows no indicator before the delay while my-team is still resolving', async () => {
    mockFetchMyTeam.mockReturnValue(new Promise(() => {}));
    renderGuard('/join/ABC123', { isAuthenticated: true });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument();
  });

  it('shows the indicator after the delay while my-team is still resolving', async () => {
    mockFetchMyTeam.mockReturnValue(new Promise(() => {}));
    renderGuard('/join/ABC123', { isAuthenticated: true });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
  });
});
