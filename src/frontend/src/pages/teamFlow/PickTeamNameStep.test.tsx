/**
 * The pick-name step must not offer a suggestion that is already taken in this
 * run, and a TEAM_NAME_TAKEN answer from the PATCH gets its own message instead
 * of the generic "forbidden" one.
 *
 * The suggestions lookup (`listTeams`) is asynchronous, so the block is held
 * back until it resolves: otherwise a taken name (or the whole chip row, when
 * every suggestion is taken) would flash before the filter applies. The lookup
 * also always includes the run's own team, the one this very step is naming.
 * Its `name` is still null at this point in the flow, even though the API type
 * claims `string`. That must not blow up the filter.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';

const navigateMock = vi.fn();
vi.mock('react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router')>()),
  useNavigate: () => navigateMock,
}));
vi.mock('../../api/teams', () => ({ getTeam: vi.fn(), patchTeamName: vi.fn() }));
vi.mock('../../api/runs', () => ({ listTeams: vi.fn() }));

import PickTeamNameStep from './PickTeamNameStep';
import { AuthContext } from '../../auth/AuthContext';
import { WizardStateProvider } from '../../wizard/WizardStateContext';
import { ApiError } from '../../api/client';
import { getTeam, patchTeamName } from '../../api/teams';
import { listTeams } from '../../api/runs';
import type { Team, PaginatedList } from '../../api/types';
import i18n from '../../i18n';

const mockGetTeam = vi.mocked(getTeam);
const mockPatchTeamName = vi.mocked(patchTeamName);
const mockListTeams = vi.mocked(listTeams);

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

// `Team.name` is typed `string`, but a not-yet-named team really does answer
// `null` from the server — the fixture takes `string | null` and casts to
// exercise exactly that runtime shape.
const teamsPage = (names: Array<string | null>): PaginatedList<Team> => ({
  items: names.map((name, i) => ({
    id: `team-${i}`,
    organization_id: 'o1',
    run_id: 'run-1',
    name,
    color: null,
    member_count: 1,
    share_code: null,
    coordinator_chat_open: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  })) as unknown as Team[],
  total: names.length,
  skip: 0,
  limit: 100,
});

function renderStep() {
  return render(
    <AuthContext.Provider value={authValue}>
      <WizardStateProvider>
        <MemoryRouter initialEntries={['/join/ABC123/team/name']}>
          <Routes>
            <Route path="/join/:code/team/name" element={<PickTeamNameStep />} />
          </Routes>
        </MemoryRouter>
      </WizardStateProvider>
    </AuthContext.Provider>,
  );
}

describe('<PickTeamNameStep> taken-name handling', () => {
  beforeEach(() => {
    mockGetTeam.mockReset();
    mockPatchTeamName.mockReset();
    mockListTeams.mockReset();
    navigateMock.mockReset();
    sessionStorage.clear();
    // The step only queries with a team in wizard state; the provider reads it
    // from sessionStorage, the org slug comes from the authenticated user.
    sessionStorage.setItem(
      'clavis_wizard_team_state',
      JSON.stringify({ teamId: 'team-1', runId: 'run-1' }),
    );
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
  });

  // The heading already asks the team to choose together, so the info box does not
  // say it a second time.
  describe('the info box', () => {
    afterEach(async () => {
      await i18n.changeLanguage('de');
    });

    it.each([
      ['de', 'Du legst den Namen für dein Team fest.', 'Sprich mit deinem Team und gib ihm einen Namen.'],
      ['en', null, 'Discuss with your team and decide on a name together.'],
    ])('in %s holds its first sentence and not the repeat of the heading', async (lng, first, repeat) => {
      await i18n.changeLanguage(lng);
      mockListTeams.mockResolvedValue(teamsPage([]));

      renderStep();

      await waitFor(() => expect(mockListTeams).toHaveBeenCalled());
      if (first) expect(screen.getByText(first)).toBeInTheDocument();
      expect(screen.queryByText(repeat)).toBeNull();
    });
  });

  it('leaves out a suggestion already taken in this run, case-insensitively', async () => {
    mockListTeams.mockResolvedValue(teamsPage(['schnelle füchse']));

    renderStep();

    expect(await screen.findByText('Stadtentdecker')).toBeInTheDocument();
    expect(screen.getByText('Neugierige Crew')).toBeInTheDocument();
    expect(screen.getByText('Augsburger Abenteurer')).toBeInTheDocument();
    expect(screen.queryByText('Schnelle Füchse')).not.toBeInTheDocument();
  });

  it('tolerates the caller\'s own unnamed team alongside a named one', async () => {
    // The run's team list always includes the caller's own team, which has no
    // name yet at this point in the flow — the backend sends null for it.
    mockListTeams.mockResolvedValue(teamsPage([null, 'Schnelle Füchse']));

    renderStep();

    expect(await screen.findByText('Stadtentdecker')).toBeInTheDocument();
    expect(screen.getByText('Neugierige Crew')).toBeInTheDocument();
    expect(screen.getByText('Augsburger Abenteurer')).toBeInTheDocument();
    expect(screen.queryByText('Schnelle Füchse')).not.toBeInTheDocument();
  });

  it('shows only the free-text field when every suggestion is taken', async () => {
    mockListTeams.mockResolvedValue(
      teamsPage(['Schnelle Füchse', 'Stadtentdecker', 'Neugierige Crew', 'Augsburger Abenteurer']),
    );

    renderStep();

    await waitFor(() => expect(mockListTeams).toHaveBeenCalledWith('slug', 'run-1'));
    expect(screen.queryByText('Team-Namen-Vorschläge:')).not.toBeInTheDocument();
    expect(screen.queryByText('Schnelle Füchse')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Team-Name')).toBeInTheDocument();
  });

  it('never renders a taken suggestion while the lookup is still pending', async () => {
    let resolveList!: (value: PaginatedList<Team>) => void;
    mockListTeams.mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );

    renderStep();

    // Let pending microtasks (mount effects) run without resolving the
    // lookup — this is the window a taken suggestion must never flash in.
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(screen.queryByText('Schnelle Füchse')).not.toBeInTheDocument();
    expect(screen.queryByText('Stadtentdecker')).not.toBeInTheDocument();
    expect(screen.queryByText('Team-Namen-Vorschläge:')).not.toBeInTheDocument();

    resolveList(teamsPage(['Schnelle Füchse']));

    expect(await screen.findByText('Stadtentdecker')).toBeInTheDocument();
    expect(screen.queryByText('Schnelle Füchse')).not.toBeInTheDocument();
  });

  it('shows the taken-name error and keeps the input on TEAM_NAME_TAKEN', async () => {
    mockListTeams.mockResolvedValue(teamsPage([]));
    mockPatchTeamName.mockRejectedValue(
      new ApiError(409, 'Name already taken', 'TEAM_NAME_TAKEN'),
    );

    renderStep();

    const input = await screen.findByLabelText('Team-Name');
    await userEvent.type(input, 'Rockets');
    await userEvent.click(screen.getByRole('button', { name: 'Rallye starten' }));

    expect(
      await screen.findByText('Dieser Name ist in diesem Lauf schon vergeben.'),
    ).toBeInTheDocument();
    expect(input).toHaveValue('Rockets');
  });

  // The wizard ends on the stations, not on the bare participant route whose index
  // redirect a first tap could be overtaken by.
  it('ends on the stations with replace after the name is saved', async () => {
    mockListTeams.mockResolvedValue(teamsPage([]));
    mockPatchTeamName.mockResolvedValue({ name: 'Rockets' } as never);

    renderStep();

    const input = await screen.findByLabelText('Team-Name');
    await userEvent.type(input, 'Rockets');
    await userEvent.click(screen.getByRole('button', { name: 'Rallye starten' }));

    await waitFor(() =>
      expect(navigateMock).toHaveBeenCalledWith('/runs/run-1/participant/stations', {
        replace: true,
      }),
    );
  });
});
