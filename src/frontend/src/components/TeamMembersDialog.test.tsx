import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TeamMembersDialog from './TeamMembersDialog';
import {
  getRun,
  getTeam,
  listTeams,
  moveTeamMember,
  removeTeam,
  removeTeamMember,
} from '../api/runs';
import deRuns from '../i18n/locales/de/runs.json';
import enRuns from '../i18n/locales/en/runs.json';
import { ApiError } from '../api/client';
import type { Run, Team } from '../api/types';

vi.mock('../api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/runs')>();
  return {
    ...actual,
    getTeam: vi.fn(),
    listTeams: vi.fn(),
    getRun: vi.fn(),
    moveTeamMember: vi.fn(),
    removeTeam: vi.fn(),
    removeTeamMember: vi.fn(),
  };
});

const makeTeam = (
  over: Partial<Team> & Pick<Team, 'id' | 'name' | 'member_count'>,
): Team => ({
  organization_id: 'org-1',
  run_id: 'run-1',
  color: null,
  share_code: 'SHARE1',
  coordinator_chat_open: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...over,
});

const member = { id: 'member-1', display_name: 'Max Muster', is_guest: false };

// The source team comes back from GET team with its member list.
const sourceTeam = {
  ...makeTeam({ id: 'team-1', name: 'Die Fugger', member_count: 2 }),
  members: [member, { id: 'member-2', display_name: 'Erika Muster', is_guest: false }],
};

const run: Run = {
  id: 'run-1',
  organization_id: 'org-1',
  rallye_id: null,
  name: 'Rallye durch Augsburg',
  state: 'active',
  join_code: 'JOIN1',
  scheduled_for: null,
  started_at: '2026-01-01T09:00:00Z',
  paused_at: null,
  ended_at: null,
  settings: { max_members_per_team: 3 },
  max_teams: 10,
  content_languages: ['de'],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

// team-2 has a free place, team-3 is full at max_members_per_team = 3.
const teamsPage = {
  items: [
    makeTeam({ id: 'team-1', name: 'Die Fugger', member_count: 2 }),
    makeTeam({ id: 'team-2', name: 'Die Welser', member_count: 2 }),
    makeTeam({ id: 'team-3', name: 'Die Kaufleute', member_count: 3 }),
  ],
  total: 3,
  skip: 0,
  limit: 100,
};

const renderDialog = (onMemberRemoved = vi.fn()) =>
  render(
    <TeamMembersDialog
      open
      onClose={vi.fn()}
      orgSlug="test-org"
      runId="run-1"
      teamId="team-1"
      teamName="Die Fugger"
      rallyeCode="JOIN1"
      onMemberRemoved={onMemberRemoved}
    />,
  );

// Opens the move picker for the fixture member and waits for its team list.
const openMovePicker = async (user: ReturnType<typeof userEvent.setup>) => {
  await screen.findByText('Max Muster');
  await user.click(
    screen.getByRole('button', { name: 'Max Muster in ein anderes Team verschieben' }),
  );
  await screen.findByText('Die Welser');
};

describe('TeamMembersDialog — moving a member', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getTeam).mockResolvedValue(sourceTeam);
    vi.mocked(listTeams).mockResolvedValue(teamsPage);
    vi.mocked(getRun).mockResolvedValue(run);
    vi.mocked(moveTeamMember).mockResolvedValue(undefined);
  });

  // Picking a target in the picker sends the move, drops the member
  // from the list and makes the caller refresh its team counts.
  it('sends the move when a target team is picked', async () => {
    const onMemberRemoved = vi.fn();
    const user = userEvent.setup();
    renderDialog(onMemberRemoved);

    await openMovePicker(user);

    // Free places are part of the row: max 3, two members already in.
    expect(screen.getByText('1 von 3 Plätzen frei')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Die Welser/ }));

    await waitFor(() => {
      expect(moveTeamMember).toHaveBeenCalledWith(
        'test-org',
        'run-1',
        'team-1',
        'member-1',
        'team-2',
        false,
      );
    });
    await waitFor(() => {
      expect(screen.queryByText('Max Muster')).not.toBeInTheDocument();
    });
    expect(onMemberRemoved).toHaveBeenCalled();
    // The source team itself never appears as a target.
    expect(vi.mocked(moveTeamMember).mock.calls[0][4]).not.toBe('team-1');
  });

  // A full target team stays visible in the picker but cannot be
  // picked. The backend's TEAM_FULL refusal is not the only signal.
  it('disables a full target team in the picker', async () => {
    const user = userEvent.setup();
    renderDialog();

    await openMovePicker(user);

    const fullRow = screen.getByRole('button', { name: /Die Kaufleute/ });
    expect(fullRow).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('Voll')).toBeInTheDocument();
    // The Welser row with a free place stays enabled next to it.
    expect(screen.getByRole('button', { name: /Die Welser/ })).not.toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('says so when the run has no other teams', async () => {
    vi.mocked(listTeams).mockResolvedValue({ ...teamsPage, items: teamsPage.items.slice(0, 1) });
    const user = userEvent.setup();
    renderDialog();

    await screen.findByText('Max Muster');
    await user.click(
      screen.getByRole('button', { name: 'Max Muster in ein anderes Team verschieben' }),
    );

    expect(
      await screen.findByText('In diesem Lauf gibt es keine weiteren Teams.'),
    ).toBeInTheDocument();
  });

  // The coded refusals (TEAM_FULL, TARGET_NOT_IN_RUN, …) surface in place.
  // The picker does not silently close.
  it('shows the API error when the move is refused', async () => {
    vi.mocked(moveTeamMember).mockRejectedValue(
      new ApiError(422, 'Zielteam ist voll (3 Mitglieder).', 'TEAM_FULL'),
    );
    const user = userEvent.setup();
    renderDialog();

    await openMovePicker(user);
    await user.click(screen.getByRole('button', { name: /Die Welser/ }));

    expect(await screen.findByText('Zielteam ist voll (3 Mitglieder).')).toBeInTheDocument();
    expect(screen.getByText('Die Welser')).toBeInTheDocument();
  });
});

