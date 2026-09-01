import type React from 'react';
import { useState } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation, useOutletContext } from 'react-router';
import userEvent from '@testing-library/user-event';
import RunShell from './RunShell';
import FeedView from '../pages/participant/FeedView';
import TeamTab from './TeamTab';
import TeamInfo from '../pages/participant/TeamInfo';
import TeamChat from '../pages/participant/TeamChat';
import { AuthContext } from '../auth/AuthContext';
import apiClient, { ApiError } from '../api/client';
import type { AxiosRequestConfig } from 'axios';
import { useWebSocket } from '../hooks/useWebSocket';
import { useInParticipantTargets } from '../components/ParticipantTargets';
import { __resetNoticeStoreForTests } from '../hooks/useUnreadNotices';
import type { FeedEvent, FeedEventType, Team } from '../api/types';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

vi.mock('../hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

// CountdownTimer depends on real time — stub it out so the countdown tests
// can drive the clock with fake timers and only assert on appearance.
vi.mock('../components/CountdownTimer', () => ({
  default: ({ targetTime }: { targetTime: string }) => (
    <div data-testid="header-countdown">{targetTime}</div>
  ),
}));

const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const makeTeam = (id: string, name: string): Team => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  name,
  color: null,
  member_count: 2,
  share_code: 'ABC123',
  coordinator_chat_open: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

/** Renders the team the shell hands down — the surface chat, team info and
 *  leaderboard follow when a move switches it. */
const TeamProbe = () => {
  const { team } = useOutletContext<{ team: Team | null }>();
  return <div>{team ? team.name : 'kein Team'}</div>;
};

// The outlet's `key={team?.id}` remounts the routed page on a team switch.
// `useState`'s lazy initializer runs once per mount, never on a rerender. So a
// fresh count on screen after the move proves a remount. A plain rerender of
// the same instance would leave it unchanged.
let mountProbeCount = 0;
const MountCountProbe = () => {
  const { team } = useOutletContext<{ team: Team | null }>();
  const [mountedAs] = useState(() => ++mountProbeCount);
  return <div>{`mounts:${mountedAs} team:${team?.id ?? 'none'}`}</div>;
};

// Like MountCountProbe, but it also carries a draft. One probe proves that a
// final switch remounts exactly once, and that what the participant typed
// right after survives a second fetch that only confirms the same team.
let switchProbeMountCount = 0;
const TeamSwitchProbe = () => {
  const { team } = useOutletContext<{ team: Team | null }>();
  const [mountedAs] = useState(() => ++switchProbeMountCount);
  const [draft, setDraft] = useState('');
  return (
    <div>
      <div>{`mounts:${mountedAs} team:${team?.id ?? 'none'}`}</div>
      <input
        aria-label="Chat-Entwurf"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
    </div>
  );
};

// A page with local state that the participant built up before the team
// arrived. The draft lives as long as the page instance, so it survives the
// team landing and dies with a remount.
const ChatDraftProbe = () => {
  const { team } = useOutletContext<{ team: Team | null }>();
  const [draft, setDraft] = useState('');
  return (
    <div>
      <div>{team ? team.name : 'kein Team'}</div>
      <input
        aria-label="Chat-Entwurf"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
    </div>
  );
};

const renderShell = (probe?: React.ReactElement, feedElement?: React.ReactElement) =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/participant']}>
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
          <Route path="/runs/:runId/participant" element={<RunShell />}>
            {probe && <Route index element={probe} />}
            {/* Target routes for the bottom nav — the shell derives its
                active tab from them. */}
            <Route path="feed" element={feedElement ?? probe ?? <div />} />
            <Route path="stations" element={probe ?? <div />} />
          </Route>
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

/** Routes apiClient.get by URL: the feed endpoint resolves to `items`,
 *  everything else (team fetches, run state) stays offline. Used for the
 *  tests that exercise the shell's own initial feed load or FeedView's
 *  real load-then-mark-seen flow. */
const mockFeedList = (items: FeedEvent[]) => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url.includes('/feed/')) {
      return Promise.resolve({ data: { items, total: items.length, skip: 0, limit: 100 } });
    }
    return Promise.reject(new Error('offline'));
  });
};

// Route apiClient.get by URL so the shell's team fetches stay addressable
// next to the run-state fetch that fires on the same mount.
const mockTeamsByUrl = (teams: Record<string, Team>) => {
  vi.mocked(apiClient.get).mockImplementation((url) => {
    for (const [teamId, team] of Object.entries(teams)) {
      if (url.endsWith(`/teams/${teamId}`)) return Promise.resolve({ data: team });
    }
    return Promise.reject(new Error('offline'));
  });
};

const fireMoveEvent = (over: Partial<{ user_id: string; to_team_id: string }>) => {
  const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
  act(() => {
    handlers.team_member_moved?.(
      {
        run_id: 'run-1',
        member_id: 'm-1',
        user_id: 'u1',
        display_name: 'Teilnehmer',
        from_team_id: 'team-1',
        to_team_id: 'team-2',
        ...over,
      },
      { type: 'team_member_moved', data: null, timestamp: '2026-01-01T10:00:00Z' },
    );
  });
};

const makeFeedEvent = (
  id: string,
  type: FeedEventType,
  content: Record<string, unknown>,
  createdAt = '2026-06-01T12:00:00Z',
): FeedEvent => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  event_type: type,
  content,
  author_id: null,
  target_team_id: null,
  created_at: createdAt,
});

// Mirrors the 1000-per-call page size RunShell's history load pages with —
// kept local to the test since it is an implementation constant, not an
// export.
const FEED_PAGE_LIMIT = 1000;

const fillerEvents = (count: number, createdAt: string, idPrefix: string): FeedEvent[] =>
  Array.from({ length: count }, (_, i) => makeFeedEvent(`${idPrefix}-${i}`, 'milestone', {}, createdAt));

/** Routes apiClient.get by URL and by the `skip` query param, one `pages[i]`
 *  per 1000-sized page — the shell's history load requests skip=0, 1000, …
 *  in turn until it stops. Everything else (team fetches, run state) stays
 *  offline, matching mockFeedList/mockTeamsByUrl. */
const mockFeedPages = (pages: FeedEvent[][]) => {
  vi.mocked(apiClient.get).mockImplementation(
    (url: string, config?: AxiosRequestConfig) => {
      if (url.includes('/feed/')) {
        const params = config?.params as { skip?: number } | undefined;
        const skip = params?.skip ?? 0;
        const items = pages[Math.floor(skip / FEED_PAGE_LIMIT)] ?? [];
        return Promise.resolve({ data: { items, total: items.length, skip, limit: FEED_PAGE_LIMIT } });
      }
      return Promise.reject(new Error('offline'));
    },
  );
};

const feedCallCount = () =>
  vi.mocked(apiClient.get).mock.calls.filter(([url]) => (url as string).includes('/feed/')).length;

const fireFeedEvent = (event: FeedEvent) => {
  const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
  act(() => {
    handlers.feed_event?.(event, {
      type: 'feed_event',
      data: event,
      timestamp: event.created_at,
    });
  });
};

