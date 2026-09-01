/**
 * The pending step warns about a lost connection only when there is one. A
 * creator who takes more than 30 seconds to pick a name is not a connection
 * problem, so a timer-only banner must not tell joiners it is.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';

const wsStatus = { value: 'open' as 'open' | 'closed' };

const navigateMock = vi.fn();
vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => navigateMock,
}));
vi.mock('../../hooks/useWebSocket', () => ({
  useWebSocket: () => ({ status: wsStatus.value, lastError: null, send: vi.fn() }),
}));
vi.mock('../../api/teams', () => ({ getTeam: vi.fn() }));

import TeamNamePendingStep from './TeamNamePendingStep';
import { AuthContext } from '../../auth/AuthContext';
import { WizardStateProvider } from '../../wizard/WizardStateContext';
import { getTeam } from '../../api/teams';

const mockGetTeam = vi.mocked(getTeam);

const authValue = {
  user: {
    id: 'u1',
    email: 'a@example.com',
    display_name: 'A',
    role: 'participant' as const,
    organization_id: 'o1',
    organization_slug: 'slug',
  },
  isAuthenticated: true,
  isLoading: false,
  login: vi.fn(),
  logout: vi.fn(),
  checkAuth: vi.fn(),
  needsTwoFactorSetup: vi.fn().mockReturnValue(false),
};

const BANNER = /Status konnte nicht aktualisiert werden/;

function renderStep() {
  return render(
    <AuthContext.Provider value={authValue}>
      <WizardStateProvider>
        <MemoryRouter initialEntries={['/join/ABC123/team/pending']}>
          <Routes>
            <Route
              path="/join/:code/team/pending"
              element={<TeamNamePendingStep />}
            />
          </Routes>
        </MemoryRouter>
      </WizardStateProvider>
    </AuthContext.Provider>,
  );
}

describe('<TeamNamePendingStep> connection banner', () => {
  beforeEach(() => {
    mockGetTeam.mockReset();
    navigateMock.mockReset();
    sessionStorage.clear();
    // The step only queries with a team in wizard state; the provider reads it
    // from sessionStorage, the org slug comes from the authenticated user.
    sessionStorage.setItem(
      'clavis_wizard_team_state',
      JSON.stringify({ teamId: 'team-1', runId: 'run-1' }),
    );
    wsStatus.value = 'open';
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows no banner after 35 seconds while the socket is connected', async () => {
    mockGetTeam.mockRejectedValue(new Error('poll failed'));

    renderStep();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(35_000);
    });

    expect(screen.queryByText(BANNER)).not.toBeInTheDocument();
  });

  it('shows the banner when the socket is silent and a poll fails', async () => {
    wsStatus.value = 'closed';
    mockGetTeam.mockRejectedValue(new Error('poll failed'));

    renderStep();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(35_000);
    });

    expect(screen.getByText(BANNER)).toBeInTheDocument();
  });

  it('shows no banner when the socket is silent but the polls answer', async () => {
    mockGetTeam.mockResolvedValue({
      id: 'team-1',
      organization_id: 'o1',
      run_id: 'run-1',
      name: null,
      color: null,
      member_count: 1,
      share_code: 'SHARE01',
      members: [
        { id: 'u1', display_name: 'A', is_guest: false, joined_at: '2026-01-01T10:00:00Z' },
      ],
    });
    wsStatus.value = 'closed';

    renderStep();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(35_000);
    });

    expect(screen.queryByText(BANNER)).not.toBeInTheDocument();
  });

  // The wizard ends on the stations, not on the bare participant route whose index
  // redirect a first tap could be overtaken by.
  it('ends on the stations with replace once a poll brings the name', async () => {
    wsStatus.value = 'closed';
    mockGetTeam.mockResolvedValue({
      id: 'team-1',
      organization_id: 'o1',
      run_id: 'run-1',
      name: 'Rockets',
      color: null,
      member_count: 1,
      share_code: 'SHARE01',
      members: [
        { id: 'u1', display_name: 'A', is_guest: false, joined_at: '2026-01-01T10:00:00Z' },
      ],
    });

    renderStep();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(35_000);
    });

    expect(navigateMock).toHaveBeenCalledWith('/runs/run-1/participant/stations', {
      replace: true,
    });
  });
});