// The backend blocks only a removed guest from re-joining
// (teams.py records a departure for guests only). The confirm text promises
// the block to a guest and tells the truth about a registered member.
describe('TeamMembersDialog — the removal text follows the member kind', () => {
  const guest = { id: 'member-3', display_name: 'Gast Lena', is_guest: true };
  const teamWithGuest = { ...sourceTeam, members: [member, guest] };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getTeam).mockResolvedValue(teamWithGuest);
    vi.mocked(listTeams).mockResolvedValue(teamsPage);
    vi.mocked(getRun).mockResolvedValue(run);
  });

  it('tells a guest about the re-join block', async () => {
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Gast Lena');

    await user.click(screen.getByRole('button', { name: 'Gast Lena aus dem Team entfernen' }));

    expect(
      screen.getByText(/Im selben Browser kann dieser Gast dem Lauf nicht erneut beitreten/),
    ).toBeInTheDocument();
  });

  it('tells a registered member that they can join again', async () => {
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');

    await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));

    expect(
      screen.getByText(
        'Max Muster wirklich aus dem Team entfernen? Max Muster kann dem Lauf danach wieder beitreten.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/dieser Gast/)).not.toBeInTheDocument();
  });
});

// The run page reports a member change through `reloadSignal`. The
// open dialog loads its list again instead of keeping the one from its opening.
describe('TeamMembersDialog — live reload', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getTeam).mockResolvedValue(sourceTeam);
  });

  const dialog = (reloadSignal: number) => (
    <TeamMembersDialog
      open
      onClose={vi.fn()}
      orgSlug="test-org"
      runId="run-1"
      teamId="team-1"
      teamName="Die Fugger"
      rallyeCode="JOIN1"
      onMemberRemoved={vi.fn()}
      reloadSignal={reloadSignal}
    />
  );

  it('shows a member who joins while the dialog is open', async () => {
    const { rerender } = render(dialog(0));
    await screen.findByText('Max Muster');
    expect(screen.queryByText('Lea Neu')).toBeNull();

    vi.mocked(getTeam).mockResolvedValue({
      ...sourceTeam,
      members: [...sourceTeam.members, { id: 'member-3', display_name: 'Lea Neu', is_guest: true }],
    });
    rerender(dialog(1));

    expect(await screen.findByText('Lea Neu')).toBeInTheDocument();
    // The list stays in place during the reload: no spinner, no lost rows.
    expect(screen.getByText('Max Muster')).toBeInTheDocument();
    expect(getTeam).toHaveBeenCalledTimes(2);
  });
});

