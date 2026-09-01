import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router';
import { axe } from 'vitest-axe';
import ParticipantLeaderboard from './Leaderboard';
import TeamTab from '../../layouts/TeamTab';
import { AuthContext } from '../../auth/AuthContext';
import { getMyResultLink, getScoreboard } from '../../api/runs';
import type {
  RunState,
  Scoreboard,
  ScoreboardEntry,
  Team,
} from '../../api/types';
import type { PublicRunState } from '../../services/publicRuns';

vi.mock('../../api/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/runs')>();
  return {
    ...actual,
    getScoreboard: vi.fn(),
    getMyResultLink: vi.fn().mockResolvedValue({ status: 'unpublished' }),
  };
});

const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const makeTeam = (memberCount: number): Team => ({
  id: 'team-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  name: 'Die Fugger',
  color: null,
  member_count: memberCount,
  share_code: 'ABC123',
  coordinator_chat_open: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

const makeEntry = (
  over: Partial<ScoreboardEntry> & Pick<ScoreboardEntry, 'team_id' | 'team_name'>,
): ScoreboardEntry => ({
  total_points: 20,
  total_max_points: 40,
  scored_count: 2,
  last_scored_at: null,
  rank: 1,
  is_winner: false,
  ...over,
});

// The scoreboard reports whether the standing is final. The banner keys off it
// together with the run state from the outlet context.
const unscoredScoreboard: Scoreboard = {
  run_id: 'run-1',
  entries: [makeEntry({ team_id: 'team-1', team_name: 'Die Fugger' })],
  is_final: false,
  scored_count: 2,
  submission_count: 3,
};

const finalScoreboard: Scoreboard = {
  run_id: 'run-1',
  entries: [
    makeEntry({ team_id: 'team-1', team_name: 'Die Fugger', is_winner: true }),
  ],
  is_final: true,
  scored_count: 3,
  submission_count: 3,
};

const makeRun = (state: RunState, gradingComplete: boolean): PublicRunState => ({
  run_id: 'run-1',
  name: 'Rallye durch Augsburg',
  state,
  scheduled_for: null,
  started_at: '2026-01-01T09:00:00Z',
  paused_at: null,
  ended_at: state === 'ended' ? '2026-01-01T12:00:00Z' : null,
  grading_complete: gradingComplete,
});

// The leaderboard only ever renders inside TeamTab, and the sub-tab above it is
// what names it. The heading assertions therefore need the layout, not the page alone.
const renderTeamTab = (team: Team, run: PublicRunState | null = null) =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/participant/team/leaderboard']}>
      <AuthContext.Provider
        value={{
          user: authUser,
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
            path="/runs/:runId/participant"
            element={<Outlet context={{ team, run }} />}
          >
            <Route path="team" element={<TeamTab />}>
              <Route path="leaderboard" element={<ParticipantLeaderboard />} />
            </Route>
          </Route>
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('ParticipantLeaderboard inside the team tab', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    vi.mocked(getScoreboard).mockResolvedValue(unscoredScoreboard);
  });

  // A visible heading must not repeat the tab label, and a page needs one h1.
  // The tab still names the view, so the h1 carries that name visually hidden.
  it('has exactly one h1, the name of the tab, visually hidden', async () => {
    renderTeamTab(makeTeam(1));

    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Rangliste' })).toBeInTheDocument();
    const h1 = screen.getByRole('heading', { level: 1, name: 'Rangliste' });
    expect(h1).toHaveStyle({ position: 'absolute', width: '1px', overflow: 'hidden' });
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  });

  it('shows the expanded team-code panel while the team is alone', async () => {
    renderTeamTab(makeTeam(1));

    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    expect(screen.getByText('Dein Team-Code zum Teilen:')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Code kopieren' }),
    ).toBeInTheDocument();
  });

  // The panel is the founder's tool. With a second member it has done its job and
  // shrinks to one line, so the leaderboard starts near the top.
  it('collapses the team-code panel to one line once the team has a second member', async () => {
    renderTeamTab(makeTeam(2));

    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    expect(screen.queryByText('Dein Team-Code zum Teilen:')).toBeNull();
    expect(screen.getByText('Team-Code')).toBeInTheDocument();
    expect(screen.getByText('ABC123')).toBeInTheDocument();

    const copy = screen.getByRole('button', { name: 'Code kopieren' });
    expect(copy).toHaveStyle({ minHeight: '44px' });
  });

  it('collapses the team-code panel after the code was copied in this session', async () => {
    const user = userEvent.setup();
    renderTeamTab(makeTeam(1));

    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Code kopieren' }));

    expect(screen.queryByText('Dein Team-Code zum Teilen:')).toBeNull();
    expect(screen.getByText('Team-Code')).toBeInTheDocument();
    expect(screen.getByText('ABC123')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Code kopieren' }),
    ).toHaveStyle({ minHeight: '44px' });
  });

  // The own-team highlight follows the team the shell hands down (it switches on
  // team_member_moved), not a sessionStorage id cached at mount.
  it('marks the outlet team as your own team', async () => {
    vi.mocked(getScoreboard).mockResolvedValue({
      ...unscoredScoreboard,
      entries: [
        makeEntry({ team_id: 'team-1', team_name: 'Die Fugger' }),
        makeEntry({ team_id: 'team-2', team_name: 'Die Welser', rank: 2 }),
      ],
    });
    renderTeamTab({ ...makeTeam(2), id: 'team-2' });

    expect(await screen.findByText('Die Welser')).toBeInTheDocument();
    // The label renders as "· Dein Team" next to the team name.
    const ownRow = screen.getByText(/Dein Team/).closest('.MuiCard-root');
    expect(ownRow).toHaveTextContent('Die Welser');
    expect(ownRow).not.toHaveTextContent('Die Fugger');
  });
});

describe('ParticipantLeaderboard standing states', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    sessionStorage.clear();
    vi.mocked(getScoreboard).mockResolvedValue(unscoredScoreboard);
  });

  // Ended run with at least one unscored submission.
  it('shows the interim banner when the run has ended but grading continues', async () => {
    renderTeamTab(makeTeam(1), makeRun('ended', false));

    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    expect(
      screen.getByText('Zwischenstand, die Bewertung läuft noch.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Endstand, die Bewertung ist abgeschlossen.')).toBeNull();
    expect(screen.queryByText(/Gewinner/)).toBeNull();
    // Waiting for scoring is blue with the hourglass, not the orange of a warning.
    // The sentence names the state, the icon is hidden.
    const banner = screen.getByRole('alert');
    expect(banner).toHaveClass('MuiAlert-colorInfo');
    expect(banner.querySelector('[data-testid="HourglassEmptyIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
  });

  // Ended run with every submission scored.
  it('shows the final banner and the winner label once grading is complete', async () => {
    vi.mocked(getScoreboard).mockResolvedValue(finalScoreboard);
    renderTeamTab(makeTeam(1), makeRun('ended', true));

    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    expect(
      screen.getByText('Endstand, die Bewertung ist abgeschlossen.'),
    ).toBeInTheDocument();
    // The label renders as "· Gewinner" next to the team name — match the word.
    expect(screen.getByText(/Gewinner/)).toBeInTheDocument();
    expect(screen.queryByText('Zwischenstand, die Bewertung läuft noch.')).toBeNull();
    const banner = screen.getByRole('alert');
    expect(banner).toHaveClass('MuiAlert-colorSuccess');
    expect(banner.querySelector('[data-testid="CheckCircleIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
  });

  // The people who played open the published result from here.
  // The button leaves with a withdrawal.
  it('offers the published result link after the end, and only while it lives', async () => {
    vi.mocked(getScoreboard).mockResolvedValue(finalScoreboard);
    vi.mocked(getMyResultLink).mockResolvedValue({
      status: 'published',
      url: 'https://clavis.example/results#abc',
      published_at: '2026-01-01T12:00:00Z',
      expires_at: '2026-04-01T12:00:00Z',
    } as never);
    renderTeamTab(makeTeam(1), makeRun('ended', true));

    const open = await screen.findByRole('link', { name: 'Öffentliches Ergebnis öffnen' });
    expect(open).toHaveAttribute('href', 'https://clavis.example/results#abc');
    expect(open).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('button', { name: 'Link kopieren' })).toBeInTheDocument();
  });

  it('shows no result link while it is unpublished', async () => {
    vi.mocked(getScoreboard).mockResolvedValue(finalScoreboard);
    vi.mocked(getMyResultLink).mockResolvedValue({ status: 'unpublished' } as never);
    renderTeamTab(makeTeam(1), makeRun('ended', true));

    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Öffentliches Ergebnis öffnen' })).not.toBeInTheDocument();
  });

  // Active run: no banner, and no winner labels either.
  it('shows no banner while the run is active', async () => {
    renderTeamTab(makeTeam(1), makeRun('active', false));

    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    expect(screen.queryByText(/Zwischenstand|Endstand/)).toBeNull();
    expect(screen.queryByText(/Gewinner/)).toBeNull();
  });

  // Equal points share the rank. The API assigns 1, 1, 3 and the rows must not
  // re-number themselves by list index.
  it('renders the shared ranks the API assigned instead of the row index', async () => {
    vi.mocked(getScoreboard).mockResolvedValue({
      ...finalScoreboard,
      entries: [
        makeEntry({ team_id: 'team-1', team_name: 'Die Fugger', is_winner: true }),
        makeEntry({ team_id: 'team-2', team_name: 'Die Welser', is_winner: true }),
        makeEntry({
          team_id: 'team-3',
          team_name: 'Die Kaufleute',
          total_points: 10,
          total_max_points: 30,
          rank: 3,
        }),
      ],
    });
    renderTeamTab(makeTeam(1), makeRun('ended', true));

    expect(await screen.findByText('Die Kaufleute')).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: 'Platz 1' })).toHaveLength(2);
    expect(screen.getByRole('img', { name: 'Platz 3' })).toBeInTheDocument();
    // Every rank-1 team carries the winner mark — also when the rank is shared.
    expect(screen.getAllByText(/Gewinner/)).toHaveLength(2);
    // Rank 3 carries its number, not the trophy.
    expect(screen.getByRole('img', { name: 'Platz 3' }).querySelector('svg')).toBeNull();
  });

  // The rank avatar must not carry aria-label on a div without a role
  // (axe aria-prohibited-attr).
  it('has no axe violations on the final standing', async () => {
    vi.mocked(getScoreboard).mockResolvedValue(finalScoreboard);
    const { container } = renderTeamTab(makeTeam(1), makeRun('ended', true));

    expect(await screen.findByText(/Gewinner/)).toBeInTheDocument();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The shell hears team_dissolved on the run channel and says so to the open page.
  // The standing loads again at once, without the wait for the next refresh.
  it('loads the standing again when another team of the run is dissolved', async () => {
    vi.mocked(getScoreboard).mockResolvedValueOnce({
      ...unscoredScoreboard,
      entries: [
        makeEntry({ team_id: 'team-2', team_name: 'Die Welser', total_points: 30 }),
        makeEntry({ team_id: 'team-1', team_name: 'Die Fugger', rank: 2 }),
      ],
    });
    renderTeamTab(makeTeam(2));

    expect(await screen.findByText('Die Welser')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Platz 2' })).toBeInTheDocument();
    expect(getScoreboard).toHaveBeenCalledTimes(1);

    act(() => {
      window.dispatchEvent(
        new CustomEvent('clavis:team-dissolved', {
          detail: { run_id: 'run-1', team_id: 'team-2' },
        }),
      );
    });

    await waitFor(() => expect(screen.queryByText('Die Welser')).toBeNull());
    expect(getScoreboard).toHaveBeenCalledTimes(2);
    expect(screen.getByText('Die Fugger')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Platz 1' })).toBeInTheDocument();
  });

  it('stops listening for a dissolved team once the page is gone', async () => {
    const { unmount } = renderTeamTab(makeTeam(2));
    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    unmount();

    window.dispatchEvent(new CustomEvent('clavis:team-dissolved'));

    expect(getScoreboard).toHaveBeenCalledTimes(1);
  });
});
