import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router';
import TeamInfo from './TeamInfo';
import i18n from '../../i18n';
import { AuthContext } from '../../auth/AuthContext';
import { ApiError } from '../../api/client';
import type { Team } from '../../api/types';

const getTeamMock = vi.fn();
const leaveTeamMock = vi.fn();

const REFUSED_DE =
  'Du bist das letzte Mitglied. Wenn du gehst, wird das Team aufgelöst und seine Antworten zählen nicht mehr.';
const REFUSED_EN =
  'You are the last member. If you leave, the team is dissolved and its answers no longer count.';
const PLAIN_DE = 'Möchtest du dieses Team wirklich verlassen?';
const NO_REJOIN_DE = 'Als Gast kannst du diesem Lauf danach nicht erneut beitreten.';

/**
 * Every leave the page sends, with what was on the screen at that moment.
 * The dissolution rule is checked on this record after every test.
 */
interface SentLeave {
  teamId: string;
  confirmed: boolean;
  refusalOnScreen: boolean;
}
let sentLeaves: SentLeave[] = [];
/** Team ids whose leave the server refused with TEAM_WOULD_DISSOLVE so far. */
let refusedTeams: string[] = [];

vi.mock('../../api/runs', () => ({
  getTeam: (...args: unknown[]) => getTeamMock(...args),
  leaveTeam: async (...args: unknown[]) => {
    const teamId = args[2] as string;
    const options = args[3] as { confirmDissolution?: boolean } | undefined;
    const text = document.body.textContent ?? '';
    sentLeaves.push({
      teamId,
      confirmed: options?.confirmDissolution === true,
      refusalOnScreen:
        refusedTeams.includes(teamId) &&
        (text.includes(REFUSED_DE) || text.includes(REFUSED_EN)),
    });
    try {
      return await leaveTeamMock(...args);
    } catch (err) {
      if (err instanceof ApiError && err.errorCode === 'TEAM_WOULD_DISSOLVE') {
        refusedTeams.push(teamId);
      }
      throw err;
    }
  },
}));

type Role = 'participant' | 'guest';

const makeUser = (role: Role) => ({
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role,
  organization_id: 'org-1',
  organization_slug: 'test-org',
});

const makeTeam = (memberCount: number, id = 'team-1'): Team => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  name: 'Die Fugger',
  color: null,
  member_count: memberCount,
  share_code: 'ABC123',
  coordinator_chat_open: false,
  members: Array.from({ length: memberCount }, (_, i) => ({
    id: `m-${i + 1}`,
    display_name: `Mitglied ${i + 1}`,
  })),
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

const tree = (team: Team | null, role: Role, membership?: 'unknown' | 'present' | 'none') => (
  <MemoryRouter initialEntries={['/runs/run-1/participant/team/info']}>
    <AuthContext.Provider
      value={{
        user: makeUser(role),
        isAuthenticated: true,
        isLoading: false,
        login: vi.fn(),
        logout: vi.fn(),
        checkAuth: vi.fn(),
        needsTwoFactorSetup: () => false,
      }}
    >
      <Routes>
        <Route
          path="/runs/:runId/participant/team"
          element={<Outlet context={{ team, run: null, membership }} />}
        >
          <Route path="info" element={<TeamInfo />} />
        </Route>
      </Routes>
    </AuthContext.Provider>
  </MemoryRouter>
);

const renderInfo = (team: Team | null, role: Role, membership?: 'unknown' | 'present' | 'none') =>
  render(tree(team, role, membership));

const openLeaveDialog = async (name: string) => {
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name }));
  return screen.findByRole('dialog');
};

const wouldDissolve = () =>
  new ApiError(409, 'Leaving would dissolve the team', 'TEAM_WOULD_DISSOLVE', {
    error_code: 'TEAM_WOULD_DISSOLVE',
    member_count: 1,
    answer_count: 3,
    result_published: false,
    run_ended: false,
  });