// The page decides nothing. Every removal, every move and "Team
// entfernen" goes to the server without `confirm_dissolution`, and only the
// server's refusal TEAM_WOULD_DISSOLVE brings up the dissolution confirmation.
describe('TeamMembersDialog — the server decides whether a step dissolves the team', () => {
  const loneTeam = { ...sourceTeam, member_count: 1, members: [member] };

  const dialog = (props: Partial<React.ComponentProps<typeof TeamMembersDialog>> = {}) => (
    <TeamMembersDialog
      open
      onClose={vi.fn()}
      orgSlug="test-org"
      runId="run-1"
      teamId="team-1"
      teamName="Die Fugger"
      rallyeCode="JOIN1"
      onMemberRemoved={vi.fn()}
      {...props}
    />
  );
  const renderWith = () => {
    const onTeamRemoved = vi.fn();
    const onMemberRemoved = vi.fn();
    render(dialog({ onTeamRemoved, onMemberRemoved }));
    return { onTeamRemoved, onMemberRemoved };
  };

  // The body of 409 TEAM_WOULD_DISSOLVE, with the facts as the server has them.
  const refusal = (facts: Record<string, unknown> = {}) =>
    new ApiError(409, 'The step would dissolve the team.', 'TEAM_WOULD_DISSOLVE', {
      detail: 'The step would dissolve the team.',
      error_code: 'TEAM_WOULD_DISSOLVE',
      member_count: 1,
      answer_count: 5,
      result_published: false,
      run_ended: false,
      ...facts,
    });
  const busy = () =>
    new ApiError(409, 'Team is busy.', 'TEAM_BUSY', { detail: 'Team is busy.', error_code: 'TEAM_BUSY' });
  const staff = (members: number, answers: number) =>
    `Mit diesem Schritt wird das Team Die Fugger aufgelöst. Mitglieder: ${members}, Antworten: ${answers}. Das Team verlässt die Rangliste.`;
  const endedText = 'Der Lauf ist beendet. Die Endrangliste ändert sich.';
  const publishedText = 'Das Ergebnis ist veröffentlicht. Das öffentliche Ergebnis ändert sich sofort.';
  const guestsText = 'Die Gäste des Teams können dem Lauf im selben Browser nicht erneut beitreten.';
  const busyText = 'Das Team wird gerade geändert. Versuch es bitte gleich noch einmal.';
  const membersFailed =
    'Die Mitglieder des Teams konnten nicht gelesen werden.';

  type User = ReturnType<typeof userEvent.setup>;
  const moveName = 'Max Muster in ein anderes Team verschieben';

  // The three acts: how the person asks for each, how the refusal is
  // confirmed, and the call each one makes, plain and confirmed.
  const acts = {
    remove: {
      ask: async (user: User) => {
        await screen.findByText('Max Muster');
        await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
        await user.click(screen.getByRole('button', { name: 'Entfernen' }));
      },
      confirmName: 'Entfernen',
      call: removeTeamMember,
      args: (confirmed: boolean) => ['test-org', 'run-1', 'team-1', 'member-1', confirmed],
    },
    move: {
      ask: async (user: User) => {
        await openMovePicker(user);
        await user.click(screen.getByRole('button', { name: /Die Welser/ }));
      },
      confirmName: moveName,
      call: moveTeamMember,
      args: (confirmed: boolean) => ['test-org', 'run-1', 'team-1', 'member-1', 'team-2', confirmed],
    },
    team: {
      ask: async (user: User) => {
        await screen.findByText('Max Muster');
        await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
        await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
      },
      confirmName: 'Team entfernen',
      call: removeTeam,
      args: (confirmed: boolean) => ['test-org', 'run-1', 'team-1', confirmed],
    },
  } as const;
  const kinds = ['remove', 'move', 'team'] as const;
  const mockOf = (kind: (typeof kinds)[number]) => vi.mocked(acts[kind].call as (...a: unknown[]) => Promise<void>);
  // Whether a recorded call carried the confirmation of the dissolution.
  const sentConfirmed = (kind: (typeof kinds)[number]) =>
    mockOf(kind).mock.calls.map((args) => args[acts[kind].args(true).length - 1] === true);

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getTeam).mockResolvedValue(sourceTeam);
    vi.mocked(listTeams).mockResolvedValue(teamsPage);
    vi.mocked(getRun).mockResolvedValue(run);
    vi.mocked(removeTeam).mockResolvedValue(undefined);
    vi.mocked(removeTeamMember).mockResolvedValue(undefined);
    vi.mocked(moveTeamMember).mockResolvedValue(undefined);
  });

  it('a plain removal of a member who is not the last: one call without the field, no second confirmation', async () => {
    const user = userEvent.setup();
    const { onTeamRemoved, onMemberRemoved } = renderWith();
    await acts.remove.ask(user);

    await waitFor(() => expect(onMemberRemoved).toHaveBeenCalledTimes(1));
    expect(removeTeamMember).toHaveBeenCalledTimes(1);
    expect(removeTeamMember).toHaveBeenCalledWith(...acts.remove.args(false));
    expect(screen.queryByText(/aufgelöst/)).not.toBeInTheDocument();
    expect(onTeamRemoved).not.toHaveBeenCalled();
    // The list is back, without the member.
    expect(await screen.findByText('Erika Muster')).toBeInTheDocument();
    expect(screen.queryByText('Max Muster')).not.toBeInTheDocument();
  });

  it('the first question of "Team entfernen" names the team and no number', async () => {
    const user = userEvent.setup();
    renderWith();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));

    expect(screen.getByText('Team Die Fugger wirklich entfernen?')).toBeInTheDocument();
    expect(screen.queryByText(/Mitglieder:|Antworten:|\d/)).not.toBeInTheDocument();
    expect(removeTeam).not.toHaveBeenCalled();
  });

  it('the refusal of "Team entfernen" names the team, its members and answers, the ranking and the guests', async () => {
    vi.mocked(removeTeam).mockRejectedValueOnce(refusal({ member_count: 2, answer_count: 7 }));
    const user = userEvent.setup();
    renderWith();
    await acts.team.ask(user);

    expect(await screen.findByText(staff(2, 7))).toBeInTheDocument();
    expect(screen.getByText(guestsText)).toBeInTheDocument();
  });

  describe.each(kinds)('%s', (kind) => {
    const act = acts[kind];

    it('the rule: the first call never carries confirm_dissolution, whatever the page has read', async () => {
      // The page sees a team with one member: it still decides nothing.
      vi.mocked(getTeam).mockResolvedValue(loneTeam);
      mockOf(kind).mockRejectedValue(refusal());
      const user = userEvent.setup();
      renderWith();
      await act.ask(user);

      await screen.findByText(staff(1, 5));
      expect(sentConfirmed(kind)).toEqual([false]);
      expect(mockOf(kind)).toHaveBeenCalledWith(...act.args(false));
    });

    it('a refusal shows the numbers of the server, and the second call carries the field', async () => {
      mockOf(kind).mockRejectedValueOnce(refusal({ member_count: 1, answer_count: 5 }));
      const user = userEvent.setup();
      const { onTeamRemoved } = renderWith();
      await act.ask(user);

      expect(await screen.findByText(staff(1, 5))).toBeInTheDocument();
      expect(sentConfirmed(kind)).toEqual([false]);
      expect(onTeamRemoved).not.toHaveBeenCalled();

      await user.click(screen.getByRole('button', { name: act.confirmName }));

      await waitFor(() => expect(onTeamRemoved).toHaveBeenCalledTimes(1));
      expect(sentConfirmed(kind)).toEqual([false, true]);
      expect(mockOf(kind)).toHaveBeenLastCalledWith(...act.args(true));
    });

    it('a cancel after the refusal sends no second call and changes nothing', async () => {
      mockOf(kind).mockRejectedValueOnce(refusal());
      const user = userEvent.setup();
      const { onTeamRemoved, onMemberRemoved } = renderWith();
      await act.ask(user);
      await screen.findByText(staff(1, 5));

      await user.click(screen.getByRole('button', { name: 'Abbrechen' }));

      expect(await screen.findByText('Max Muster')).toBeInTheDocument();
      expect(screen.queryByText(staff(1, 5))).not.toBeInTheDocument();
      expect(mockOf(kind)).toHaveBeenCalledTimes(1);
      expect(onTeamRemoved).not.toHaveBeenCalled();
      expect(onMemberRemoved).not.toHaveBeenCalled();
    });

    it('a second refusal after the confirmed send is shown again with its new numbers', async () => {
      mockOf(kind)
        .mockRejectedValueOnce(refusal({ member_count: 1, answer_count: 5 }))
        .mockRejectedValueOnce(refusal({ member_count: 1, answer_count: 9, result_published: true }));
      const user = userEvent.setup();
      const { onTeamRemoved } = renderWith();
      await act.ask(user);
      await screen.findByText(staff(1, 5));
      expect(screen.queryByText(publishedText)).not.toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: act.confirmName }));

      expect(await screen.findByText(staff(1, 9))).toBeInTheDocument();
      expect(screen.getByText(publishedText)).toBeInTheDocument();
      expect(screen.queryByText(staff(1, 5))).not.toBeInTheDocument();
      expect(onTeamRemoved).not.toHaveBeenCalled();

      // The person decides again, and that send goes through.
      await user.click(screen.getByRole('button', { name: act.confirmName }));
      await waitFor(() => expect(onTeamRemoved).toHaveBeenCalledTimes(1));
      expect(sentConfirmed(kind)).toEqual([false, true, true]);
    });

    it('TEAM_BUSY keeps its notice, on the first call and on the confirmed one', async () => {
      mockOf(kind).mockRejectedValueOnce(busy());
      const user = userEvent.setup();
      const { onTeamRemoved } = renderWith();
      await act.ask(user);
      expect(await screen.findByText(busyText)).toBeInTheDocument();
      expect(screen.queryByText(/aufgelöst/)).not.toBeInTheDocument();
      expect(sentConfirmed(kind)).toEqual([false]);

      // The same step again: refused as a dissolution, then busy once more.
      mockOf(kind).mockRejectedValueOnce(refusal()).mockRejectedValueOnce(busy());
      if (kind === 'move') await user.click(screen.getByRole('button', { name: /Die Welser/ }));
      else await user.click(screen.getByRole('button', { name: act.confirmName }));
      await screen.findByText(staff(1, 5));
      await user.click(screen.getByRole('button', { name: act.confirmName }));

      expect(await screen.findByText(busyText)).toBeInTheDocument();
      // The question stays, and nothing counts as removed.
      expect(screen.getByText(staff(1, 5))).toBeInTheDocument();
      expect(onTeamRemoved).not.toHaveBeenCalled();
    });
  });

  // The sentences come from the answer of the server alone.
  it.each([
    { name: 'run_ended', facts: { run_ended: true }, ended: true, published: false },
    { name: 'result_published', facts: { result_published: true }, ended: false, published: true },
    { name: 'both', facts: { run_ended: true, result_published: true }, ended: true, published: true },
    { name: 'neither', facts: {}, ended: false, published: false },
    { name: 'a missing run_ended', facts: { run_ended: undefined }, ended: false, published: false },
  ])('a refusal with $name shows its sentences and no other', async ({ facts, ended, published }) => {
    vi.mocked(removeTeamMember).mockRejectedValueOnce(refusal(facts));
    const user = userEvent.setup();
    renderWith();
    await acts.remove.ask(user);
    await screen.findByText(staff(1, 5));

    expect(screen.queryAllByText(endedText)).toHaveLength(ended ? 1 : 0);
    expect(screen.queryAllByText(publishedText)).toHaveLength(published ? 1 : 0);
    // The guests of the team are named for "Team entfernen" only.
    expect(screen.queryByText(guestsText)).not.toBeInTheDocument();
  });

  it('the refusal of a move and of "Team entfernen" name the final ranking and the public result too', async () => {
    const facts = { run_ended: true, result_published: true };
    vi.mocked(moveTeamMember).mockRejectedValueOnce(refusal(facts));
    const user = userEvent.setup();
    const { unmount } = render(dialog());
    await acts.move.ask(user);
    await screen.findByText(staff(1, 5));
    expect(screen.getByText(endedText)).toBeInTheDocument();
    expect(screen.getByText(publishedText)).toBeInTheDocument();
    unmount();

    vi.mocked(removeTeam).mockRejectedValueOnce(refusal(facts));
    render(dialog());
    await acts.team.ask(user);
    await screen.findByText(staff(1, 5));
    expect(screen.getByText(endedText)).toBeInTheDocument();
    expect(screen.getByText(publishedText)).toBeInTheDocument();
  });

  it('a plain move that the server does: one call without the field, the team stays', async () => {
    const user = userEvent.setup();
    const { onTeamRemoved, onMemberRemoved } = renderWith();
    await acts.move.ask(user);

    await waitFor(() => expect(onMemberRemoved).toHaveBeenCalledTimes(1));
    expect(sentConfirmed('move')).toEqual([false]);
    expect(onTeamRemoved).not.toHaveBeenCalled();
  });

  it('treats a team that is already gone as removed, on the first call and on the confirmed one', async () => {
    const gone = () => new ApiError(404, 'Team not found', undefined, { detail: 'Team not found' });
    vi.mocked(removeTeam).mockRejectedValueOnce(gone());
    const user = userEvent.setup();
    const first = renderWith();
    await acts.team.ask(user);
    await waitFor(() => expect(first.onTeamRemoved).toHaveBeenCalledTimes(1));
    expect(sentConfirmed('team')).toEqual([false]);
  });

  it('a refusal stays on screen when the member list is read again behind it', async () => {
    vi.mocked(removeTeamMember).mockRejectedValueOnce(refusal());
    const user = userEvent.setup();
    const { rerender } = render(dialog());
    await acts.remove.ask(user);
    await screen.findByText(staff(1, 5));

    vi.mocked(getTeam).mockResolvedValue({ ...sourceTeam, members: [] });
    rerender(dialog({ reloadSignal: 1 }));
    await waitFor(() => expect(getTeam).toHaveBeenCalledTimes(2));

    expect(screen.getByText(staff(1, 5))).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Entfernen' })).toBeEnabled();
  });

  it('a refusal without its numbers is shown as an error, never with a zero', async () => {
    vi.mocked(removeTeamMember).mockRejectedValueOnce(
      new ApiError(409, 'The step would dissolve the team.', 'TEAM_WOULD_DISSOLVE', {}),
    );
    const user = userEvent.setup();
    renderWith();
    await acts.remove.ask(user);

    expect(await screen.findByText('The step would dissolve the team.')).toBeInTheDocument();
    expect(screen.queryByText(/Mitglieder: 0/)).not.toBeInTheDocument();
    expect(removeTeamMember).toHaveBeenCalledTimes(1);
  });

  // The member list of the open dialog: a display, and a gate for nothing.
  describe('the member list', () => {
    it('says so when the first read fails, reads again, and holds no step back', async () => {
      vi.mocked(getTeam).mockRejectedValue(new Error('team down'));
      const user = userEvent.setup();
      render(dialog());

      expect(await screen.findByText(membersFailed)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Team entfernen' })).toBeEnabled();

      vi.mocked(getTeam).mockResolvedValue(sourceTeam);
      await user.click(screen.getByRole('button', { name: 'Erneut laden' }));
      expect(await screen.findByText('Max Muster')).toBeInTheDocument();
      expect(screen.queryByText(membersFailed)).not.toBeInTheDocument();
    });

    it('keeps the list during a failed refresh, says so, and lets the plain question through', async () => {
      const user = userEvent.setup();
      const { rerender } = render(dialog());
      await screen.findByText('Max Muster');

      vi.mocked(getTeam).mockRejectedValue(new Error('team down'));
      rerender(dialog({ reloadSignal: 1 }));
      expect(await screen.findByText(membersFailed)).toBeInTheDocument();
      expect(screen.getByText('Max Muster')).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
      await user.click(screen.getByRole('button', { name: 'Entfernen' }));
      await waitFor(() => expect(removeTeamMember).toHaveBeenCalledWith(...acts.remove.args(false)));
    });

    it('a member whom the fresh list no longer names is no longer asked about', async () => {
      const user = userEvent.setup();
      const { rerender } = render(dialog());
      await screen.findByText('Max Muster');
      await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
      expect(screen.getByRole('button', { name: 'Entfernen' })).toBeInTheDocument();

      vi.mocked(getTeam).mockResolvedValue({ ...sourceTeam, members: [sourceTeam.members[1]] });
      rerender(dialog({ reloadSignal: 1 }));

      await waitFor(() => expect(screen.queryByRole('button', { name: 'Entfernen' })).not.toBeInTheDocument());
      expect(screen.getByText('Erika Muster')).toBeInTheDocument();
      expect(removeTeamMember).not.toHaveBeenCalled();
    });

    it('a dialog that opens again reads the members anew', async () => {
      const { rerender } = render(dialog());
      await screen.findByText('Max Muster');
      rerender(dialog({ open: false }));
      vi.mocked(getTeam).mockResolvedValue(loneTeam);
      rerender(dialog({ open: true }));

      await waitFor(() => expect(getTeam).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(screen.queryByText('Erika Muster')).not.toBeInTheDocument());
    });
  });

  it('every text exists in German and English, and no key of the old page-side decision is left', () => {
    const keys = (o: Record<string, unknown>, prefix = ''): string[] =>
      Object.entries(o).flatMap(([k, v]) =>
        v && typeof v === 'object' ? keys(v as Record<string, unknown>, `${prefix}${k}.`) : [`${prefix}${k}`],
      );
    expect(keys(deRuns.teamMembers).sort()).toEqual(keys(enRuns.teamMembers).sort());
    expect(enRuns.teamMembers).not.toEqual(deRuns.teamMembers);
    expect(keys(deRuns.teamMembers.removeTeam)).toEqual([
      'action',
      'confirm',
      'busy',
      'reload',
      'membersFailed',
      'guests',
    ]);
    // The texts of the server's refusal: the same five keys, last in the file.
    expect(keys(deRuns.dissolveRefused)).toEqual([
      'staff',
      'staffRunEnded',
      'staffResultPublished',
      'participant',
      'participantGuest',
    ]);
    expect(keys(enRuns.dissolveRefused)).toEqual(keys(deRuns.dissolveRefused));
    expect(enRuns.dissolveRefused).not.toEqual(deRuns.dissolveRefused);
    expect(Object.keys(deRuns).at(-1)).toBe('dissolveRefused');
    expect(Object.keys(enRuns).at(-1)).toBe('dissolveRefused');
  });
});