describe('RunShell', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    __resetNoticeStoreForTests();
    mountProbeCount = 0;
    switchProbeMountCount = 0;
    vi.mocked(apiClient.get).mockRejectedValue(new Error('offline'));
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  // The frame wraps its page in the participant target sizes.
  it('renders its page inside the participant target sizes', async () => {
    const Probe = () => <p>{useInParticipantTargets() ? 'inside' : 'outside'}</p>;
    renderShell(<Probe />);
    expect(await screen.findByText('inside')).toBeInTheDocument();
  });

  // The overflow button is the large size, which keeps the 44 px touch target.
  // The class is the assertion, because jsdom lays nothing out.
  it('renders the header overflow button in the large size', () => {
    renderShell();

    expect(screen.getByRole('button', { name: 'Mehr' })).toHaveClass('MuiIconButton-sizeLarge');
  });

  // While the run state is loading or unavailable, the header falls back to the
  // brand wordmark from the instance config.
  it('falls back to the brand wordmark in the header', () => {
    renderShell();

    expect(screen.getByText('Clavis')).toBeInTheDocument();
  });

  // The moved participant's open app follows to the new team without a reload:
  // the run-WS event for this user re-points the cached membership, and the
  // shell hands the new team down via the outlet, where chat, team info and
  // leaderboard pick it up.
  it('switches to the new team on team_member_moved for the current user', async () => {
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    mockTeamsByUrl({
      'team-1': makeTeam('team-1', 'Die Fugger'),
      'team-2': makeTeam('team-2', 'Die Welser'),
    });

    renderShell(<TeamProbe />);
    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();

    fireMoveEvent({});

    expect(await screen.findByText('Die Welser')).toBeInTheDocument();
    expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-2');
    // The participant learns why their chat just changed teams.
    expect(
      screen.getByText('Du wurdest in das Team „Die Welser“ verschoben.'),
    ).toBeInTheDocument();
  });

  // The run channel carries every move of the run — only the moved member's
  // own client may switch.
  it('ignores team_member_moved for another member', async () => {
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    mockTeamsByUrl({
      'team-1': makeTeam('team-1', 'Die Fugger'),
      'team-2': makeTeam('team-2', 'Die Welser'),
    });

    renderShell(<TeamProbe />);
    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();

    fireMoveEvent({ user_id: 'someone-else' });

    expect(screen.queryByText('Die Welser')).toBeNull();
    expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-1');
  });

  // A page below the outlet (e.g. StationList) does not depend on the team
  // itself. Without a remount it would show the old team's data until its own
  // periodic refresh. Keying the outlet by team id remounts the routed page on
  // a switch, so it loads its data again as on any mount.
  it('remounts the routed child page on team_member_moved for the current user', async () => {
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    mockTeamsByUrl({
      'team-1': makeTeam('team-1', 'Die Fugger'),
      'team-2': makeTeam('team-2', 'Die Welser'),
    });

    renderShell(<MountCountProbe />);
    const before = await screen.findByText(/team:team-1$/);
    const countBefore = Number(before.textContent!.match(/mounts:(\d+)/)![1]);

    fireMoveEvent({});

    // A count that advances (not a rerender of the same instance still
    // reporting its first mount) is the proof the page remounted.
    const after = await screen.findByText(/team:team-2$/);
    const countAfter = Number(after.textContent!.match(/mounts:(\d+)/)![1]);
    expect(countAfter).toBeGreaterThan(countBefore);
  });

  // The team id lands only when getTeam resolves, after the routed pages have
  // already mounted. Keying the outlet by it would remount every page on that
  // first load and take whatever the participant had opened or typed with it.
  // The key must stay put while the initial load (and any refetch of the same
  // team) lands, and rise only on a real move.
  it('keeps a typed draft when the team loads after the page mounted, and remounts the page on a move', async () => {
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    let resolveTeam1: ((value: { data: Team }) => void) | undefined;
    const team1Response = new Promise<{ data: Team }>((resolve) => {
      resolveTeam1 = resolve;
    });
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/teams/team-1')) return team1Response;
      if (url.endsWith('/teams/team-2')) {
        return Promise.resolve({ data: makeTeam('team-2', 'Die Welser') });
      }
      return Promise.reject(new Error('offline'));
    });

    renderShell(<ChatDraftProbe />);

    // The page is up — still without a team — and the participant types
    // before getTeam ever resolves.
    const draft = await screen.findByLabelText('Chat-Entwurf');
    fireEvent.change(draft, { target: { value: 'Sehr geehrtes Team' } });
    expect(draft).toHaveValue('Sehr geehrtes Team');

    await act(async () => {
      resolveTeam1!({ data: makeTeam('team-1', 'Die Fugger') });
      await Promise.resolve();
    });

    // The initial load landed, and with it the team in the outlet context —
    // but the page instance (and its draft) survived it.
    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    expect(screen.getByLabelText('Chat-Entwurf')).toHaveValue('Sehr geehrtes Team');

    // Only a real switch remounts: the fresh page starts without the draft.
    fireMoveEvent({});
    expect(await screen.findByText('Die Welser')).toBeInTheDocument();
    expect(screen.getByLabelText('Chat-Entwurf')).toHaveValue('');
  });

  // A move back to the rendered team must win over a slower fetch for an
  // intermediate target still in flight. Comparing a resolving fetch against
  // the *rendered* team would let a stale response for an already-reverted move
  // switch the participant to a team they had been moved out of again.
  it('a move back to the rendered team wins over a still-pending fetch for an earlier target', async () => {
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    let resolveTeam2: ((value: { data: Team }) => void) | undefined;
    const team2Response = new Promise<{ data: Team }>((resolve) => {
      resolveTeam2 = resolve;
    });
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/teams/team-1')) {
        return Promise.resolve({ data: makeTeam('team-1', 'Die Fugger') });
      }
      if (url.endsWith('/teams/team-2')) return team2Response;
      return Promise.reject(new Error('offline'));
    });

    renderShell(<TeamProbe />);
    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();

    // Moved to team-2 — its fetch stays pending — then moved right back to
    // team-1 before that fetch ever resolves.
    fireMoveEvent({ to_team_id: 'team-2' });
    fireMoveEvent({ to_team_id: 'team-1' });

    // The stale team-2 fetch now resolves. It must not apply.
    await act(async () => {
      resolveTeam2!({ data: makeTeam('team-2', 'Die Welser') });
      await Promise.resolve();
    });

    expect(screen.getByText('Die Fugger')).toBeInTheDocument();
    expect(screen.queryByText('Die Welser')).toBeNull();
    expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-1');
  });

  // A→B→A→B with both B fetches (the one from the first A→B and the one from the
  // second) still pending. Both resolve after the last move event and both pass
  // the supersede check. Counting every resolution that matches the latest
  // target would bump the switch counter twice, remount the page a second time
  // and erase what the participant had just entered after the first.
  it('remounts once for a final switch when two pending fetches for the same team resolve after it', async () => {
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    const team1 = makeTeam('team-1', 'Die Fugger');
    const team2 = makeTeam('team-2', 'Die Welser');
    const resolvers: Array<(value: { data: Team }) => void> = [];
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/teams/team-1')) return Promise.resolve({ data: team1 });
      if (url.endsWith('/teams/team-2')) {
        return new Promise((resolve) => { resolvers.push(resolve); });
      }
      return Promise.reject(new Error('offline'));
    });

    renderShell(<TeamSwitchProbe />);
    const initial = await screen.findByText(/team:team-1$/);
    const mountsInitial = Number(initial.textContent!.match(/mounts:(\d+)/)![1]);

    fireMoveEvent({ to_team_id: 'team-2' }); // A → B, fetch #1 for team-2 starts and stays pending
    fireMoveEvent({ to_team_id: 'team-1' }); // B → A, already rendered — no fetch
    fireMoveEvent({ to_team_id: 'team-2' }); // A → B again, fetch #2 for team-2 starts and stays pending
    expect(resolvers).toHaveLength(2);

    // Fetch #1 resolves first: the real switch, one remount.
    await act(async () => {
      resolvers[0]({ data: team2 });
      await Promise.resolve();
    });
    const afterFirst = await screen.findByText(/team:team-2$/);
    const mountsAfterFirst = Number(afterFirst.textContent!.match(/mounts:(\d+)/)![1]);
    expect(mountsAfterFirst).toBeGreaterThan(mountsInitial);

    fireEvent.change(screen.getByLabelText('Chat-Entwurf'), { target: { value: 'Hallo Team' } });

    // Fetch #2 resolves with the same team: must not remount again.
    await act(async () => {
      resolvers[1]({ data: team2 });
      await Promise.resolve();
    });
    const afterSecond = await screen.findByText(/team:team-2$/);
    const mountsAfterSecond = Number(afterSecond.textContent!.match(/mounts:(\d+)/)![1]);
    expect(mountsAfterSecond).toBe(mountsAfterFirst);
    expect(screen.getByLabelText('Chat-Entwurf')).toHaveValue('Hallo Team');
  });

  // A coordination notice that lands while the participant is on another tab
  // still reaches them: the activity tab lights up its unread badge from the
  // run WS event, and opening the tab clears it once FeedView's own load
  // (mounted here for real) has landed.
  it('badges the activity tab for a notice pushed on another tab and clears it on open', async () => {
    mockFeedList([]);
    renderShell(undefined, <FeedView />);
    fireFeedEvent(makeFeedEvent('n1', 'announcement', { de: 'Neue Mitteilung', en: 'New announcement' }));

    expect(
      screen.getByRole('button', { name: 'Mitteilungen, 1 neue Meldung' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Mitteilungen, 1 neue Meldung' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Mitteilungen' })).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: 'Mitteilungen, 1 neue Meldung' })).toBeNull();
  });

  // The shell mounts on every run tab, so it loads the feed itself instead of
  // relying on FeedView (mounted only on the activity tab) to seed the shared
  // registry. A run opened straight on the Rallye tab still shows the badge and
  // the header countdown from the list, without a visit to the activity tab or a
  // live WS event first.
  it('fills the unread badge and the header countdown from the list on a non-activity tab', async () => {
    mockFeedList([
      makeFeedEvent('c1', 'countdown', {
        de: 'Endspurt!',
        en: 'Final sprint!',
        target_time: '2099-01-01T00:00:00Z',
      }),
    ]);

    renderShell();

    expect(
      await screen.findByRole('button', { name: 'Mitteilungen, 1 neue Meldung' }),
    ).toBeInTheDocument();
    expect(await screen.findByTestId('header-countdown')).toHaveTextContent('2099-01-01T00:00:00Z');
  });

  // The overflow menu's "Abmelden" asks a guest first.
  it('asks a guest before the sign-out from the overflow menu', async () => {
    const logout = vi.fn().mockResolvedValue(undefined);
    render(
      <MemoryRouter initialEntries={['/runs/run-1/participant']}>
        <AuthContext.Provider
          value={{
            user: { ...authUser, role: 'guest' as const },
            isAuthenticated: true,
            isLoading: false,
            login: vi.fn(),
            logout,
            checkAuth: vi.fn(),
            needsTwoFactorSetup: () => false,
          }}
        >
          <Routes>
            <Route path="/runs/:runId/participant" element={<RunShell />} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Mehr' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Abmelden' }));

    const dialog = await screen.findByRole('dialog', { name: 'Als Gast abmelden?' });
    expect(logout).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Abmelden' }));
    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1));
  });

  // A countdown whose target is still ahead stops showing the moment the run
  // ends, live and after a reload.
  it('removes the header countdown when the run ends', async () => {
    let runState = 'active';
    const countdown = makeFeedEvent('c1', 'countdown', {
      text: 'Treffpunkt am Rathaus',
      target_time: '2099-01-01T00:00:00Z',
    });
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/feed/')) {
        return Promise.resolve({ data: { items: [countdown], total: 1, skip: 0, limit: 100 } });
      }
      if (url.endsWith('/state')) {
        return Promise.resolve({
          data: { run_id: 'run-1', name: 'Lauf', state: runState, scheduled_for: null, started_at: null, paused_at: null, ended_at: null },
        });
      }
      return Promise.reject(new Error('offline'));
    });

    const view = renderShell();
    expect(await screen.findByTestId('header-countdown')).toBeInTheDocument();

    runState = 'ended';
    const stateHandlers = vi
      .mocked(useWebSocket)
      .mock.calls.map(([options]) => options.handlers)
      .filter((handlers) => handlers?.run_state_change)
      .at(-1)!;
    act(() => {
      stateHandlers.run_state_change!(
        { run_id: 'run-1', organization_id: 'org-1', previous_state: 'active', new_state: 'ended', changed_at: null },
        { type: 'run_state_change', data: {}, timestamp: '2026-06-01T12:00:00Z' },
      );
    });
    await waitFor(() => expect(screen.queryByTestId('header-countdown')).toBeNull());

    // After a reload the run is already ended when the list arrives.
    view.unmount();
    __resetNoticeStoreForTests();
    renderShell();
    await screen.findByRole('button', { name: 'Mitteilungen, 1 neue Meldung' });
    await waitFor(() => expect(vi.mocked(apiClient.get).mock.calls.some(([u]) => (u as string).endsWith('/state'))).toBe(true));
    await waitFor(() => expect(screen.queryByTestId('header-countdown')).toBeNull());
  });

  // A notice that arrives while the participant is already watching the
  // activity tab is seen the moment it lands — the badge must not light up
  // under their eyes.
  it('does not badge a notice that arrives on the open activity tab', () => {
    renderShell();

    fireEvent.click(screen.getByRole('button', { name: 'Mitteilungen' }));
    fireFeedEvent(makeFeedEvent('n1', 'announcement', { de: 'Neue Mitteilung', en: 'New announcement' }));

    expect(screen.getByRole('button', { name: 'Mitteilungen' })).toBeInTheDocument();
  });

  // The running countdown rides in the app bar wherever the participant is, and
  // there is nothing after its target time.
  it('shows the active countdown in the app bar until its target time passes', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-06-01T12:00:00Z'));
    try {
      renderShell();
      expect(screen.queryByTestId('header-countdown')).toBeNull();

      fireFeedEvent(
        makeFeedEvent('c1', 'countdown', {
          de: 'Endspurt!',
          en: 'Final sprint!',
          target_time: '2026-06-01T12:10:00Z',
        }),
      );

      expect(screen.getByTestId('header-countdown')).toHaveTextContent('2026-06-01T12:10:00Z');

      act(() => {
        vi.advanceTimersByTime(10 * 60_000 + 1_500);
      });

      expect(screen.queryByTestId('header-countdown')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  // One listFeed() call returns only the latest 100 events, so a countdown
  // pushed earlier in a long-running rally could sit further back. The shell
  // must page for it.
  it('shows a countdown that sits on the second page of the history load', async () => {
    const page1 = fillerEvents(FEED_PAGE_LIMIT, '2026-06-02T00:00:00Z', 'filler');
    const page2 = [
      makeFeedEvent(
        'c1',
        'countdown',
        { de: 'Endspurt!', en: 'Final sprint!', target_time: '2099-01-01T00:00:00Z' },
        '2026-06-01T00:00:00Z',
      ),
    ];
    mockFeedPages([page1, page2]);

    renderShell();

    expect(await screen.findByTestId('header-countdown')).toHaveTextContent('2099-01-01T00:00:00Z');
    expect(feedCallCount()).toBe(2);
  });

  // A notice created after the participant's last visit but pushed while more
  // than 1000 events already sit on the run's feed must still count as unread,
  // even though it falls outside the first page.
  it('still counts an unseen notice that sits beyond the first 1000 events', async () => {
    localStorage.setItem('clavis_feed_last_seen_run-1', String(Date.parse('2026-06-01T00:00:00Z')));
    const page1 = fillerEvents(FEED_PAGE_LIMIT, '2026-06-03T00:00:00Z', 'filler');
    const page2 = [
      makeFeedEvent(
        'n1',
        'announcement',
        { de: 'Alt, aber ungesehen', en: 'Old but unseen' },
        '2026-06-01T12:00:00Z',
      ),
    ];
    mockFeedPages([page1, page2]);

    renderShell();

    expect(
      await screen.findByRole('button', { name: 'Mitteilungen, 1 neue Meldung' }),
    ).toBeInTheDocument();
    expect(feedCallCount()).toBe(2);
  });

  // A live feed_event with plain-string content opens the notice popup with that
  // text, resolved via feedMessage.
  it('opens the notice popup with a plain-string feed_event', () => {
    renderShell();

    fireFeedEvent(makeFeedEvent('n1', 'announcement', { text: 'Bitte zum Treffpunkt kommen' }));

    expect(screen.getByText('Bitte zum Treffpunkt kommen')).toBeInTheDocument();
  });

  // A language-map content still opens the popup with the interface language's
  // text.
  it('opens the notice popup with the interface language for a language-map feed_event', () => {
    renderShell();

    fireFeedEvent(makeFeedEvent('n1', 'alert', { de: 'Achtung Baustelle', en: 'Watch out: construction' }));

    expect(screen.getByText('Achtung Baustelle')).toBeInTheDocument();
    expect(screen.queryByText('Watch out: construction')).toBeNull();
  });

  // Empty content opens no popup.
  it('opens no notice popup for an empty-content feed_event', () => {
    renderShell();

    fireFeedEvent(makeFeedEvent('n1', 'reminder', {}));

    expect(screen.queryByRole('alert')).toBeNull();
  });

  // An alarm stays open until its close button is pressed. A tap anywhere else,
  // which MUI reports as "clickaway", does not close it.
  it('keeps an alarm on screen when the participant taps elsewhere, and vibrates', () => {
    const vibrate = vi.fn();
    Object.defineProperty(navigator, 'vibrate', { value: vibrate, configurable: true });
    renderShell();

    fireFeedEvent(makeFeedEvent('a9', 'alert', { text: 'Bitte sofort zum Treffpunkt' }));
    expect(vibrate).toHaveBeenCalledTimes(1);

    fireEvent.click(document.body);
    fireEvent.touchEnd(document.body);
    expect(screen.getByText('Bitte sofort zum Treffpunkt')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    return waitFor(() => expect(screen.queryByText('Bitte sofort zum Treffpunkt')).toBeNull());
  });

  it('does not vibrate for a plain notice', () => {
    const vibrate = vi.fn();
    Object.defineProperty(navigator, 'vibrate', { value: vibrate, configurable: true });
    renderShell();

    fireFeedEvent(makeFeedEvent('n9', 'announcement', { text: 'Nur eine Mitteilung' }));

    expect(vibrate).not.toHaveBeenCalled();
  });

  // The server decides who gets a targeted notice and pushes it to the team's
  // members only. The client shows what arrives. A filter on the team id in
  // sessionStorage would drop a team's own alarm in a tab that has no stored
  // team (a bookmark, a new tab) or a stale one.
  it('shows a targeted live notice also when the tab has no stored team', () => {
    renderShell();

    fireFeedEvent({ ...makeFeedEvent('a2', 'alert', { text: 'Alarm für Team A' }), target_team_id: 'team-a' });

    expect(screen.getByText('Alarm für Team A')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mitteilungen, 1 neue Meldung' })).toBeInTheDocument();
  });

  // Once a page both contains the newest countdown and already reaches back past
  // the last-seen boundary, nothing further back could still be new: the load
  // stops after that one page.
  it('stops the history load after the first page once both stop conditions hold', async () => {
    localStorage.setItem('clavis_feed_last_seen_run-1', String(Date.parse('2026-06-01T00:00:00Z')));
    const page1 = [
      // index 0 — this page's oldest event, already before the last-seen
      // boundary, so nothing before this page could still be unseen.
      makeFeedEvent('old', 'milestone', {}, '2026-05-01T00:00:00Z'),
      ...fillerEvents(FEED_PAGE_LIMIT - 2, '2026-06-02T00:00:00Z', 'filler'),
      makeFeedEvent(
        'c1',
        'countdown',
        { de: 'Endspurt!', en: 'Final sprint!', target_time: '2099-01-01T00:00:00Z' },
        '2026-06-02T01:00:00Z',
      ),
    ];
    expect(page1).toHaveLength(FEED_PAGE_LIMIT);
    mockFeedPages([page1]);

    renderShell();

    expect(await screen.findByTestId('header-countdown')).toHaveTextContent('2099-01-01T00:00:00Z');
    expect(feedCallCount()).toBe(1);
  });

  // A team of the run is dissolved while the app is open.
  describe('team_dissolved on the run channel', () => {
    const OVERVIEW = '/participant-overview';

    const overviewCallCount = () =>
      vi.mocked(apiClient.get).mock.calls.filter(([url]) => (url as string).endsWith(OVERVIEW))
        .length;

    /** The team fetch answers, and the overview answers as `overview` says. */
    const mockRun = (overview: () => Promise<unknown>) => {
      vi.mocked(apiClient.get).mockImplementation((url: string) => {
        if (url.endsWith('/teams/team-1')) {
          return Promise.resolve({ data: makeTeam('team-1', 'Die Fugger') });
        }
        if (url.endsWith(OVERVIEW)) return overview();
        return Promise.reject(new Error('offline'));
      });
    };

    const notTeamMember = () =>
      Promise.reject(
        new ApiError(404, 'User is not a member of any team in this run', 'NOT_TEAM_MEMBER'),
      );

    // The shell's own subscription: the last call belongs to the chat probe.
    const shellHandlers = () =>
      [...vi.mocked(useWebSocket).mock.calls]
        .reverse()
        .find(([options]) => !!options.handlers?.team_member_moved)![0].handlers!;

    const fireDissolved = (teamId: string, runId = 'run-1') => {
      const handlers = shellHandlers();
      act(() => {
        handlers.team_dissolved?.(
          { run_id: runId, team_id: teamId },
          { type: 'team_dissolved', data: null, timestamp: '2026-01-01T10:00:00Z' },
        );
      });
    };

    const fireMovedToTeam2 = () => {
      const handlers = shellHandlers();
      act(() => {
        handlers.team_member_moved?.(
          {
            run_id: 'run-1',
            member_id: 'm-1',
            user_id: 'u1',
            display_name: 'Teilnehmer',
            from_team_id: 'team-1',
            to_team_id: 'team-2',
          },
          { type: 'team_member_moved', data: null, timestamp: '2026-01-01T10:00:00Z' },
        );
      });
    };

    const LocationProbe = () => {
      const location = useLocation();
      return <div>{`at:${location.pathname}${location.search}`}</div>;
    };

    /** The chat page of the team tab: it owns the socket of the team's chat. */
    const ChatSocketProbe = () => {
      const { status } = useWebSocket({ path: '/api/orgs/test-org/runs/run-1/chat/team-1/ws' });
      const { team } = useOutletContext<{ team: Team | null }>();
      return <div>{`chat:${status} team:${team?.name ?? 'none'}`}</div>;
    };

    const renderRun = (role: 'participant' | 'guest' | 'coordinator' = 'participant') =>
      render(
        <MemoryRouter initialEntries={['/runs/run-1/participant']}>
          <AuthContext.Provider
            value={{
              user: { ...authUser, role },
              isAuthenticated: true,
              isLoading: false,
              login: vi.fn(),
              logout: vi.fn(),
              checkAuth: vi.fn(),
              needsTwoFactorSetup: () => false,
            }}
          >
            <Routes>
              <Route path="/runs/:runId/participant" element={<RunShell />}>
                <Route index element={<ChatSocketProbe />} />
              </Route>
              <Route path="/no-runs" element={<LocationProbe />} />
            </Routes>
          </AuthContext.Provider>
        </MemoryRouter>,
      );

    const listenForReload = () => {
      const onReload = vi.fn();
      window.addEventListener('clavis:team-dissolved', onReload);
      return {
        onReload,
        stop: () => window.removeEventListener('clavis:team-dissolved', onReload),
      };
    };

    // The standing of the others changes, and their pages load it.
    it('tells the open pages to load again when another team is dissolved', async () => {
      sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
      mockRun(notTeamMember);
      const { onReload, stop } = listenForReload();

      renderRun();
      expect(await screen.findByText('chat:open team:Die Fugger')).toBeInTheDocument();
      fireDissolved('team-9');

      expect(onReload).toHaveBeenCalledTimes(1);
      // The own team still stands: no request about the membership, no exit.
      expect(overviewCallCount()).toBe(0);
      expect(screen.getByText('chat:open team:Die Fugger')).toBeInTheDocument();
      expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-1');
      stop();
    });

    it('ignores a team_dissolved of another run', async () => {
      sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
      mockRun(notTeamMember);
      const { onReload, stop } = listenForReload();

      renderRun();
      expect(await screen.findByText('chat:open team:Die Fugger')).toBeInTheDocument();
      fireDissolved('team-1', 'run-2');

      expect(onReload).not.toHaveBeenCalled();
      expect(overviewCallCount()).toBe(0);
      stop();
    });

    // The socket of the chat is closed first, and the team tab asks for nothing
    // that names the membership. The event on the run channel makes the shell
    // ask, and the refusal leads to the notice.
    it('sends a former member to the notice after the chat socket closed first', async () => {
      sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
      mockRun(notTeamMember);
      vi.mocked(useWebSocket).mockImplementation(({ path }) => ({
        status: path.includes('/chat/') ? 'closed' : 'open',
        lastError: null,
        send: vi.fn(),
      }));

      renderRun('guest');
      expect(await screen.findByText('chat:closed team:Die Fugger')).toBeInTheDocument();
      // A closed chat alone moves nobody.
      expect(overviewCallCount()).toBe(0);

      fireDissolved('team-1');

      expect(await screen.findByText('at:/no-runs?reason=removed')).toBeInTheDocument();
      expect(overviewCallCount()).toBe(1);
      expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBeNull();
    });

    it('sends a former member out when the overview answers without a membership', async () => {
      sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
      mockRun(() =>
        Promise.resolve({
          data: { stations: [], answered_challenge_ids: [], is_team_member: false },
        }),
      );

      renderRun();
      expect(await screen.findByText('chat:open team:Die Fugger')).toBeInTheDocument();
      fireDissolved('team-1');

      expect(await screen.findByText('at:/no-runs?reason=removed')).toBeInTheDocument();
    });

    // The request decides, not the event: a member the server still knows stays.
    it('keeps a member in the run while the server still names the membership', async () => {
      sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
      mockRun(() =>
        Promise.resolve({
          data: { stations: [], answered_challenge_ids: [], is_team_member: true },
        }),
      );

      renderRun();
      expect(await screen.findByText('chat:open team:Die Fugger')).toBeInTheDocument();
      fireDissolved('team-1');

      await waitFor(() => expect(overviewCallCount()).toBe(1));
      expect(screen.getByText('chat:open team:Die Fugger')).toBeInTheDocument();
      expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-1');
    });

    // The last member who was moved follows team_member_moved to the new team;
    // the dissolution of the old one, which arrives after it, is not theirs.
    it('does not send out a member who was moved before the old team dissolved', async () => {
      sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
      vi.mocked(apiClient.get).mockImplementation((url: string) => {
        if (url.endsWith('/teams/team-1')) {
          return Promise.resolve({ data: makeTeam('team-1', 'Die Fugger') });
        }
        if (url.endsWith('/teams/team-2')) {
          return Promise.resolve({ data: makeTeam('team-2', 'Die Welser') });
        }
        if (url.endsWith(OVERVIEW)) return notTeamMember();
        return Promise.reject(new Error('offline'));
      });

      renderRun();
      expect(await screen.findByText('chat:open team:Die Fugger')).toBeInTheDocument();
      fireMovedToTeam2();
      fireDissolved('team-1');

      expect(await screen.findByText('chat:open team:Die Welser')).toBeInTheDocument();
      expect(overviewCallCount()).toBe(0);
      expect(sessionStorage.getItem('clavis_team_for_run_run-1')).toBe('team-2');
    });

    // Staff may look at the run. Only a playing role is sent out.
    it('does not send out a coordinator who looks at the run', async () => {
      sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
      mockRun(notTeamMember);

      renderRun('coordinator');
      expect(await screen.findByText('chat:open team:Die Fugger')).toBeInTheDocument();
      fireDissolved('team-1');

      expect(overviewCallCount()).toBe(0);
      expect(screen.getByText('chat:open team:Die Fugger')).toBeInTheDocument();
    });
  });
});

// The run shell's main takes the focus from the skip link: it carries
// tabindex -1. It draws no outline of its own through the shared
// #main-content rule of index.css, pinned in AppShell's a11y test.
describe('RunShell — main takes the skip-link focus', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    __resetNoticeStoreForTests();
    vi.mocked(apiClient.get).mockRejectedValue(new Error('offline'));
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  it('main carries id main-content and tabindex -1', () => {
    renderShell();

    const main = screen.getByRole('main');
    expect(main).toHaveAttribute('id', 'main-content');
    expect(main).toHaveAttribute('tabindex', '-1');
  });
});

// The team chat fits the phone screen. jsdom lays nothing out, so these tests
// read the declared styles that make the column. The measured geometry is
// tests/e2e/participant-chat-layout.spec.ts.
describe('RunShell — a page that fills the screen', () => {
  const renderShellAt = (path: string) =>
    render(
      <MemoryRouter initialEntries={[path]}>
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
            <Route path="/runs/:runId/participant" element={<RunShell />}>
              <Route path="stations" element={<TeamProbe />} />
              <Route path="team/leaderboard" element={<TeamProbe />} />
              <Route path="team/chat" element={<TeamProbe />} />
            </Route>
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );

  /** The run state answers, so the shell renders its outlet and not the spinner. */
  const mockRunAndTeam = (getTeam: () => Promise<{ data: Team }>) => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/state')) {
        return Promise.resolve({
          data: {
            run_id: 'run-1',
            name: 'Stadtrallye',
            state: 'active',
            scheduled_for: null,
            started_at: null,
            paused_at: null,
            ended_at: null,
          },
        });
      }
      if (url.endsWith('/teams/team-1')) return getTeam();
      return Promise.reject(new Error('offline'));
    });
  };

  const teamCallCount = () =>
    vi.mocked(apiClient.get).mock.calls.filter(([url]) => (url as string).endsWith('/teams/team-1'))
      .length;

  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    __resetNoticeStoreForTests();
    vi.mocked(apiClient.get).mockRejectedValue(new Error('offline'));
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  // On the chat the shell is as high as the screen and a flex column down to
  // the page: the header stands in the flow and takes its own height, main
  // takes the rest and may shrink. No number is subtracted anywhere.
  it('is a screen-high flex column on the chat tab, with the header in the flow', () => {
    renderShellAt('/runs/run-1/participant/team/chat');

    const main = screen.getByRole('main');
    expect(main).toHaveStyle({ display: 'flex', flexDirection: 'column', minHeight: '0', paddingTop: '8px' });
    // The bottom navigation is fixed, so main keeps the room for it.
    expect(main).toHaveStyle({ paddingBottom: '72px' });
    expect(main.parentElement).toHaveStyle({ height: '100dvh', display: 'flex', flexDirection: 'column' });
    expect(screen.getByRole('banner')).toHaveStyle({ position: 'sticky' });
  });

  it('keeps the scrolling document and the fixed header on every other page', () => {
    renderShellAt('/runs/run-1/participant/team/leaderboard');

    const main = screen.getByRole('main');
    expect(main).not.toHaveStyle({ display: 'flex' });
    expect(main).toHaveStyle({ paddingTop: '64px' });
    expect(main.parentElement).toHaveStyle({ minHeight: '100dvh' });
    expect(main.parentElement).not.toHaveStyle({ height: '100dvh' });
    expect(screen.getByRole('banner')).toHaveStyle({ position: 'fixed' });
  });

  // The shell asks for the team at its mount. If that answer is lost, the team
  // tab asks again when it opens without a team.
  it('asks for the team again when the team tab opens without one', async () => {
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    let answer: () => Promise<{ data: Team }> = () => Promise.reject(new Error('offline'));
    mockRunAndTeam(() => answer());

    renderShellAt('/runs/run-1/participant/stations');
    expect(await screen.findByText('kein Team')).toBeInTheDocument();
    await waitFor(() => expect(teamCallCount()).toBe(1));

    // The connection is back by the time the participant taps "Team".
    answer = () => Promise.resolve({ data: makeTeam('team-1', 'Die Fugger') });
    fireEvent.click(screen.getByRole('button', { name: 'Team' }));

    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();
    expect(teamCallCount()).toBe(2);
  });

  it('does not ask again once the team is there', async () => {
    sessionStorage.setItem('clavis_team_for_run_run-1', 'team-1');
    mockRunAndTeam(() => Promise.resolve({ data: makeTeam('team-1', 'Die Fugger') }));

    renderShellAt('/runs/run-1/participant/stations');
    expect(await screen.findByText('Die Fugger')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Team' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Team' })).toHaveClass('Mui-selected'));
    fireEvent.click(screen.getByRole('button', { name: 'Rallye' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Rallye' })).toHaveClass('Mui-selected'));

    expect(screen.getByText('Die Fugger')).toBeInTheDocument();
    expect(teamCallCount()).toBe(1);
  });
});