describe('TeamInfo, the confirmation of "Team verlassen"', () => {
  beforeEach(() => {
    sessionStorage.clear();
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    sentLeaves = [];
    refusedTeams = [];
    getTeamMock.mockReset();
    leaveTeamMock.mockReset();
    leaveTeamMock.mockResolvedValue(undefined);
  });

  afterEach(async () => {
    await i18n.changeLanguage('de');
    // The rule, on every test of this file: no leave carries the confirmation
    // unless a refusal for the same team was on the screen when it was sent.
    for (const sent of sentLeaves.filter((s) => s.confirmed)) {
      expect(sent.refusalOnScreen).toBe(true);
    }
  });

  // The page decides nothing: whatever member count it holds, the first
  // question is the plain one and the first call carries no confirmation.
  describe('before the server said anything', () => {
    it('asks the plain question, also when the page holds one member only', async () => {
      renderInfo(makeTeam(1), 'participant');

      const dialog = await openLeaveDialog('Team verlassen');

      expect(dialog).toHaveTextContent(PLAIN_DE);
      expect(dialog).not.toHaveTextContent('aufgelöst');
      expect(dialog).not.toHaveTextContent(NO_REJOIN_DE);
    });

    it('asks the guest the plain guest question', async () => {
      renderInfo(makeTeam(1), 'guest');

      const dialog = await openLeaveDialog('Team verlassen');

      expect(dialog).toHaveTextContent(`${PLAIN_DE} ${NO_REJOIN_DE}`);
      expect(dialog).not.toHaveTextContent('aufgelöst');
    });

    it('reads no member count to open the confirmation', async () => {
      renderInfo(makeTeam(1), 'participant');

      await openLeaveDialog('Team verlassen');

      expect(getTeamMock).not.toHaveBeenCalled();
    });
  });

  // With others left in the team nothing dissolves.
  it('lets a member who is not the last leave with one call and no second confirmation', async () => {
    renderInfo(makeTeam(2), 'participant');
    const user = userEvent.setup();

    const dialog = await openLeaveDialog('Team verlassen');
    expect(dialog).toHaveTextContent(PLAIN_DE);
    await user.click(screen.getByRole('button', { name: 'Verlassen' }));

    await waitFor(() =>
      expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBeNull(),
    );
    expect(leaveTeamMock).toHaveBeenCalledTimes(1);
    expect(leaveTeamMock).toHaveBeenCalledWith('test-org', 'run-1', 'team-1');
    expect(screen.queryByText(REFUSED_DE)).not.toBeInTheDocument();
  });

  // The server's refusal is the only source of the dissolution text.
  describe('when the server refuses because the leave dissolves the team', () => {
    it('sends the first call without the field, shows the dissolution text, and sends the field with the second call', async () => {
      leaveTeamMock.mockRejectedValueOnce(wouldDissolve());
      renderInfo(makeTeam(1), 'participant');
      const user = userEvent.setup();

      await openLeaveDialog('Team verlassen');
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));

      await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent(REFUSED_DE));
      expect(screen.getByRole('dialog')).not.toHaveTextContent(NO_REJOIN_DE);
      expect(screen.getByRole('dialog')).not.toHaveTextContent(PLAIN_DE);
      expect(leaveTeamMock).toHaveBeenCalledTimes(1);
      expect(leaveTeamMock).toHaveBeenLastCalledWith('test-org', 'run-1', 'team-1');
      // The refusal is a question, not an error.
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(screen.queryByText('Leaving would dissolve the team')).not.toBeInTheDocument();
      expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-1');

      await user.click(screen.getByRole('button', { name: 'Verlassen' }));

      await waitFor(() => expect(leaveTeamMock).toHaveBeenCalledTimes(2));
      expect(leaveTeamMock).toHaveBeenLastCalledWith('test-org', 'run-1', 'team-1', {
        confirmDissolution: true,
      });
      await waitFor(() =>
        expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBeNull(),
      );
    });

    it('shows the guest the sentence about the new join in the second confirmation', async () => {
      leaveTeamMock.mockRejectedValueOnce(wouldDissolve());
      renderInfo(makeTeam(2), 'guest');
      const user = userEvent.setup();

      await openLeaveDialog('Team verlassen');
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));

      await waitFor(() =>
        expect(screen.getByRole('dialog')).toHaveTextContent(`${REFUSED_DE} ${NO_REJOIN_DE}`),
      );
    });

    // Both texts exist in English.
    it('says the second confirmation in English', async () => {
      await i18n.changeLanguage('en');
      leaveTeamMock.mockRejectedValue(wouldDissolve());
      const first = renderInfo(makeTeam(1), 'participant');
      const user = userEvent.setup();

      await openLeaveDialog('Leave team');
      await user.click(screen.getByRole('button', { name: 'Leave' }));
      await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent(REFUSED_EN));
      expect(screen.getByRole('dialog')).not.toHaveTextContent('As a guest');
      first.unmount();

      renderInfo(makeTeam(1), 'guest');
      await openLeaveDialog('Leave team');
      await user.click(screen.getByRole('button', { name: 'Leave' }));
      await waitFor(() =>
        expect(screen.getByRole('dialog')).toHaveTextContent(
          `${REFUSED_EN} As a guest you cannot join this run again afterwards.`,
        ),
      );
    });

    it('sends nothing more and keeps the membership when the person cancels', async () => {
      leaveTeamMock.mockRejectedValueOnce(wouldDissolve());
      renderInfo(makeTeam(2), 'participant');
      const user = userEvent.setup();

      await openLeaveDialog('Team verlassen');
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));
      await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent(REFUSED_DE));

      await user.click(screen.getByRole('button', { name: 'Abbrechen' }));

      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(leaveTeamMock).toHaveBeenCalledTimes(1);
      expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-1');
      expect(screen.getByText('Die Fugger')).toBeInTheDocument();
      expect(screen.queryByText('Leaving would dissolve the team')).not.toBeInTheDocument();
    });

    // The mirror of the rule: the facts changed again, the server refuses the
    // confirmed send, and the person is asked again instead of reading an
    // error or leaving unasked.
    it('shows a second refusal after a confirmed send again', async () => {
      leaveTeamMock.mockRejectedValueOnce(wouldDissolve());
      leaveTeamMock.mockRejectedValueOnce(wouldDissolve());
      renderInfo(makeTeam(1), 'participant');
      const user = userEvent.setup();

      await openLeaveDialog('Team verlassen');
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));
      await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent(REFUSED_DE));
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));

      await waitFor(() => expect(leaveTeamMock).toHaveBeenCalledTimes(2));
      await waitFor(() =>
        expect(screen.getByRole('button', { name: 'Verlassen' })).toBeEnabled(),
      );
      expect(screen.getByRole('dialog')).toHaveTextContent(REFUSED_DE);
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
      expect(screen.queryByText('Leaving would dissolve the team')).not.toBeInTheDocument();
      expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-1');

      await user.click(screen.getByRole('button', { name: 'Verlassen' }));

      await waitFor(() => expect(leaveTeamMock).toHaveBeenCalledTimes(3));
      expect(leaveTeamMock).toHaveBeenLastCalledWith('test-org', 'run-1', 'team-1', {
        confirmDissolution: true,
      });
      await waitFor(() =>
        expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBeNull(),
      );
    });

    it('keeps the present handling of any other 409', async () => {
      leaveTeamMock.mockRejectedValueOnce(new ApiError(409, 'Run is not active', 'RUN_NOT_ACTIVE'));
      renderInfo(makeTeam(2), 'participant');
      const user = userEvent.setup();

      await openLeaveDialog('Team verlassen');
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));

      expect(await screen.findByText('Run is not active')).toBeInTheDocument();
      expect(screen.getByRole('dialog')).not.toHaveTextContent(REFUSED_DE);
      expect(leaveTeamMock).toHaveBeenCalledTimes(1);
    });
  });

  // No path sends confirm_dissolution true unless a refusal for the same act
  // on the same team was shown to the person before.
  describe('the rule: no confirmation without a refusal that was shown', () => {
    it('sends no confirmation on any first call, whatever the page holds', async () => {
      for (const [count, role] of [
        [1, 'participant'],
        [1, 'guest'],
        [2, 'participant'],
        [5, 'guest'],
      ] as const) {
        const view = renderInfo(makeTeam(count), role);
        const user = userEvent.setup();
        const before = sentLeaves.length;

        await openLeaveDialog('Team verlassen');
        await user.click(screen.getByRole('button', { name: 'Verlassen' }));

        await waitFor(() => expect(sentLeaves.length).toBe(before + 1));
        expect(sentLeaves[before].confirmed).toBe(false);
        view.unmount();
        sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
      }
    });

    it('starts with the plain question and an unconfirmed call again after a cancelled refusal', async () => {
      leaveTeamMock.mockRejectedValueOnce(wouldDissolve());
      renderInfo(makeTeam(2), 'participant');
      const user = userEvent.setup();

      await openLeaveDialog('Team verlassen');
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));
      await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent(REFUSED_DE));
      await user.click(screen.getByRole('button', { name: 'Abbrechen' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

      const again = await openLeaveDialog('Team verlassen');
      expect(again).toHaveTextContent(PLAIN_DE);
      expect(again).not.toHaveTextContent(REFUSED_DE);
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));
      await waitFor(() => expect(leaveTeamMock).toHaveBeenCalledTimes(2));
      expect(leaveTeamMock).toHaveBeenLastCalledWith('test-org', 'run-1', 'team-1');
    });

    // The shell switches the team live when the person is moved. A refusal
    // that was shown for the old team confirms nothing for the new one.
    it('does not carry a shown refusal over to another team', async () => {
      leaveTeamMock.mockRejectedValueOnce(wouldDissolve());
      const view = renderInfo(makeTeam(1), 'participant');
      const user = userEvent.setup();

      await openLeaveDialog('Team verlassen');
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));
      await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent(REFUSED_DE));

      view.rerender(tree(makeTeam(1, 'team-2'), 'participant'));

      await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent(PLAIN_DE));
      expect(screen.getByRole('dialog')).not.toHaveTextContent(REFUSED_DE);
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));
      await waitFor(() => expect(leaveTeamMock).toHaveBeenCalledTimes(2));
      expect(leaveTeamMock).toHaveBeenLastCalledWith('test-org', 'run-1', 'team-2');
    });

    it('sends the confirmation only while the refusal is on the screen', async () => {
      leaveTeamMock.mockRejectedValueOnce(wouldDissolve());
      renderInfo(makeTeam(1), 'participant');
      const user = userEvent.setup();

      await openLeaveDialog('Team verlassen');
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));
      await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent(REFUSED_DE));
      await user.click(screen.getByRole('button', { name: 'Verlassen' }));
      await waitFor(() => expect(sentLeaves).toHaveLength(2));

      expect(sentLeaves).toEqual([
        { teamId: 'team-1', confirmed: false, refusalOnScreen: false },
        { teamId: 'team-1', confirmed: true, refusalOnScreen: true },
      ]);
    });
  });
});

