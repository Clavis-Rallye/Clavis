/**
 * The "create or join" choice is the one screen a member of an existing team
 * must never reach. A browser back lands here, so the step itself checks my-team
 * and forwards: the creator to their invite screen, a joiner to the waiting
 * screen. Only a user with no team sees the choice.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';

vi.mock('../../services/publicRuns', () => ({ fetchMyTeam: vi.fn() }));
// Same stub pattern as LazyRoute.test.tsx, extended with the accessible
// name the real component puts on the host element.
vi.mock('../../components/ClavisCircularProgress', () => ({
  default: ({ ariaLabel }: { ariaLabel?: string }) => (
    <div role="progressbar" aria-label={ariaLabel} data-testid="loading-indicator" />
  ),
}));

import FindTeamStep from './FindTeamStep';
import { AuthContext } from '../../auth/AuthContext';
import { WizardStateProvider } from '../../wizard/WizardStateContext';
import { fetchMyTeam, type PublicRunMyTeam } from '../../services/publicRuns';

const mockFetchMyTeam = vi.mocked(fetchMyTeam);

const myTeam = (overrides: Partial<PublicRunMyTeam> = {}): PublicRunMyTeam => ({
  run_id: 'run-1',
  team_id: 'team-1',
  team_name: null,
  team_share_code: 'SHARE01',
  is_creator: false,
  ...overrides,
});

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

function renderStep() {
  return render(
    <AuthContext.Provider value={authValue}>
      <WizardStateProvider>
        <MemoryRouter initialEntries={['/join/ABC123/team/find']}>
          <Routes>
            <Route path="/join/:code/team/find" element={<FindTeamStep />} />
            <Route
              path="/join/:code/team/invite"
              element={<div data-testid="invite">invite</div>}
            />
            <Route
              path="/join/:code/team/waiting"
              element={<div data-testid="waiting">waiting</div>}
            />
            <Route
              path="/runs/:runId/participant/stations"
              element={<div data-testid="stations">stations</div>}
            />
          </Routes>
        </MemoryRouter>
      </WizardStateProvider>
    </AuthContext.Provider>,
  );
}

describe('<FindTeamStep> destination routing', () => {
  beforeEach(() => {
    mockFetchMyTeam.mockReset();
    sessionStorage.clear();
  });

  it('sends the creator of an unnamed team to the invite step', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'ok', data: myTeam({ is_creator: true }) });

    renderStep();

    expect(await screen.findByTestId('invite')).toBeInTheDocument();
    expect(screen.queryByText('Finde dein Team')).not.toBeInTheDocument();
  });

  it('sends a joiner in an unnamed team to the waiting step', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'ok', data: myTeam({ is_creator: false }) });

    renderStep();

    expect(await screen.findByTestId('waiting')).toBeInTheDocument();
    expect(screen.queryByText('Finde dein Team')).not.toBeInTheDocument();
  });

  it('shows the choice to a user who has no team yet', async () => {
    mockFetchMyTeam.mockResolvedValue({
      kind: 'ok',
      data: myTeam({ team_id: null, team_share_code: null }),
    });

    renderStep();

    expect(await screen.findByText('Finde dein Team')).toBeInTheDocument();
    expect(screen.queryByTestId('invite')).not.toBeInTheDocument();
    expect(screen.queryByTestId('waiting')).not.toBeInTheDocument();
  });

  it('sends a member of a named team straight into the run', async () => {
    mockFetchMyTeam.mockResolvedValue({
      kind: 'ok',
      data: myTeam({ team_name: 'Schnelle Füchse', is_creator: true }),
    });

    renderStep();

    expect(await screen.findByTestId('stations')).toBeInTheDocument();
  });

  it('shows nothing while my-team is still answering', async () => {
    mockFetchMyTeam.mockReturnValue(new Promise(() => {}));

    renderStep();

    await waitFor(() => expect(mockFetchMyTeam).toHaveBeenCalled());
    expect(screen.queryByText('Finde dein Team')).not.toBeInTheDocument();
    expect(screen.queryByTestId('invite')).not.toBeInTheDocument();
  });
});

// A slow my-team lookup must show a progress indicator, but only once the wait
// outlasts a short delay, so a fast connection never flashes a spinner.
describe('<FindTeamStep> loading indicator', () => {
  beforeEach(() => {
    mockFetchMyTeam.mockReset();
    sessionStorage.clear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows no indicator before the delay while my-team is still answering', async () => {
    mockFetchMyTeam.mockReturnValue(new Promise(() => {}));

    renderStep();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument();
  });

  it('shows the indicator after the delay while my-team is still answering', async () => {
    mockFetchMyTeam.mockReturnValue(new Promise(() => {}));

    renderStep();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
  });

  // The spinner says what loads, so a screen reader hears more than a bare
  // "progress bar" (no aria-progressbar-name violation).
  it('names the spinner for the destination while my-team is still answering', async () => {
    mockFetchMyTeam.mockReturnValue(new Promise(() => {}));

    renderStep();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(
      screen.getByRole('progressbar', { name: 'Teamziel wird geladen' }),
    ).toBeInTheDocument();
  });
});