// The team tab of a run page that was opened without the key GuestJoin and the
// sign-in landing leave in sessionStorage (a bookmark, a second tab, a reload
// after the storage was cleared).
describe('RunShell — the team tab knows its team', () => {
  const HISTORY = '/api/me/run-history';
  const KEY = 'clavis_team_for_run_run-1';

  const historyItem = (runId: string, teamId: string) => ({
    run_id: runId,
    run_name: 'Lauf',
    rallye_title: { de: 'Rallye' },
    team_id: teamId,
    team_name: 'Die Fugger',
    started_at: null,
    ended_at: null,
    state: 'active',
    placement: null,
    total_teams: null,
    total_points: 0,
  });

  const teamWithMembers = (memberCount: number) => ({
    ...makeTeam('team-1', 'Die Fugger'),
    member_count: memberCount,
    members: Array.from({ length: memberCount }, (_, i) => ({
      id: `m-${i + 1}`,
      display_name: `Mitglied ${i + 1}`,
    })),
  });

  /** What the server answers; `history` is what /api/me/run-history lists. */
  const mockServer = (
    history: () => Promise<unknown>,
    options: { memberCount?: number; overview?: () => Promise<unknown> } = {},
  ) => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url === HISTORY) return history();
      if (url.endsWith('/teams/team-1')) {
        return Promise.resolve({ data: teamWithMembers(options.memberCount ?? 2) });
      }
      if (url.endsWith('/teams/team-1/chat/')) {
        return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      }
      if (url.endsWith('/participant-overview') && options.overview) return options.overview();
      return Promise.reject(new Error('offline'));
    });
  };

  const historyOf = (...items: ReturnType<typeof historyItem>[]) => () =>
    Promise.resolve({ data: { items } });

  const visited: string[] = [];
  const PathProbe = () => {
    const location = useLocation();
    visited.push(`${location.pathname}${location.search}`);
    return <div>{`at:${location.pathname}${location.search}`}</div>;
  };

  const renderTeamTab = (
    path: 'info' | 'chat',
    role: 'participant' | 'guest' = 'participant',
    logout = vi.fn(),
  ) =>
    render(
      <MemoryRouter initialEntries={[`/runs/run-1/participant/team/${path}`]}>
        <AuthContext.Provider
          value={{
            user: { ...authUser, role },
            isAuthenticated: true,
            isLoading: false,
            login: vi.fn(),
            logout,
            checkAuth: vi.fn(),
            needsTwoFactorSetup: () => false,
          }}
        >
          <Routes>
            <Route path="/runs/:runId/participant" element={<RunShell />}>
              <Route path="team" element={<TeamTab />}>
                <Route path="info" element={<TeamInfo />} />
                <Route path="chat" element={<TeamChat />} />
              </Route>
            </Route>
            <Route path="/" element={<PathProbe />} />
            <Route path="/dashboard" element={<PathProbe />} />
            <Route path="/no-runs" element={<PathProbe />} />
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );

  const chatCalls = () =>
    vi.mocked(apiClient.get).mock.calls.filter(([url]) =>
      (url as string).endsWith('/teams/team-1/chat/'),
    );

  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    __resetNoticeStoreForTests();
    visited.length = 0;
    vi.mocked(apiClient.get).mockRejectedValue(new Error('offline'));
    vi.mocked(apiClient.post).mockReset();
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  // The server names the team, the shell keeps the answer and loads it.
  it('shows the team on the team tab when the server names a membership', async () => {
    mockServer(historyOf(historyItem('run-0', 'team-0'), historyItem('run-1', 'team-1')));

    renderTeamTab('info');

    expect(await screen.findByRole('heading', { name: 'Die Fugger' })).toBeInTheDocument();
    expect(screen.queryByText(/keine Team-Mitgliedschaft/)).not.toBeInTheDocument();
    expect(sessionStorage.getItem(KEY)).toBe('team-1');
  });

  it('loads the chat of the team the server names', async () => {
    mockServer(historyOf(historyItem('run-1', 'team-1')));

    renderTeamTab('chat');

    await waitFor(() => expect(chatCalls()).toHaveLength(1));
    expect(screen.queryByText(/keinem Team zugeordnet/)).not.toBeInTheDocument();
  });

  it('says nothing about a missing membership while the server has not answered', async () => {
    let answer: (value: unknown) => void = () => {};
    mockServer(() => new Promise((resolve) => { answer = resolve; }));

    renderTeamTab('info');

    expect(await screen.findByRole('status')).toBeInTheDocument();
    expect(screen.queryByText(/keine Team-Mitgliedschaft/)).not.toBeInTheDocument();
    await act(async () => { answer({ data: { items: [] } }); });
    expect(await screen.findByText(/keine Team-Mitgliedschaft/)).toBeInTheDocument();
  });

  it('shows the sentence for a missing membership when the server lists none for this run', async () => {
    mockServer(historyOf(historyItem('run-0', 'team-0')));

    renderTeamTab('info');

    expect(
      await screen.findByText('Für diesen Lauf wurde keine Team-Mitgliedschaft gefunden.'),
    ).toBeInTheDocument();
    expect(sessionStorage.getItem(KEY)).toBeNull();
  });

  it('keeps the chat on its fallback when the server lists no membership', async () => {
    mockServer(historyOf());

    renderTeamTab('chat');

    expect(await screen.findByText(/keinem Team zugeordnet/)).toBeInTheDocument();
    expect(chatCalls()).toHaveLength(0);
  });

  // A failed lookup is not an answer: no endless spinner, no "no membership".
  describe('a failed lookup of the membership', () => {
    const failOnceThenAnswer = () => {
      let calls = 0;
      mockServer(() => {
        calls += 1;
        return calls === 1
          ? Promise.reject(new Error('offline'))
          : Promise.resolve({ data: { items: [historyItem('run-1', 'team-1')] } });
      });
      return () => calls;
    };

    it('shows an error with a retry on the team tab, and the team after the retry', async () => {
      const calls = failOnceThenAnswer();

      renderTeamTab('info');

      expect(
        await screen.findByText('Die Team-Mitgliedschaft konnte nicht geprüft werden.'),
      ).toBeInTheDocument();
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
      expect(screen.queryByText(/keine Team-Mitgliedschaft/)).not.toBeInTheDocument();
      expect(calls()).toBe(1);

      await userEvent.click(screen.getByRole('button', { name: 'Erneut versuchen' }));

      expect(await screen.findByRole('heading', { name: 'Die Fugger' })).toBeInTheDocument();
      expect(calls()).toBe(2);
      expect(sessionStorage.getItem(KEY)).toBe('team-1');
    });

    it('shows the same error with a retry on the chat tab, no endless spinner', async () => {
      const calls = failOnceThenAnswer();

      renderTeamTab('chat');

      expect(
        await screen.findByText('Die Team-Mitgliedschaft konnte nicht geprüft werden.'),
      ).toBeInTheDocument();
      expect(screen.queryByRole('status')).not.toBeInTheDocument();
      expect(screen.queryByText(/keinem Team zugeordnet/)).not.toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: 'Erneut versuchen' }));

      await waitFor(() => expect(chatCalls()).toHaveLength(1));
      expect(calls()).toBe(2);
    });
  });

  it('does not ask the server when the key is there', async () => {
    sessionStorage.setItem(KEY, 'team-1');
    mockServer(historyOf());

    renderTeamTab('info');

    expect(await screen.findByRole('heading', { name: 'Die Fugger' })).toBeInTheDocument();
    expect(
      vi.mocked(apiClient.get).mock.calls.filter(([url]) => url === HISTORY),
    ).toHaveLength(0);
  });

  // A last member leaves. The team_dissolved the leave itself causes reaches
  // the shell of the tab that left.
  describe('the last member who leaves in this tab', () => {
    const OVERVIEW_GONE = () =>
      Promise.resolve({
        data: { stations: [], answered_challenge_ids: [], is_team_member: false },
      });

    const shellHandlers = () =>
      [...vi.mocked(useWebSocket).mock.calls]
        .reverse()
        .find(([options]) => !!options.handlers?.team_member_moved)![0].handlers!;

    const fireDissolved = (handlers = shellHandlers()) =>
      act(async () => {
        handlers.team_dissolved?.(
          { run_id: 'run-1', team_id: 'team-1' },
          { type: 'team_dissolved', data: null, timestamp: '2026-01-01T10:00:00Z' },
        );
        // let the shell's own request about the membership settle
        await new Promise((resolve) => setTimeout(resolve, 20));
      });

    const startLeave = async () => {
      const user = userEvent.setup();
      await user.click(await screen.findByRole('button', { name: 'Team verlassen' }));
      const dialog = await screen.findByRole('dialog');
      await user.click(within(dialog).getByRole('button', { name: 'Verlassen' }));
    };

    it('ends on the page the leave sends a guest to when the event comes before the answer', async () => {
      sessionStorage.setItem(KEY, 'team-1');
      mockServer(historyOf(), { memberCount: 1, overview: OVERVIEW_GONE });
      let answerLeave: () => void = () => {};
      vi.mocked(apiClient.post).mockImplementation(
        () => new Promise((resolve) => { answerLeave = () => resolve({ data: null }); }),
      );
      const logout = vi.fn().mockResolvedValue(undefined);

      renderTeamTab('info', 'guest', logout);
      await startLeave();
      await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(1));
      await fireDissolved();
      act(() => answerLeave());

      expect(await screen.findByText('at:/')).toBeInTheDocument();
      expect(visited).not.toContain('/no-runs?reason=removed');
      expect(logout).toHaveBeenCalled();
    });

    it('ends on the page the leave sends a guest to when the event comes after the answer', async () => {
      sessionStorage.setItem(KEY, 'team-1');
      mockServer(historyOf(), { memberCount: 1, overview: OVERVIEW_GONE });
      vi.mocked(apiClient.post).mockResolvedValue({ data: null });
      const logout = vi.fn().mockResolvedValue(undefined);

      renderTeamTab('info', 'guest', logout);
      const handlers = (await screen.findByRole('button', { name: 'Team verlassen' })) &&
        shellHandlers();
      await startLeave();
      expect(await screen.findByText('at:/')).toBeInTheDocument();
      await fireDissolved(handlers);

      expect(visited).not.toContain('/no-runs?reason=removed');
      expect(screen.getByText('at:/')).toBeInTheDocument();
    });

    it('ends on the dashboard for a participant when the event comes before the answer', async () => {
      sessionStorage.setItem(KEY, 'team-1');
      mockServer(historyOf(), { memberCount: 1, overview: OVERVIEW_GONE });
      let answerLeave: () => void = () => {};
      vi.mocked(apiClient.post).mockImplementation(
        () => new Promise((resolve) => { answerLeave = () => resolve({ data: null }); }),
      );

      renderTeamTab('info');
      await startLeave();
      await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(1));
      await fireDissolved();
      act(() => answerLeave());

      expect(await screen.findByText('at:/dashboard')).toBeInTheDocument();
      expect(visited).not.toContain('/no-runs?reason=removed');
    });

    // Nobody in this tab asked for it.
    it('still sends a member whose team is dissolved without a leave in this tab to the removed page', async () => {
      sessionStorage.setItem(KEY, 'team-1');
      mockServer(historyOf(), { overview: OVERVIEW_GONE });

      renderTeamTab('info');
      expect(await screen.findByRole('heading', { name: 'Die Fugger' })).toBeInTheDocument();
      await fireDissolved();

      expect(await screen.findByText('at:/no-runs?reason=removed')).toBeInTheDocument();
    });

    it('sends a member to the removed page again after a leave that failed', async () => {
      sessionStorage.setItem(KEY, 'team-1');
      mockServer(historyOf(), { memberCount: 2, overview: OVERVIEW_GONE });
      vi.mocked(apiClient.post).mockRejectedValue(
        new ApiError(500, 'Something went wrong', 'INTERNAL'),
      );

      renderTeamTab('info');
      await startLeave();
      expect(await screen.findByText('Something went wrong')).toBeInTheDocument();
      await fireDissolved();

      expect(await screen.findByText('at:/no-runs?reason=removed')).toBeInTheDocument();
    });
  });
});