// What the page says without a team, and the count it shows.
describe('TeamInfo, without a team and the member count', () => {
  beforeEach(() => {
    sessionStorage.clear();
    sentLeaves = [];
    refusedTeams = [];
    leaveTeamMock.mockReset();
  });

  afterEach(async () => {
    await i18n.changeLanguage('de');
  });

  const SENTENCE_DE = 'Für diesen Lauf wurde keine Team-Mitgliedschaft gefunden.';
  const SENTENCE_EN = 'No team membership found for this run.';

  it('says the missing membership in German when the shell found none', () => {
    renderInfo(null, 'participant', 'none');

    expect(screen.getByText(SENTENCE_DE)).toBeInTheDocument();
  });

  it('says the missing membership in English', async () => {
    await i18n.changeLanguage('en');
    renderInfo(null, 'participant', 'none');

    expect(screen.getByText(SENTENCE_EN)).toBeInTheDocument();
  });

  it('waits while the shell still asks the server, with or without a stored key', () => {
    renderInfo(null, 'participant', 'unknown');

    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.queryByText(SENTENCE_DE)).not.toBeInTheDocument();
  });

  it('keeps asking the stored key when the shell hands down no answer', () => {
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    renderInfo(null, 'participant');

    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.queryByText(SENTENCE_DE)).not.toBeInTheDocument();
  });

  it('counts one member in the singular and more in the plural, in German', () => {
    const { unmount } = renderInfo({ ...makeTeam(1), member_count: 1 }, 'participant');
    expect(screen.getByText('1 Mitglied')).toBeInTheDocument();
    unmount();

    renderInfo({ ...makeTeam(3), member_count: 3 }, 'participant');
    expect(screen.getByText('3 Mitglieder')).toBeInTheDocument();
  });

  it('counts one member in the singular and more in the plural, in English', async () => {
    await i18n.changeLanguage('en');
    const { unmount } = renderInfo({ ...makeTeam(1), member_count: 1 }, 'participant');
    expect(screen.getByText('1 Member')).toBeInTheDocument();
    unmount();

    renderInfo({ ...makeTeam(2), member_count: 2 }, 'participant');
    expect(screen.getByText('2 Members')).toBeInTheDocument();
  });
});