// The dialog swaps its content by state, and the pressed
// button goes away. The focus must stay inside role=dialog, on the new text.
describe('TeamMembersDialog — the focus after an inner step', () => {
  const dissolveRefusal = () =>
    new ApiError(409, 'The step would dissolve the team.', 'TEAM_WOULD_DISSOLVE', {
      detail: 'The step would dissolve the team.',
      error_code: 'TEAM_WOULD_DISSOLVE',
      member_count: 2,
      answer_count: 7,
      result_published: false,
      run_ended: false,
    });
  const refusalText =
    'Mit diesem Schritt wird das Team Die Fugger aufgelöst. Mitglieder: 2, Antworten: 7. Das Team verlässt die Rangliste.';

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getTeam).mockResolvedValue(sourceTeam);
    vi.mocked(listTeams).mockResolvedValue(teamsPage);
    vi.mocked(getRun).mockResolvedValue(run);
    vi.mocked(removeTeam).mockResolvedValue(undefined);
    vi.mocked(removeTeamMember).mockResolvedValue(undefined);
    vi.mocked(moveTeamMember).mockResolvedValue(undefined);
  });

  // The focus is inside role=dialog and on (or in) the element that holds the text.
  const expectFocusOn = (text: string) => {
    const active = document.activeElement as HTMLElement;
    expect(within(screen.getByRole('dialog')).getByText(text)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toContainElement(active);
    expect(active).toHaveTextContent(text);
  };

  it('the plain question of "Team entfernen" takes the focus', async () => {
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));

    await waitFor(() => expectFocusOn('Team Die Fugger wirklich entfernen?'));
  });

  it('the question before a member is removed takes the focus', async () => {
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));

    await waitFor(() => expect(screen.getByRole('dialog')).toContainElement(document.activeElement as HTMLElement));
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toHaveTextContent(/Max Muster/);
  });

  it('the move picker takes the focus', async () => {
    const user = userEvent.setup();
    renderDialog();
    await openMovePicker(user);

    expect(screen.getByRole('dialog')).toContainElement(document.activeElement as HTMLElement);
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toHaveTextContent(/Max Muster/);
  });

  // Back on the list, the focus is on the dialog title, inside role=dialog.
  const expectFocusOnTitle = () => {
    const active = document.activeElement as HTMLElement;
    expect(screen.getByRole('dialog')).toContainElement(active);
    expect(active).toHaveTextContent('Die Fugger verwalten');
  };

  // The refusal is spoken by one polite live region that exists before the
  // step. The focus goes to "Abbrechen", so the sentence is not read twice.
  const expectRefusalSpokenOnce = () => {
    const dialog = screen.getByRole('dialog');
    const regions = within(dialog).getAllByRole('status');
    expect(regions).toHaveLength(1);
    expect(regions[0]).toHaveAttribute('aria-atomic', 'true');
    expect(regions[0]).toHaveTextContent(refusalText);
    expect(regions[0]).toHaveTextContent('Mitglieder: 2, Antworten: 7');
    expect(screen.getAllByText(refusalText)).toHaveLength(1);
    const active = document.activeElement as HTMLElement;
    expect(dialog).toContainElement(active);
    expect(active.tagName).toBe('BUTTON');
    expect(active).toHaveTextContent('Abbrechen');
  };

  it('the refusal after "Team entfernen" on the question is spoken once by a live region', async () => {
    vi.mocked(removeTeam).mockRejectedValueOnce(dissolveRefusal());
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    expect(within(screen.getByRole('dialog')).getAllByRole('status')).toHaveLength(1);
    expect(within(screen.getByRole('dialog')).getByRole('status')).toBeEmptyDOMElement();
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));

    await screen.findByText(refusalText);
    await waitFor(expectRefusalSpokenOnce);
  });

  it('the refusal after "Entfernen" on the member question is spoken once by a live region', async () => {
    vi.mocked(removeTeamMember).mockRejectedValueOnce(dissolveRefusal());
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    expect(within(screen.getByRole('dialog')).getAllByRole('status')).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Entfernen' }));

    await screen.findByText(refusalText);
    await waitFor(expectRefusalSpokenOnce);
  });

  it('a successful "Entfernen" brings the view back to the list with the focus inside the dialog', async () => {
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Entfernen' }));

    await waitFor(() => expect(screen.queryByText('Max Muster')).not.toBeInTheDocument());
    await waitFor(expectFocusOnTitle);
    expect(within(screen.getByRole('dialog')).getByText('Erika Muster')).toBeInTheDocument();
  });

  it.each([
    ['the team question', async (user: ReturnType<typeof userEvent.setup>) => {
      await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    }],
    ['the member question', async (user: ReturnType<typeof userEvent.setup>) => {
      await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
    }],
    ['the move picker', async (user: ReturnType<typeof userEvent.setup>) => {
      await openMovePicker(user);
    }],
  ])('"Abbrechen" on %s brings the view back to the list with the focus inside the dialog', async (_n, step) => {
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await step(user);
    await user.click(screen.getByRole('button', { name: 'Abbrechen' }));

    await screen.findByText('Max Muster');
    await waitFor(expectFocusOnTitle);
  });

  it('"Abbrechen" on the refusal brings the view back to the list with the focus inside the dialog', async () => {
    vi.mocked(removeTeam).mockRejectedValueOnce(dissolveRefusal());
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    await screen.findByText(refusalText);
    await user.click(screen.getByRole('button', { name: 'Abbrechen' }));

    await screen.findByText('Max Muster');
    await waitFor(expectFocusOnTitle);
  });

  it('Escape still closes the dialog from an inner step', async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <TeamMembersDialog
        open
        onClose={onClose}
        orgSlug="test-org"
        runId="run-1"
        teamId="team-1"
        teamName="Die Fugger"
        rallyeCode="JOIN1"
        onMemberRemoved={vi.fn()}
      />,
    );
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    await user.keyboard('{Escape}');

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// A pressed button keeps the focus while its request runs. jsdom does not
// drop the focus of an element that turns disabled, as Chrome does, so these tests assert
// the structure that would drop it: no disabled attribute on the pressed button, the busy
// state in aria-disabled, and a handler that refuses a second call by itself.
describe('TeamMembersDialog — the pressed button keeps the focus while its request runs', () => {
  const dissolveRefusal = () =>
    new ApiError(409, 'The step would dissolve the team.', 'TEAM_WOULD_DISSOLVE', {
      detail: 'The step would dissolve the team.',
      error_code: 'TEAM_WOULD_DISSOLVE',
      member_count: 2,
      answer_count: 7,
      result_published: false,
      run_ended: false,
    });
  const refusalText =
    'Mit diesem Schritt wird das Team Die Fugger aufgelöst. Mitglieder: 2, Antworten: 7. Das Team verlässt die Rangliste.';

  type User = ReturnType<typeof userEvent.setup>;
  const pending = () => {
    let resolve!: () => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<void>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  };

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getTeam).mockResolvedValue(sourceTeam);
    vi.mocked(listTeams).mockResolvedValue(teamsPage);
    vi.mocked(getRun).mockResolvedValue(run);
    vi.mocked(removeTeam).mockResolvedValue(undefined);
    vi.mocked(removeTeamMember).mockResolvedValue(undefined);
  });

  // (a) the focus is on the pressed button inside role=dialog, (b) it is not disabled,
  // (c) it says busy by aria-disabled and keeps its name.
  const expectKeepsFocus = (name: string) => {
    const button = screen.getByRole('button', { name });
    expect(button).toHaveFocus();
    expect(screen.getByRole('dialog')).toContainElement(button);
    expect(document.body).not.toHaveFocus();
    expect(button).not.toBeDisabled();
    expect(button).not.toHaveAttribute('disabled');
    expect(button).toHaveAttribute('aria-disabled', 'true');
    return button;
  };
  // A second click, two clicks in one tick and a second Enter send nothing.
  const pressAgain = async (user: User, button: HTMLElement) => {
    await user.click(button);
    act(() => {
      button.click();
      button.click();
    });
    await user.keyboard('{Enter}');
  };
  const expectTitleFocus = () => {
    expect(screen.getByRole('dialog')).toContainElement(document.activeElement as HTMLElement);
    expect(document.activeElement).toHaveTextContent('Die Fugger verwalten');
  };

  it('the confirm of the question "Team entfernen" keeps the focus and sends once', async () => {
    const call = pending();
    vi.mocked(removeTeam).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));

    const button = expectKeepsFocus('Team entfernen');
    await pressAgain(user, button);
    expect(removeTeam).toHaveBeenCalledTimes(1);
    expectKeepsFocus('Team entfernen');

    await act(async () => call.reject(dissolveRefusal()));
    await screen.findByText(refusalText);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Abbrechen' })).toHaveFocus());
    expect(removeTeam).toHaveBeenCalledTimes(1);
  });

  it('the confirm of the question "Entfernen" keeps the focus and sends once', async () => {
    const call = pending();
    vi.mocked(removeTeamMember).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Entfernen' }));

    const button = expectKeepsFocus('Entfernen');
    await pressAgain(user, button);
    expect(removeTeamMember).toHaveBeenCalledTimes(1);
    expectKeepsFocus('Entfernen');

    await act(async () => call.resolve());
    await waitFor(() => expect(screen.queryByText('Max Muster')).not.toBeInTheDocument());
    expectTitleFocus();
    expect(removeTeamMember).toHaveBeenCalledTimes(1);
  });

  it('the refusal of "Entfernen" leaves the question for "Abbrechen" with the focus inside the dialog', async () => {
    const call = pending();
    vi.mocked(removeTeamMember).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Entfernen' }));
    expectKeepsFocus('Entfernen');

    await act(async () => call.reject(dissolveRefusal()));
    await screen.findByText(refusalText);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Abbrechen' })).toHaveFocus());
  });

  // The refusal's confirm is the only call with confirm_dissolution.
  it.each([
    ['team', 'Team entfernen'],
    ['remove', 'Entfernen'],
  ])('the confirm of the refusal (%s) keeps the focus and sends confirm_dissolution once', async (kind, name) => {
    const confirmed = pending();
    const api = kind === 'team' ? vi.mocked(removeTeam) : vi.mocked(removeTeamMember);
    api.mockRejectedValueOnce(dissolveRefusal()).mockReturnValueOnce(confirmed.promise);
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    if (kind === 'team') {
      await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
      await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    } else {
      await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
      await user.click(screen.getByRole('button', { name: 'Entfernen' }));
    }
    await screen.findByText(refusalText);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Abbrechen' })).toHaveFocus());
    await user.click(screen.getByRole('button', { name }));
    expect(api).toHaveBeenCalledTimes(2);

    const button = expectKeepsFocus(name);
    await pressAgain(user, button);
    expect(api).toHaveBeenCalledTimes(2);
    expect(api.mock.calls.filter((c) => c.at(-1) === true)).toHaveLength(1);
    expectKeepsFocus(name);

    await act(async () => confirmed.resolve());
    await waitFor(() => expect(screen.queryByText(refusalText)).not.toBeInTheDocument());
    await waitFor(expectTitleFocus);
    expect(api.mock.calls.filter((c) => c.at(-1) === true)).toHaveLength(1);
  });

  // The request has ended and its result is queued, but React has not rendered it yet:
  // the DOM still shows the busy button with the handler of the busy render. A press here
  // must not meet the native disabled attribute. The handler itself has to refuse it.
  const pressBeforeTheResultRenders = async (settle: () => void, button: HTMLElement) => {
    const env = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    env.IS_REACT_ACT_ENVIRONMENT = false;
    try {
      settle();
      for (let i = 0; i < 10; i++) await Promise.resolve();
      // The render of the result has not committed: the old busy button is still there.
      expect(button).toBeInTheDocument();
      expect(button).toHaveAttribute('aria-disabled', 'true');
      button.click();
    } finally {
      env.IS_REACT_ACT_ENVIRONMENT = true;
    }
    await act(async () => {});
  };

  const toRefusal = async (user: User, api: ReturnType<typeof vi.mocked<typeof removeTeam>>) => {
    api.mockRejectedValueOnce(dissolveRefusal());
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    await screen.findByText(refusalText);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Abbrechen' })).toHaveFocus());
  };

  it('a press between the result and its render sends no second confirm_dissolution', async () => {
    const confirmed = pending();
    const api = vi.mocked(removeTeam);
    const user = userEvent.setup();
    await toRefusal(user, api);
    api.mockReturnValueOnce(confirmed.promise);
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    const button = expectKeepsFocus('Team entfernen');
    expect(api).toHaveBeenCalledTimes(2);

    await pressBeforeTheResultRenders(() => confirmed.resolve(), button);
    expect(api).toHaveBeenCalledTimes(2);
    expect(api.mock.calls.filter((c) => c.at(-1) === true)).toHaveLength(1);
  });

  it('a press between a failed result and its render sends no second request', async () => {
    const call = pending();
    vi.mocked(removeTeamMember).mockReturnValueOnce(call.promise);
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Max Muster aus dem Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Entfernen' }));
    const button = expectKeepsFocus('Entfernen');

    await pressBeforeTheResultRenders(() => call.reject(new ApiError(500, 'boom')), button);
    expect(removeTeamMember).toHaveBeenCalledTimes(1);
    // The next render is free again.
    expect(screen.getByRole('button', { name: 'Entfernen' })).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('two clicks in one tick from idle on the confirm of the refusal send one confirm_dissolution', async () => {
    const confirmed = pending();
    const api = vi.mocked(removeTeam);
    const user = userEvent.setup();
    await toRefusal(user, api);
    api.mockReturnValueOnce(confirmed.promise);
    const button = screen.getByRole('button', { name: 'Team entfernen' });
    expect(button).not.toHaveAttribute('aria-disabled', 'true');
    act(() => {
      button.click();
      button.click();
    });
    expect(api).toHaveBeenCalledTimes(2);
    expect(api.mock.calls.filter((c) => c.at(-1) === true)).toHaveLength(1);
    await act(async () => confirmed.resolve());
  });

  it('a new refusal after the confirm takes the focus to "Abbrechen" and says the new numbers', async () => {
    const api = vi.mocked(removeTeam);
    const user = userEvent.setup();
    await toRefusal(user, api);
    api.mockRejectedValueOnce(
      new ApiError(409, 'The step would dissolve the team.', 'TEAM_WOULD_DISSOLVE', {
        detail: 'The step would dissolve the team.',
        error_code: 'TEAM_WOULD_DISSOLVE',
        member_count: 5,
        answer_count: 9,
        result_published: false,
        run_ended: false,
      }),
    );
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));

    const dialog = screen.getByRole('dialog');
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Abbrechen' })).toHaveFocus());
    expect(within(dialog).getByRole('status')).toHaveTextContent(/Mitglieder: 5, Antworten: 9/);
    expect(api).toHaveBeenCalledTimes(2);
    // One more Enter does not confirm the facts the person has not heard.
    await user.keyboard('{Enter}');
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('a refusal that fails again leaves the focus on the confirm button inside the dialog', async () => {
    const confirmed = pending();
    vi.mocked(removeTeam)
      .mockRejectedValueOnce(dissolveRefusal())
      .mockReturnValueOnce(confirmed.promise);
    const user = userEvent.setup();
    renderDialog();
    await screen.findByText('Max Muster');
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    await screen.findByText(refusalText);
    await user.click(screen.getByRole('button', { name: 'Team entfernen' }));
    expectKeepsFocus('Team entfernen');

    await act(async () => confirmed.reject(new ApiError(500, 'boom')));
    const button = screen.getByRole('button', { name: 'Team entfernen' });
    expect(button).toHaveFocus();
    expect(button).not.toHaveAttribute('aria-disabled', 'true');
  });
});