// A paused run says so on every participant page. The notice stands in the
// frame, comes from the run state the frame already has, and is announced
// once, when the pause starts.
describe('RunShell — a paused run says so', () => {
  const NOTICE = 'Der Lauf ist pausiert. Es geht weiter, sobald die Spielleitung ihn fortsetzt.';
  const PAUSE_TOAST = 'Der Lauf wurde pausiert.';
  const RESUME_TOAST = 'Der Lauf wurde fortgesetzt.';

  const mockRunState = (state: () => string) => {
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.endsWith('/state')) {
        return Promise.resolve({
          data: {
            run_id: 'run-1',
            name: 'Stadtrallye',
            state: state(),
            scheduled_for: null,
            started_at: null,
            paused_at: null,
            ended_at: null,
          },
        });
      }
      return Promise.reject(new Error('offline'));
    });
  };

  const renderAt = (path: string) =>
    render(
      <MemoryRouter initialEntries={[path]}>
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
            <Route path="/runs/:runId/participant" element={<RunShell />}>
              <Route path="stations" element={<div>Stationen-Seite</div>} />
              <Route path="feed" element={<div>Feed-Seite</div>} />
              <Route path="team/chat" element={<div>Chat-Seite</div>} />
            </Route>
          </Routes>
        </AuthContext.Provider>
      </MemoryRouter>,
    );

  const fireStateChange = (previous: string, next: string) => {
    const handlers = vi
      .mocked(useWebSocket)
      .mock.calls.map(([options]) => options.handlers)
      .filter((h) => h?.run_state_change)
      .at(-1)!;
    act(() => {
      handlers.run_state_change!(
        { run_id: 'run-1', organization_id: 'org-1', previous_state: previous, new_state: next, changed_at: null },
        { type: 'run_state_change', data: {}, timestamp: '2026-06-01T12:00:00Z' },
      );
    });
  };

  /** Every element a screen reader treats as live and that holds the text. */
  const liveElementsWith = (text: string): Element[] =>
    Array.from(
      document.querySelectorAll(
        '[role="alert"], [role="status"], [role="log"], [aria-live="polite"], [aria-live="assertive"]',
      ),
    ).filter((el) => el.textContent?.includes(text));

  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    localStorage.clear();
    __resetNoticeStoreForTests();
    vi.mocked(apiClient.get).mockRejectedValue(new Error('offline'));
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
  });

  // A page that opens during a pause knows it from the fetched state, with no
  // live message, and the notice is text only: nothing announces it at mount.
  it('shows the notice at once on a run that is paused when the frame mounts, on two routes', async () => {
    mockRunState(() => 'paused');

    const first = renderAt('/runs/run-1/participant/stations');
    expect(await screen.findByText(NOTICE)).toBeInTheDocument();
    expect(screen.getByText('Stationen-Seite')).toBeInTheDocument();
    expect(liveElementsWith(NOTICE)).toHaveLength(0);
    first.unmount();

    renderAt('/runs/run-1/participant/feed');
    expect(await screen.findByText(NOTICE)).toBeInTheDocument();
    expect(screen.getByText('Feed-Seite')).toBeInTheDocument();
    expect(liveElementsWith(NOTICE)).toHaveLength(0);
  });

  it('stands between the header and the page, inside the main landmark', async () => {
    mockRunState(() => 'paused');
    renderAt('/runs/run-1/participant/team/chat');

    const notice = await screen.findByText(NOTICE);
    const page = screen.getByText('Chat-Seite');
    expect(screen.getByRole('main')).toContainElement(notice);
    expect(notice.compareDocumentPosition(page) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole('banner').compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows no notice on a run that is active', async () => {
    mockRunState(() => 'active');
    renderAt('/runs/run-1/participant/stations');

    expect(await screen.findByText('Stationen-Seite')).toBeInTheDocument();
    expect(screen.queryByText(NOTICE)).toBeNull();
  });

  // The pause arrives over the WebSocket: the notice shows, one live element
  // holds its text, no toast does, and a page change says nothing again.
  it('announces a pause from the WebSocket once, with no toast, and not again on a page change', async () => {
    let runState = 'active';
    mockRunState(() => runState);
    renderAt('/runs/run-1/participant/stations');
    expect(await screen.findByText('Stationen-Seite')).toBeInTheDocument();
    expect(screen.queryByText(NOTICE)).toBeNull();

    runState = 'paused';
    fireStateChange('active', 'paused');

    await waitFor(() => expect(screen.getAllByText(NOTICE).length).toBeGreaterThan(0));
    expect(screen.queryByText(PAUSE_TOAST)).toBeNull();
    expect(liveElementsWith(NOTICE)).toHaveLength(1);
    expect(liveElementsWith(PAUSE_TOAST)).toHaveLength(0);
    const announcer = liveElementsWith(NOTICE)[0];

    fireEvent.click(screen.getByRole('button', { name: 'Mitteilungen' }));
    await screen.findByText('Feed-Seite');
    expect(screen.getAllByText(NOTICE).length).toBeGreaterThan(0);
    // The same single element still holds it, untouched by the page change.
    expect(liveElementsWith(NOTICE)).toEqual([announcer]);
    expect(screen.queryByText(PAUSE_TOAST)).toBeNull();
  });

  // The resume removes the notice without a reload. The resume toast shows.
  it('removes the notice at the resume and shows the resume toast', async () => {
    let runState = 'active';
    mockRunState(() => runState);
    renderAt('/runs/run-1/participant/stations');
    expect(await screen.findByText('Stationen-Seite')).toBeInTheDocument();

    runState = 'paused';
    fireStateChange('active', 'paused');
    await waitFor(() => expect(screen.getAllByText(NOTICE).length).toBeGreaterThan(0));

    runState = 'active';
    fireStateChange('paused', 'active');

    await waitFor(() => expect(screen.queryByText(NOTICE)).toBeNull());
    expect(await screen.findByText(RESUME_TOAST)).toBeInTheDocument();
    expect(liveElementsWith(NOTICE)).toHaveLength(0);
  });

  // The run socket reports its own status. The shell opens that socket twice
  // (the run state and the notices), so one shared status feeds every caller
  // on the run path. Every other socket stays open. The mock is a real hook,
  // so a status change re-renders the shell the way a reconnect does.
  const mockRunSocket = () => {
    let runSocketStatus: 'open' | 'closed' = 'open';
    const rerenders = new Set<() => void>();
    vi.mocked(useWebSocket).mockImplementation(({ path }) => {
      const [, bump] = useState(0);
      const onRunPath = path.endsWith('/runs/run-1/ws');
      if (onRunPath) rerenders.add(() => bump((n) => n + 1));
      return { status: onRunPath ? runSocketStatus : 'open', lastError: null, send: vi.fn() };
    });
    return (status: 'open' | 'closed') =>
      act(() => {
        runSocketStatus = status;
        rerenders.forEach((rerender) => rerender());
      });
  };

  // The resume came while the socket was down. The reconnect brings it.
  it('removes the notice after a reconnect that finds the run resumed, with no remount', async () => {
    let runState = 'paused';
    mockRunState(() => runState);
    const setSocket = mockRunSocket();
    renderAt('/runs/run-1/participant/stations');
    expect(await screen.findByText(NOTICE)).toBeInTheDocument();

    setSocket('closed');
    runState = 'active';
    setSocket('open');

    await waitFor(() => expect(screen.queryByText(NOTICE)).toBeNull());
    expect(await screen.findByText(RESUME_TOAST)).toBeInTheDocument();
    expect(screen.getByText('Stationen-Seite')).toBeInTheDocument();
  });

  it('shows the notice after a reconnect that finds the run paused, announced once', async () => {
    let runState = 'active';
    mockRunState(() => runState);
    const setSocket = mockRunSocket();
    renderAt('/runs/run-1/participant/stations');
    expect(await screen.findByText('Stationen-Seite')).toBeInTheDocument();
    expect(screen.queryByText(NOTICE)).toBeNull();

    setSocket('closed');
    runState = 'paused';
    setSocket('open');

    await waitFor(() => expect(screen.getAllByText(NOTICE).length).toBeGreaterThan(0));
    expect(liveElementsWith(NOTICE)).toHaveLength(1);
    expect(screen.queryByText(PAUSE_TOAST)).toBeNull();
  });
});

