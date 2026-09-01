/**
 * The invite step must create a team only when the server says this user has
 * none. Creating on every mount would give a creator who pressed the browser
 * back button a second team.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { StrictMode } from 'react';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useNavigate } from 'react-router';

vi.mock('../../services/publicRuns', () => ({ fetchMyTeam: vi.fn(), fetchByCode: vi.fn() }));
vi.mock('../../api/guest', () => ({ joinAsGuest: vi.fn() }));
vi.mock('../../api/teams', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/teams')>()),
  advanceTeamStage: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('../../components/ClavisCircularProgress', () => ({
  default: () => <div data-testid="loading-indicator" />,
}));

import InviteTeamStep from './InviteTeamStep';
import { AuthContext } from '../../auth/AuthContext';
import { WizardStateProvider } from '../../wizard/WizardStateContext';
import { joinAsGuest } from '../../api/guest';
import { ApiError } from '../../api/client';
import { fetchByCode, fetchMyTeam, type PublicRunMyTeam } from '../../services/publicRuns';

const mockFetchMyTeam = vi.mocked(fetchMyTeam);
const mockJoinAsGuest = vi.mocked(joinAsGuest);

const myTeam = (overrides: Partial<PublicRunMyTeam> = {}): PublicRunMyTeam => ({
  run_id: 'run-1',
  team_id: 'team-1',
  team_name: null,
  team_share_code: 'SHARE01',
  is_creator: true,
  ...overrides,
});

const createdTeam = {
  token: 'jwt',
  user_id: 'u1',
  display_name: 'A',
  team_id: 'team-new',
  team_name: null,
  team_share_code: 'NEW001',
  run_id: 'run-1',
};

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

function tree() {
  return (
    <AuthContext.Provider value={authValue}>
      <WizardStateProvider>
        <MemoryRouter initialEntries={['/join/ABC123/team/invite']}>
          <Routes>
            <Route path="/join/:code/team/invite" element={<InviteTeamStep />} />
          </Routes>
        </MemoryRouter>
      </WizardStateProvider>
    </AuthContext.Provider>
  );
}

describe('<InviteTeamStep> team bootstrap', () => {
  beforeEach(() => {
    mockFetchMyTeam.mockReset();
    mockJoinAsGuest.mockReset();
    sessionStorage.clear();
  });

  it('creates a team once when my-team answers 404', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    mockJoinAsGuest.mockResolvedValue(createdTeam);

    render(tree());

    await waitFor(() => expect(mockJoinAsGuest).toHaveBeenCalledTimes(1));
    expect(mockJoinAsGuest).toHaveBeenCalledWith(
      'ABC123',
      expect.objectContaining({ mode: 'create' }),
    );
    expect(await screen.findByText('NEW001')).toBeInTheDocument();
  });

  it('401 from my-team creates exactly one team and renders the QR code', async () => {
    // A brand-new guest reaches this step without a session cookie, so my-team
    // answers 401: they cannot be in a team yet, and the create call is what makes
    // the guest account and sets the cookie. The step must not show the retry state
    // forever.
    mockFetchMyTeam.mockResolvedValue({ kind: 'unauthenticated' });
    mockJoinAsGuest.mockResolvedValue(createdTeam);

    render(tree());

    await waitFor(() => expect(mockJoinAsGuest).toHaveBeenCalledTimes(1));
    expect(mockJoinAsGuest).toHaveBeenCalledWith(
      'ABC123',
      expect.objectContaining({ mode: 'create' }),
    );
    expect(
      await screen.findByRole('img', { name: 'QR code' }),
    ).toBeInTheDocument();
  });

  it('adopts the existing team instead of creating a second one', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'ok', data: myTeam() });

    render(tree());

    expect(await screen.findByText('SHARE01')).toBeInTheDocument();
    expect(mockJoinAsGuest).not.toHaveBeenCalled();
  });

  // A run of an archived organization and the ban after leaving are permanent.
  // The step names them instead of "Team konnte nicht erstellt werden. Bitte
  // versuche es erneut."
  it.each([
    [new ApiError(403, 'This organization is archived.', 'ORG_ARCHIVED'), /Organisation archiviert wurde/],
    [new ApiError(403, 'Du hast diesen Run verlassen und kannst ihm nicht erneut beitreten.', 'RUN_DEPARTED'), /nicht erneut beitreten\. Bitte wende dich/],
  ])('names a permanent refusal of the create (%s)', async (error, text) => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    mockJoinAsGuest.mockRejectedValue(error);

    render(tree());

    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByText(/Bitte versuche es erneut/)).not.toBeInTheDocument();
  });

  it('shows a retry state and creates nothing when the lookup answers 429', async () => {
    // fetchMyTeam normalises 429, a timeout and a network error to `error`.
    mockFetchMyTeam.mockResolvedValue({ kind: 'error' });

    render(tree());

    expect(
      await screen.findByRole('button', { name: 'Erneut versuchen' }),
    ).toBeInTheDocument();
    expect(mockJoinAsGuest).not.toHaveBeenCalled();
  });

  it('sends one create when the step mounts twice under StrictMode', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    mockJoinAsGuest.mockResolvedValue(createdTeam);

    // StrictMode mounts, unmounts and mounts again on the same instance —
    // the double-create this reproduces is exactly what a fast remount does
    // in production.
    render(<StrictMode>{tree()}</StrictMode>);

    await waitFor(() => expect(mockJoinAsGuest).toHaveBeenCalledTimes(1));
    expect(mockFetchMyTeam).toHaveBeenCalledTimes(1);
  });

  it('sends one create across a genuine unmount and remount while the first request is still in flight', async () => {
    // A real navigation-away-and-back throws the whole InviteTeamStep
    // instance away — any instance-scoped guard (a `useRef`) starts over as
    // `false` on the new instance. Only a guard that survives the component
    // can prevent this from firing a second create. `<WizardStateProvider>`
    // lives above `<Routes>` in App.tsx and stays mounted throughout a route
    // change, so it is kept outside the swapped subtree here too.
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    let resolveJoin!: (value: typeof createdTeam) => void;
    mockJoinAsGuest.mockReturnValue(
      new Promise((resolve) => {
        resolveJoin = resolve;
      }),
    );

    const shell = (child: React.ReactNode) => (
      <AuthContext.Provider value={authValue}>
        <WizardStateProvider>{child}</WizardStateProvider>
      </AuthContext.Provider>
    );
    const routed = (
      <MemoryRouter initialEntries={['/join/ABC123/team/invite']}>
        <Routes>
          <Route path="/join/:code/team/invite" element={<InviteTeamStep />} />
        </Routes>
      </MemoryRouter>
    );

    const view = render(shell(routed));
    await waitFor(() => expect(mockJoinAsGuest).toHaveBeenCalledTimes(1));

    // Unmount just the routed subtree (the step included), then remount it —
    // the wizard provider above never unmounts.
    view.rerender(shell(<div data-testid="elsewhere" />));
    view.rerender(shell(routed));

    // Give the remounted instance's effect a chance to run before the first
    // request resolves.
    await Promise.resolve();
    await Promise.resolve();

    resolveJoin(createdTeam);
    await screen.findByText('NEW001');

    expect(mockJoinAsGuest).toHaveBeenCalledTimes(1);
  });
});

// The QR placeholder must show a progress indicator while the team is created,
// but only once the wait outlasts a short delay, so a fast bootstrap never
// flashes a spinner.
describe('<InviteTeamStep> bootstrap loading indicator', () => {
  beforeEach(() => {
    mockFetchMyTeam.mockReset();
    mockJoinAsGuest.mockReset();
    sessionStorage.clear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows no indicator before the delay while the team is being created', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    mockJoinAsGuest.mockReturnValue(new Promise(() => {}));

    render(tree());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(screen.queryByTestId('loading-indicator')).not.toBeInTheDocument();
  });

  it('shows the indicator after the delay while the team is being created', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    mockJoinAsGuest.mockReturnValue(new Promise(() => {}));

    render(tree());

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });

    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
  });
});

// Once the creator moves on, the invite step must be gone from the history.
// Kept there, a browser back from the run would reopen "Lade dein Team ein".
describe('<InviteTeamStep> history', () => {
  beforeEach(() => {
    mockFetchMyTeam.mockReset();
    mockJoinAsGuest.mockReset();
    sessionStorage.clear();
  });

  const NameStep = () => {
    const navigate = useNavigate();
    return <button onClick={() => navigate(-1)}>browser back</button>;
  };

  it('replaces its history entry when the creator continues', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    mockJoinAsGuest.mockResolvedValue(createdTeam);

    render(
      <AuthContext.Provider value={authValue}>
        <WizardStateProvider>
          <MemoryRouter initialEntries={['/before', '/join/HIST01/team/invite']} initialIndex={1}>
            <Routes>
              <Route path="/before" element={<div>step before the invite</div>} />
              <Route path="/join/:code/team/invite" element={<InviteTeamStep />} />
              <Route path="/join/:code/team/name" element={<NameStep />} />
            </Routes>
          </MemoryRouter>
        </WizardStateProvider>
      </AuthContext.Provider>,
    );

    expect(await screen.findByText('NEW001')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Weiter' }));
    fireEvent.click(await screen.findByRole('button', { name: 'browser back' }));

    expect(await screen.findByText('step before the invite')).toBeInTheDocument();
    expect(screen.queryByText('NEW001')).toBeNull();
  });
});

// A refusal is its own page, it waits for a run that has not started, and a
// full run leads into an existing team.
describe('<InviteTeamStep> refusals', () => {
  const mockFetchByCode = vi.mocked(fetchByCode);

  beforeEach(() => {
    mockFetchMyTeam.mockReset();
    mockJoinAsGuest.mockReset();
    mockFetchByCode.mockReset();
    sessionStorage.clear();
  });
  afterEach(() => vi.useRealTimers());

  const renderAt = (code: string) =>
    render(
      <AuthContext.Provider value={authValue}>
        <WizardStateProvider>
          <MemoryRouter initialEntries={[`/join/${code}/team/invite`]}>
            <Routes>
              <Route path="/join/:code/team/invite" element={<InviteTeamStep />} />
              <Route path="/join/:code/team/join" element={<div>join an existing team step</div>} />
            </Routes>
          </MemoryRouter>
        </WizardStateProvider>
      </AuthContext.Provider>,
    );

  it('shows a run that has not started as its own page, without the QR placeholder', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    mockJoinAsGuest.mockRejectedValue(new ApiError(422, 'Run has not started yet.', 'RUN_NOT_STARTED'));
    mockFetchByCode.mockResolvedValue(null);

    renderAt('WAIT01');

    expect(await screen.findByRole('heading', { name: 'Der Lauf hat noch nicht begonnen' })).toBeInTheDocument();
    expect(screen.getByText(/Bleib auf dieser Seite/)).toBeInTheDocument();
    expect(screen.queryByText('------')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Weiter' })).toBeNull();
  });

  it('creates the team by itself once the run has started', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    mockJoinAsGuest.mockRejectedValueOnce(new ApiError(422, 'Run has not started yet.', 'RUN_NOT_STARTED'));
    mockJoinAsGuest.mockResolvedValue(createdTeam);
    mockFetchByCode.mockResolvedValue({
      run_id: 'run-1',
      rallye_name: 'Lauf',
      organization_slug: 'slug',
      state: 'active',
    });

    renderAt('WAIT02');
    expect(await screen.findByRole('heading', { name: 'Der Lauf hat noch nicht begonnen' })).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(36_000);
    });

    expect(await screen.findByText('NEW001')).toBeInTheDocument();
    expect(mockJoinAsGuest).toHaveBeenCalledTimes(2);
  });

  it('leads from "all teams are formed" into the step for an existing team', async () => {
    mockFetchMyTeam.mockResolvedValue({ kind: 'not-found' });
    mockJoinAsGuest.mockRejectedValue(new ApiError(422, 'Team limit reached.', 'TEAM_LIMIT_REACHED'));

    renderAt('FULL01');

    expect(await screen.findByRole('heading', { name: 'Alle Teams sind gebildet' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Bestehendem Team beitreten' }));
    expect(await screen.findByText('join an existing team step')).toBeInTheDocument();
  });
});
