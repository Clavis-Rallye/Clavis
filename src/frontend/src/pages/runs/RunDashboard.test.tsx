import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import RunDashboard from './RunDashboard';
import { AuthContext } from '../../auth/AuthContext';
import apiClient, { ApiError } from '../../api/client';
import { useRunState } from '../../hooks/useRunState';
import { useWebSocket } from '../../hooks/useWebSocket';
import type { RunState } from '../../api/types';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

vi.mock('../../hooks/useRunState', () => ({
  useRunState: vi.fn(),
}));

vi.mock('../../hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

// Records the value of every QR code the page renders and still renders the
// real one: the card and the large view must encode the same link.
const qr = vi.hoisted(() => ({ values: [] as string[] }));
vi.mock('qrcode.react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('qrcode.react')>();
  const { createElement } = await import('react');
  return {
    ...actual,
    QRCodeSVG: (props: Parameters<typeof actual.QRCodeSVG>[0]) => {
      qr.values.push(String(props.value));
      return createElement(actual.QRCodeSVG, props);
    },
  };
});

const authUser = {
  id: 'u1',
  email: 'coord@example.com',
  display_name: 'Coordinator',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const runFixture = (state: RunState = 'configured') => ({
  id: 'run-1',
  organization_id: 'org-1',
  rallye_id: 'r1',
  name: 'Herbst-Rallye',
  state,
  join_code: 'JOIN42',
  scheduled_for: null,
  started_at: null,
  paused_at: null,
  ended_at: null,
  settings: {},
  max_teams: null,
  content_languages: ['de', 'en'],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

const teamFixture = {
  id: 'team-1',
  organization_id: 'org-1',
  run_id: 'run-1',
  name: 'Team Alpha',
  color: null,
  member_count: 2,
  share_code: null,
  // Window open so the chat action is enabled in these tests.
  // The gating itself is covered by its own test below.
  coordinator_chat_open: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const mockStart = vi.fn();
const mockPause = vi.fn();
const mockResume = vi.fn();
const mockEnd = vi.fn();
const mockSchedule = vi.fn();

const makeRunState = (state: RunState, extra: Record<string, unknown> = {}) => ({
  run: runFixture(state),
  state,
  wsStatus: 'open' as const,
  loading: false,
  error: null,
  refresh: vi.fn(),
  start: mockStart,
  pause: mockPause,
  resume: mockResume,
  end: mockEnd,
  schedule: mockSchedule,
  ...extra,
});

const renderDashboard = () =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1']}>
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
          <Route path="/runs/:runId" element={<RunDashboard />} />
          <Route path="/runs/:runId/submissions" element={<div>Submissions</div>} />
          <Route path="/runs/:runId/scoreboard" element={<div>Scoreboard</div>} />
          <Route path="/runs/:runId/teams/:teamId/chat" element={<div>TeamChat-Stub</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

// Dispatch a `join_blocked` WS message the way the
// backend sends it on run_state:{run_id}: reason RUN_NOT_STARTED or
// RUN_PAUSED, no personal data in the payload.
const dispatchJoinBlocked = (reason: 'RUN_NOT_STARTED' | 'RUN_PAUSED') => {
  const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
  const data = { run_id: 'run-1', reason };
  handlers.join_blocked(data, { type: 'join_blocked', data, timestamp: '2026-01-01T10:00:00Z' });
};

describe('RunDashboard', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useWebSocket).mockReturnValue({ status: 'open', lastError: null, send: vi.fn() });
    // Default API responses, shaped per endpoint (team-progress and the
    // feedback summary are plain objects, not paginated lists).
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) {
        return Promise.resolve({ data: { teams: [] } });
      }
      if (url.includes('/feedback/summary')) {
        return Promise.resolve({
          data: { average_rating: 0, total_count: 0, distribution: {} },
        });
      }
      if (url.includes('/scores/scoreboard')) {
        return Promise.resolve({
          data: { run_id: 'run-1', entries: [], is_final: false, scored_count: 0, submission_count: 0 },
        });
      }
      if (url.includes('/results/link')) {
        return Promise.resolve({
          data: { status: 'unpublished', url: null, published_at: null, expires_at: null },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });
  });

  it('renders run name, state chip, and join code after load', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
    renderDashboard();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument();
    });
    expect(screen.getAllByText('Konfiguriert').length).toBeGreaterThan(0);
    expect(screen.getByText('JOIN42')).toBeInTheDocument();
  });

  it('for configured state: Starten + Planen visible, Pausieren + Beenden absent', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );

    expect(screen.getByRole('button', { name: 'Starten' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Planen' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pausieren' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Beenden' })).not.toBeInTheDocument();
  });

  it('for active state: Pausieren + Beenden visible, Starten absent', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );

    expect(screen.getByRole('button', { name: 'Pausieren' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Beenden' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Starten' })).not.toBeInTheDocument();
  });

  it('clicking Beenden opens ConfirmDialog; Cancel does not fire end()', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    renderDashboard();

    await waitFor(() => screen.getByRole('button', { name: 'Beenden' }));
    await userEvent.click(screen.getByRole('button', { name: 'Beenden' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeInTheDocument();

    await userEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));
    expect(mockEnd).not.toHaveBeenCalled();
  });

  it('Confirm in ConfirmDialog fires end()', async () => {
    mockEnd.mockResolvedValueOnce(undefined);
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    renderDashboard();

    await waitFor(() => screen.getByRole('button', { name: 'Beenden' }));
    await userEvent.click(screen.getByRole('button', { name: 'Beenden' }));

    const dialog = await screen.findByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Beenden' }));

    await waitFor(() => expect(mockEnd).toHaveBeenCalledTimes(1));
  });

  it('feedback summary section only rendered when state is ended', async () => {
    // First render with configured — feedback not shown
    vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
    const { rerender } = renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );
    expect(screen.queryByText('Feedback')).not.toBeInTheDocument();
    // The results card is an ended-run fixture, too.
    expect(screen.queryByRole('heading', { name: 'Ergebnisse' })).not.toBeInTheDocument();

    // Re-render with ended state. A full URL-keyed implementation (not
    // mockResolvedValueOnce): the results card's own GET fires from a child
    // effect and would otherwise consume the queued one-shot responses.
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/feedback/summary')) {
        return Promise.resolve({
          data: { average_rating: 4.2, total_count: 5, distribution: { 4: 3, 5: 2 } },
        });
      }
      if (url.includes('/scores/scoreboard')) {
        return Promise.resolve({
          data: { run_id: 'run-1', entries: [], is_final: false, scored_count: 0, submission_count: 0 },
        });
      }
      if (url.includes('/results/link')) {
        return Promise.resolve({
          data: { status: 'unpublished', url: null, published_at: null, expires_at: null },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

    vi.mocked(useRunState).mockReturnValue(makeRunState('ended'));

    act(() => {
      rerender(
        <MemoryRouter initialEntries={['/runs/run-1']}>
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
              <Route path="/runs/:runId" element={<RunDashboard />} />
            </Routes>
          </AuthContext.Provider>
        </MemoryRouter>,
      );
    });

    await waitFor(() => {
      expect(screen.getByText('Feedback')).toBeInTheDocument();
    });
    expect(screen.getByText(/Bewertung: 4\.2 \/ 5/)).toBeInTheDocument();
    expect(screen.queryByText('Noch kein Feedback eingegangen.')).toBeNull();

    // The results card shows the grading status line and a
    // publish action that is disabled while grading is open. Scoped to the
    // card: the submissions tile above carries the same "X von Y bewertet".
    const resultsCard = screen
      .getByRole('heading', { name: 'Ergebnisse' })
      .closest('.MuiCard-root');
    expect(resultsCard).not.toBeNull();
    expect(within(resultsCard as HTMLElement).getByText('0 von 0 bewertet')).toBeInTheDocument();
    expect(within(resultsCard as HTMLElement).getByText('Bewertung läuft noch')).toBeInTheDocument();
    expect(
      within(resultsCard as HTMLElement).getByRole('button', { name: 'Ergebnis veröffentlichen' }),
    ).toBeDisabled();
  });

  it('stat row reads the submissions as scored out of total, with a separate Öffnen button', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) {
        return Promise.resolve({
          data: {
            teams: [
              {
                team_id: 'team-1',
                member_count: 2,
                stations_completed: 1,
                stations_total: 3,
                challenges_submitted: 7,
                challenges_scored: 4,
                challenges_total: 9,
                total_points: 12,
                requested_help: false,
              },
            ],
          },
        });
      }
      if (url.includes('/teams/')) {
        return Promise.resolve({
          data: { items: [teamFixture], total: 1, skip: 0, limit: 100 },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

    renderDashboard();

    await waitFor(() => expect(screen.getByText('4 von 7 bewertet')).toBeInTheDocument());
    // What waits for scoring is the blue chip with the
    // hourglass, and it says "unbewertet" instead of "neu".
    const unscoredChip = screen.getByText('3 unbewertet').closest('.MuiChip-root');
    expect(unscoredChip?.querySelector('[data-testid="HourglassEmptyIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(screen.queryByText('3 neu')).not.toBeInTheDocument();
    // "Einreichungen" labels the stat, not the value.
    expect(screen.getAllByText('Einreichungen').length).toBeGreaterThan(0);

    const openSubmissions = screen.getByRole('button', { name: 'Einreichungen öffnen' });
    expect(openSubmissions).toHaveTextContent('Öffnen');
    await userEvent.click(openSubmissions);
    expect(screen.getByText('Submissions')).toBeInTheDocument();
  });

  it('teams card without teams points at the join code', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );

    const empty = await screen.findByText(/Noch keine Teams\./);
    expect(empty).toHaveTextContent('JOIN42');
  });

  it('team row chat icon navigates to the coordinator team chat', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) {
        return Promise.resolve({ data: { teams: [] } });
      }
      if (url.includes('/teams/')) {
        return Promise.resolve({
          data: { items: [teamFixture], total: 1, skip: 0, limit: 100 },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

    renderDashboard();

    await waitFor(() => expect(screen.getByText('Team Alpha')).toBeInTheDocument());

    await userEvent.click(
      screen.getByRole('button', { name: 'Chat öffnen, Team Team Alpha' }),
    );
    expect(screen.getByText('TeamChat-Stub')).toBeInTheDocument();
  });

  it('chat icon is disabled while the assist window is closed', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    const closedTeam = { ...teamFixture, coordinator_chat_open: false };
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) {
        return Promise.resolve({ data: { teams: [] } });
      }
      if (url.includes('/teams/')) {
        return Promise.resolve({
          data: { items: [closedTeam], total: 1, skip: 0, limit: 100 },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

    renderDashboard();

    await waitFor(() => expect(screen.getByText('Team Alpha')).toBeInTheDocument());

    // WCAG 2.5.3: the name starts with the visible "Chat öffnen". Why the
    // button is locked is its description, not its name.
    const lockedButton = screen.getByRole('button', { name: 'Chat öffnen, Team Team Alpha' });
    expect(lockedButton).toBeDisabled();
    expect(lockedButton).toHaveTextContent('Chat öffnen');
    expect(lockedButton).toHaveAccessibleDescription(
      'Gesperrt. Der Chat öffnet sich, wenn das Team Hilfe anfordert.',
    );
  });

  it('names the chat button of a team without a name without a gap', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    const unnamed = { ...teamFixture, name: '', coordinator_chat_open: false };
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) {
        return Promise.resolve({ data: { teams: [] } });
      }
      if (url.includes('/teams/')) {
        return Promise.resolve({
          data: { items: [unnamed], total: 1, skip: 0, limit: 100 },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

    renderDashboard();

    const button = await screen.findByRole('button', { name: 'Chat öffnen, Team ohne Namen' });
    expect(button).toHaveAccessibleDescription(
      'Gesperrt. Der Chat öffnet sich, wenn das Team Hilfe anfordert.',
    );
  });

  it('live help request shows a clickable Hilfe chip that opens the team chat', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) {
        return Promise.resolve({ data: { teams: [] } });
      }
      if (url.includes('/teams/')) {
        return Promise.resolve({
          data: { items: [teamFixture], total: 1, skip: 0, limit: 100 },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

    renderDashboard();
    await waitFor(() => expect(screen.getByText('Team Alpha')).toBeInTheDocument());

    // No Hilfe chip before the help request arrives
    expect(
      screen.queryByRole('button', { name: 'Team Alpha bittet um Unterstützung' }),
    ).not.toBeInTheDocument();

    // Dispatch the SYSTEM feed event over the dashboard's WS subscription
    const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
    const helpEvent = {
      id: 'fe-1',
      event_type: 'system',
      content: {
        message_key: 'help_request',
        team_id: 'team-1',
        team_name: 'Team Alpha',
        de: 'Team Team Alpha benötigt Hilfe.',
        en: 'Team Team Alpha needs help.',
      },
      created_at: '2026-01-01T10:00:00Z',
    };
    act(() => {
      handlers.feed_event(helpEvent, {
        type: 'feed_event',
        data: helpEvent,
        timestamp: '2026-01-01T10:00:00Z',
      });
    });

    // Toast + clickable chip appear
    const chip = await screen.findByRole('button', { name: 'Team Alpha bittet um Unterstützung' });
    await userEvent.click(chip);
    expect(screen.getByText('TeamChat-Stub')).toBeInTheDocument();
  });

  // The dashboard highlights a team whose help request
  // was already pending when the page loaded, with no live WS event required.
  it('highlights a team from loaded team progress alone, without a live WS event', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) {
        return Promise.resolve({
          data: {
            teams: [
              {
                team_id: 'team-1',
                member_count: 2,
                stations_completed: 0,
                stations_total: 3,
                challenges_submitted: 0,
                challenges_scored: 0,
                challenges_total: 9,
                total_points: 0,
                requested_help: true,
              },
            ],
          },
        });
      }
      if (url.includes('/teams/')) {
        return Promise.resolve({
          data: { items: [teamFixture], total: 1, skip: 0, limit: 100 },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

    renderDashboard();

    // No WS event is ever dispatched — the chip must come from team progress.
    const chip = await screen.findByRole('button', { name: 'Team Alpha bittet um Unterstützung' });
    await userEvent.click(chip);
    expect(screen.getByText('TeamChat-Stub')).toBeInTheDocument();
  });

  it('help request for a team that joined after load refreshes the team list and highlights it', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    // First teams fetch returns empty (team joins later). Subsequent fetches
    // return the team — the WS help event must trigger the refresh.
    let teamsCalls = 0;
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) {
        return Promise.resolve({ data: { teams: [] } });
      }
      if (url.includes('/teams/')) {
        teamsCalls += 1;
        return Promise.resolve({
          data: {
            items: teamsCalls === 1 ? [] : [teamFixture],
            total: teamsCalls === 1 ? 0 : 1,
            skip: 0,
            limit: 100,
          },
        });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

    renderDashboard();
    await waitFor(() => expect(teamsCalls).toBeGreaterThan(0));
    expect(screen.queryByText('Team Alpha')).not.toBeInTheDocument();

    const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
    const helpEvent = {
      id: 'fe-2',
      event_type: 'system',
      content: {
        message_key: 'help_request',
        team_id: 'team-1',
        team_name: 'Team Alpha',
        de: 'Team Team Alpha benötigt Hilfe.',
        en: 'Team Team Alpha needs help.',
      },
      created_at: '2026-01-01T10:00:00Z',
    };
    act(() => {
      handlers.feed_event(helpEvent, {
        type: 'feed_event',
        data: helpEvent,
        timestamp: '2026-01-01T10:00:00Z',
      });
    });

    // Team list refreshed → row + Hilfe chip render
    await waitFor(() => expect(screen.getByText('Team Alpha')).toBeInTheDocument());
    expect(
      screen.getByRole('button', { name: 'Team Alpha bittet um Unterstützung' }),
    ).toBeInTheDocument();
  });

  // Joins refused before the start are counted live
  // next to the start action, from zero while the page is open.
  it('counts live join_blocked events next to the start action', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );
    // Nothing refused yet — no counter.
    expect(screen.queryByText(/Beitrittsversuch/)).not.toBeInTheDocument();

    act(() => dispatchJoinBlocked('RUN_NOT_STARTED'));
    expect(screen.getByText('1 Beitrittsversuch vor dem Start')).toBeInTheDocument();

    // Both refusal reasons count — the counter does not discriminate.
    act(() => {
      dispatchJoinBlocked('RUN_PAUSED');
      dispatchJoinBlocked('RUN_NOT_STARTED');
    });
    expect(screen.getByText('3 Beitrittsversuche vor dem Start')).toBeInTheDocument();
  });

  // A paused run refuses joins too: the counter
  // names the pause instead of the start.
  it('counts join_blocked events while the run is paused', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('paused'));
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );

    act(() => dispatchJoinBlocked('RUN_PAUSED'));
    expect(screen.getByText('1 Beitrittsversuch während der Pause')).toBeInTheDocument();
  });

  // The counter resets when the run starts:
  // a later pause must not resurrect the pre-start attempts.
  it('resets the refused-join counter when the run starts', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
    const { rerender } = renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );
    act(() => {
      dispatchJoinBlocked('RUN_NOT_STARTED');
      dispatchJoinBlocked('RUN_NOT_STARTED');
      dispatchJoinBlocked('RUN_NOT_STARTED');
    });
    expect(screen.getByText('3 Beitrittsversuche vor dem Start')).toBeInTheDocument();

    // useRunState applies the run_state_change after Starten.
    const rerenderWithState = (state: RunState) => {
      vi.mocked(useRunState).mockReturnValue(makeRunState(state));
      act(() => {
        rerender(
          <MemoryRouter initialEntries={['/runs/run-1']}>
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
                <Route path="/runs/:runId" element={<RunDashboard />} />
              </Routes>
            </AuthContext.Provider>
          </MemoryRouter>,
        );
      });
    };

    // The run starts — the counter resets.
    rerenderWithState('active');
    expect(screen.queryByText(/Beitrittsversuch/)).not.toBeInTheDocument();

    // Not merely hidden with the start action: a later pause would show a
    // stale "während der Pause" count if the reset had not cleared it.
    rerenderWithState('paused');
    expect(screen.queryByText(/Beitrittsversuch/)).not.toBeInTheDocument();
  });

  describe('copy of the join link', () => {
    const stubClipboard = (writeText: () => Promise<void>) => {
      Object.defineProperty(navigator, 'clipboard', {
        value: { writeText },
        configurable: true,
      });
    };

    it('confirms the copy of the join link', async () => {
      const writeText = vi.fn().mockResolvedValue(undefined);
      stubClipboard(writeText);
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      renderDashboard();

      await userEvent.click(await screen.findByRole('button', { name: 'Beitritts-Link kopieren' }));

      expect(writeText).toHaveBeenCalledWith(expect.stringMatching(/\/join\//));
      expect(await screen.findByText('Beitritts-Link in die Zwischenablage kopiert')).toBeInTheDocument();
    });

    it('says so when the browser refuses the clipboard', async () => {
      stubClipboard(vi.fn().mockRejectedValue(new Error('denied')));
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      renderDashboard();

      await userEvent.click(await screen.findByRole('button', { name: 'Beitritts-Link kopieren' }));

      expect(await screen.findByText('Link kopieren fehlgeschlagen')).toBeInTheDocument();
    });
  });

  // The join card folds at minute 15 while the page stays open, and its toggle
  // says whether the card is open.
  it('folds the join card at minute 15 while the page stays open, and says so', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
    try {
      vi.setSystemTime(new Date('2026-01-01T10:14:00Z'));
      const runState = makeRunState('active');
      runState.run = { ...runState.run, started_at: '2026-01-01T10:00:00Z' } as never;
      vi.mocked(useRunState).mockReturnValue(runState);
      renderDashboard();

      const toggle = await screen.findByRole('button', { name: 'Beitrittscode', expanded: true });
      expect(toggle).toHaveAttribute('aria-controls', 'run-join-card-details');

      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(toggle).toHaveAttribute('aria-expanded', 'false');
    } finally {
      vi.useRealTimers();
    }
  });

  // The planned start says whether it
  // happens on its own and why it did not, and the run is editable before
  // its start.
  it('shows the planned start, a blocked automatic start, and edits the run', async () => {
    const runState = makeRunState('scheduled');
    runState.run = {
      ...runState.run,
      scheduled_for: '2026-01-01T09:00:00Z',
      max_teams: 6,
      settings: { auto_start_blocked: { code: 'RALLYE_HAS_NO_STATIONS', at: '2026-01-01T09:00:30Z' } },
    } as never;
    vi.mocked(useRunState).mockReturnValue(runState);
    vi.mocked(apiClient.put).mockResolvedValueOnce({ data: runState.run });
    renderDashboard();

    expect(await screen.findByTestId('planned-start')).toHaveTextContent(/^Startet automatisch: /);
    expect(screen.getByText(/Die Rallye hat keine Stationen/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Lauf bearbeiten' }));
    const dialog = await screen.findByRole('dialog', { name: 'Lauf bearbeiten' });
    const name = within(dialog).getByLabelText(/^Name/);
    await userEvent.clear(name);
    await userEvent.type(name, 'Neuer Name');
    await userEvent.clear(within(dialog).getByLabelText(/Maximale Teamanzahl/));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Speichern' }));

    await waitFor(() =>
      expect(apiClient.put).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/run-1',
        expect.objectContaining({ name: 'Neuer Name', max_teams: null }),
      ),
    );
    await waitFor(() => expect(runState.refresh).toHaveBeenCalled());
  });

  // The run page of the coordinator.
  describe('rehearsal theme 4', () => {
    const feedItem = (id: string, event_type: string, content: Record<string, unknown>) => ({
      id,
      organization_id: 'org-1',
      run_id: 'run-1',
      event_type,
      content,
      author_id: null,
      target_team_id: null,
      created_at: '2026-01-01T10:05:00Z',
    });

    // "Seit Start —" for up to a minute after "Starten".
    it('shows a duration from the first second after the start', async () => {
      const state = makeRunState('active');
      state.run = { ...state.run, started_at: new Date(Date.now() + 20_000).toISOString() } as never;
      vi.mocked(useRunState).mockReturnValue(state);
      renderDashboard();

      await waitFor(() => expect(screen.getByText(/Seit Start/)).toBeInTheDocument());
      expect(screen.getByText(/Seit Start/).textContent).toMatch(/0 .* 0 /);
      expect(screen.getByText(/Seit Start/).textContent).not.toContain('—');
    });

    // System lines are not notices of the coordinator.
    it('lists past notices without system lines', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      vi.mocked(apiClient.get).mockImplementation((url: string) => {
        if (url.includes('/team-progress')) return Promise.resolve({ data: { teams: [] } });
        if (url.includes('/feed/')) {
          return Promise.resolve({
            data: {
              items: [
                feedItem('f1', 'announcement', { text: 'Treffpunkt am Rathaus' }),
                feedItem('f2', 'system', { de: 'Quiz automatisch bewertet: 10/10 Punkte' }),
              ],
              total: 2,
              skip: 0,
              limit: 100,
            },
          });
        }
        return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      });
      renderDashboard();

      expect(await screen.findByText('Treffpunkt am Rathaus')).toBeInTheDocument();
      expect(screen.queryByText(/Quiz automatisch bewertet/)).toBeNull();
    });

    // After the end the join card is gone: nobody joins an ended run.
    it('shows no join card after the end', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('ended'));
      renderDashboard();

      await screen.findByRole('heading', { name: 'Ergebnisse' });
      expect(screen.queryByRole('heading', { name: 'Beitrittscode' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Beitrittscode groß anzeigen' })).toBeNull();
      expect(screen.queryByText('JOIN42')).toBeNull();
    });

    it.each(['configured', 'scheduled', 'active', 'paused'] as const)(
      'shows the join card while the run is %s',
      async (state) => {
        vi.mocked(useRunState).mockReturnValue(makeRunState(state));
        renderDashboard();

        expect(await screen.findByRole('heading', { name: 'Beitrittscode' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Beitrittscode groß anzeigen' })).toBeInTheDocument();
        expect(screen.getByText('JOIN42')).toBeInTheDocument();
      },
    );

    // An average of no feedback is no grade.
    it('says that no feedback has arrived instead of "0.0 / 5 (0)"', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('ended'));
      renderDashboard();

      expect(await screen.findByText('Noch kein Feedback eingegangen.')).toBeInTheDocument();
      expect(screen.queryByText(/0\.0 \/ 5/)).toBeNull();
      expect(screen.queryByText(/Bewertung:/)).toBeNull();
    });

    // The sent notices must not read as one run of words ("Countdown21:22TEST
    // NVDA Countdown Countdown21:26..."), and a screen reader must not read them
    // unasked when the page opens.
    it('lists past notices as list items with kind, time and text apart, outside a live region', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      vi.mocked(apiClient.get).mockImplementation((url: string) => {
        if (url.includes('/team-progress')) return Promise.resolve({ data: { teams: [] } });
        if (url.includes('/feed/')) {
          return Promise.resolve({
            data: {
              items: [
                feedItem('f1', 'countdown', { text: 'TEST NVDA Countdown' }),
                feedItem('f2', 'announcement', { text: 'Treffpunkt am Rathaus' }),
              ],
              total: 2,
              skip: 0,
              limit: 100,
            },
          });
        }
        return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      });
      renderDashboard();

      const list = await screen.findByRole('list', { name: 'Vergangene Mitteilungen' });
      const items = within(list).getAllByRole('listitem');
      expect(items).toHaveLength(2);
      expect(list.closest('[aria-live]')).toBeNull();

      const first = items[0];
      const time = first.querySelector('time');
      expect(time).not.toBeNull();
      expect(time).toHaveAttribute('datetime', '2026-01-01T10:05:00Z');
      // Kind and time do not run together, and the text is its own paragraph.
      expect(first.textContent).toContain(`Countdown, ${time!.textContent}`);
      expect(within(first).getByText('TEST NVDA Countdown').closest('p')).toHaveTextContent(
        /^TEST NVDA Countdown$/,
      );

      // Nothing is announced for what was already there.
      expect(screen.getByTestId('notice-announcement')).toBeEmptyDOMElement();
    });

    it('announces only a notice that arrives while the page is open', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      renderDashboard();
      await screen.findByText('Noch keine Mitteilungen.');
      const region = screen.getByTestId('notice-announcement');
      expect(region).toHaveAttribute('aria-live', 'polite');
      expect(region).toBeEmptyDOMElement();

      const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
      const arrive = (data: unknown) =>
        act(() => {
          handlers.feed_event(data, { type: 'feed_event', data, timestamp: '2026-01-01T10:06:00Z' });
        });

      arrive(feedItem('f9', 'alert', { text: 'Bitte zurück zum Rathaus' }));
      expect(region).toHaveTextContent('Alarm: Bitte zurück zum Rathaus');
      expect(
        within(screen.getByRole('list', { name: 'Vergangene Mitteilungen' })).getAllByRole('listitem'),
      ).toHaveLength(1);

      // A system line is no notice of the coordinator and is not announced.
      arrive(feedItem('f10', 'system', { de: 'Quiz automatisch bewertet: 10/10 Punkte' }));
      expect(region).toHaveTextContent('Alarm: Bitte zurück zum Rathaus');
    });
  });

  // Answers the team list with `teams`, everything else as the defaults do.
  const mockTeams = (teams: unknown[]) =>
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) return Promise.resolve({ data: { teams: [] } });
      if (url.includes('/teams/')) {
        return Promise.resolve({ data: { items: teams, total: teams.length, skip: 0, limit: 100 } });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });

  const member = (id: string, display_name: string) => ({ id, display_name, is_guest: true, joined_at: '2026-01-01T10:00:00Z' });

  // The large join code for a projector.
  describe('large join code', () => {
    const setWindow = (width: number, height: number) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
    };
    const original = { width: window.innerWidth, height: window.innerHeight };
    afterEach(() => setWindow(original.width, original.height));

    it('opens from the card with the name, a QR code of 60 % and more, the code at 96 px and more, the same link', async () => {
      setWindow(1366, 768);
      qr.values.length = 0;
      vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
      renderDashboard();

      const opener = await screen.findByRole('button', { name: 'Beitrittscode groß anzeigen' });
      expect(opener).toHaveTextContent('Groß anzeigen');
      await userEvent.click(opener);

      const view = await screen.findByRole('dialog', { name: 'Herbst-Rallye' });
      expect(within(view).getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument();
      const svg = within(view).getByTestId('join-presentation-qr').querySelector('svg')!;
      expect(Number(svg.getAttribute('width'))).toBeGreaterThanOrEqual(0.6 * 768);
      const code = within(view).getByTestId('join-presentation-code');
      expect(code).toHaveTextContent('JOIN42');
      expect(parseFloat(getComputedStyle(code).fontSize)).toBeGreaterThanOrEqual(96);

      const link = `${window.location.origin}/join/JOIN42`;
      expect(qr.values.length).toBeGreaterThanOrEqual(2);
      expect(new Set(qr.values)).toEqual(new Set([link]));
      expect(view).toHaveTextContent(link.replace(/^https?:\/\//, ''));
    });

    it('shows no navigation and no team data, and a help request toast waits until the view closes', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      mockTeams([{ ...teamFixture, members: [member('m1', 'Samira')] }]);
      renderDashboard();
      await screen.findByText('Samira');

      await userEvent.click(screen.getByRole('button', { name: 'Beitrittscode groß anzeigen' }));
      const view = await screen.findByRole('dialog', { name: 'Herbst-Rallye' });
      expect(within(view).queryByRole('navigation')).toBeNull();
      expect(within(view).queryByRole('link')).toBeNull();
      expect(view).not.toHaveTextContent('Team Alpha');
      expect(view).not.toHaveTextContent('Samira');
      expect(within(view).getAllByRole('button')).toHaveLength(1);

      const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
      const helpEvent = {
        id: 'fe-help',
        event_type: 'system',
        content: { team_id: 'team-1', team_name: 'Team Alpha', de: 'Team Team Alpha benötigt Hilfe.' },
        created_at: '2026-01-01T10:00:00Z',
      };
      act(() => {
        handlers.feed_event(helpEvent, { type: 'feed_event', data: helpEvent, timestamp: helpEvent.created_at });
      });
      // The help request arrived: the page underneath marks the team.
      await waitFor(() => expect(document.body).toHaveTextContent('Team Alpha bittet um Unterstützung'));
      expect(document.querySelector('.MuiSnackbar-root')).toBeNull();

      await userEvent.keyboard('{Escape}');
      expect(await screen.findByRole('alert')).toHaveTextContent('Team Alpha bittet um Unterstützung');
    });

    it('Escape and "Schließen" leave the view, and the focus returns to the button', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
      renderDashboard();
      const opener = await screen.findByRole('button', { name: 'Beitrittscode groß anzeigen' });

      await userEvent.click(opener);
      const view = await screen.findByRole('dialog', { name: 'Herbst-Rallye' });
      const close = within(view).getByRole('button', { name: 'Schließen' });
      await waitFor(() => expect(close).toHaveFocus());
      await userEvent.keyboard('{Escape}');
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      await waitFor(() => expect(opener).toHaveFocus());

      await userEvent.click(opener);
      await userEvent.click(
        within(await screen.findByRole('dialog', { name: 'Herbst-Rallye' })).getByRole('button', { name: 'Schließen' }),
      );
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      await waitFor(() => expect(opener).toHaveFocus());
    });

    it('opening the view does not fold the card', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
      renderDashboard();
      await userEvent.click(await screen.findByRole('button', { name: 'Beitrittscode groß anzeigen' }));
      await userEvent.keyboard('{Escape}');
      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
      expect(screen.getByRole('button', { name: 'Beitrittscode', expanded: true })).toBeInTheDocument();
    });

    it('has no axe violations in the open view', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
      renderDashboard();
      await userEvent.click(await screen.findByRole('button', { name: 'Beitrittscode groß anzeigen' }));
      const view = await screen.findByRole('dialog', { name: 'Herbst-Rallye' });
      expect(await axe(view)).toHaveNoViolations();
    });
  });

  // Who is in a team, in the team row.
  describe('member names', () => {
    it('names up to three members and "+ n" for the rest', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      const eight = ['Samira', 'Jonas', 'Aylin', 'Oleksandr', 'Maria', 'Anh', 'Ahmad', 'Emre'];
      mockTeams([
        { ...teamFixture, member_count: 8, members: eight.map((n, i) => member(`m${i}`, n)) },
        { ...teamFixture, id: 'team-2', name: 'Team Beta', member_count: 1 },
      ]);
      renderDashboard();

      const names = await screen.findByTestId('team-member-names');
      expect(names).toHaveTextContent(/^Samira, Jonas, Aylin\s*\+ 5\s*und 5 weitere$/);
      // The visible "+ 5" is hidden from a screen reader, which hears "und 5 weitere".
      expect(within(names).getByText('+ 5')).toHaveAttribute('aria-hidden', 'true');
      expect(names).not.toHaveTextContent('Oleksandr');
      // A list without names, as a participant gets it, shows no names line.
      expect(screen.getAllByTestId('team-member-names')).toHaveLength(1);
    });

    it('reloads the team list at once when a member joins, leaves or is moved', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      let members = [member('m1', 'Samira')];
      vi.mocked(apiClient.get).mockImplementation((url: string) => {
        if (url.includes('/team-progress')) return Promise.resolve({ data: { teams: [] } });
        if (url.includes('/teams/')) {
          return Promise.resolve({ data: { items: [{ ...teamFixture, members }], total: 1, skip: 0, limit: 100 } });
        }
        return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      });
      renderDashboard();
      expect(await screen.findByTestId('team-member-names')).toHaveTextContent('Samira');

      const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
      members = [member('m1', 'Samira'), member('m2', 'Lea')];
      act(() => {
        const data = { run_id: 'run-1', team_id: 'team-1' };
        handlers.team_members_changed(data, { type: 'team_members_changed', data, timestamp: '2026-01-01T10:00:00Z' });
      });
      await waitFor(() => expect(screen.getByTestId('team-member-names')).toHaveTextContent('Samira, Lea'));

      members = [member('m2', 'Lea')];
      act(() => {
        const data = { run_id: 'run-1', member_id: 'm1', from_team_id: 'team-1', to_team_id: 'team-9' };
        handlers.team_member_moved(data, { type: 'team_member_moved', data, timestamp: '2026-01-01T10:01:00Z' });
      });
      await waitFor(() => expect(screen.getByTestId('team-member-names')).toHaveTextContent(/^Lea$/));
    });

    // The open dialog "Team verwalten" follows the same live event
    // as the rows behind it.
    it('shows a member who joins while the members dialog is open', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      let members = [member('m1', 'Samira')];
      vi.mocked(apiClient.get).mockImplementation((url: string) => {
        if (url.includes('/team-progress')) return Promise.resolve({ data: { teams: [] } });
        if (url.endsWith('/teams/team-1')) return Promise.resolve({ data: { ...teamFixture, members } });
        if (url.includes('/teams/')) {
          return Promise.resolve({ data: { items: [{ ...teamFixture, members }], total: 1, skip: 0, limit: 100 } });
        }
        return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      });
      renderDashboard();
      await screen.findByTestId('team-member-names');

      await userEvent.click(screen.getByRole('button', { name: 'Team Alpha verwalten' }));
      const dialog = await screen.findByRole('dialog');
      expect(await within(dialog).findByText('Samira')).toBeInTheDocument();

      const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
      members = [member('m1', 'Samira'), member('m2', 'Lea')];
      act(() => {
        const data = { run_id: 'run-1', team_id: 'team-1' };
        handlers.team_members_changed(data, { type: 'team_members_changed', data, timestamp: '2026-01-01T10:00:00Z' });
      });
      expect(await within(dialog).findByText('Lea')).toBeInTheDocument();
    });
  });

  // A team leaves the run page with the step, without a reload.
  // The page asks plainly and sends every step without `confirm_dissolution`.
  // Only the server's refusal TEAM_WOULD_DISSOLVE brings up the dissolution
  // confirmation, and only its confirmation sends the field.
  describe('a team is removed', () => {
    const beta = { ...teamFixture, id: 'team-2', name: 'Team Beta', member_count: 1 };
    // The server's list of teams: a removed team is gone from the next read.
    let live: (typeof teamFixture)[];
    const listAnswer = (items = live) => ({ data: { items, total: items.length, skip: 0, limit: 100 } });
    // `over.list` replaces the answer of the team list: a request that is held.
    const mockRun = (over: { list?: () => Promise<unknown> | undefined } = {}) =>
      vi.mocked(apiClient.get).mockImplementation((url: string) => {
        if (url.includes('/team-progress')) return Promise.resolve({ data: { teams: [] } });
        if (url.includes('/scores/scoreboard')) {
          return Promise.resolve({
            data: { run_id: 'run-1', entries: [], is_final: false, scored_count: 0, submission_count: 0 },
          });
        }
        if (url.includes('/feedback/summary')) {
          return Promise.resolve({ data: { average_rating: 0, total_count: 0, distribution: {} } });
        }
        if (url.endsWith('/runs/run-1')) return Promise.resolve({ data: runFixture('active') });
        if (url.endsWith('/teams/team-1')) {
          return Promise.resolve({ data: { ...teamFixture, members: [member('m1', 'Samira'), member('m2', 'Lea')] } });
        }
        if (url.includes('/teams/')) return over.list?.() ?? Promise.resolve(listAnswer());
        return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
      });

    const wouldDissolve = (facts: Record<string, unknown> = {}) =>
      new ApiError(409, 'The step would dissolve the team.', 'TEAM_WOULD_DISSOLVE', {
        error_code: 'TEAM_WOULD_DISSOLVE',
        member_count: 2,
        answer_count: 4,
        result_published: false,
        ...facts,
      });
    const staff = (members: number, answers: number, team = 'Team Alpha') =>
      `Mit diesem Schritt wird das Team ${team} aufgelöst. Mitglieder: ${members}, Antworten: ${answers}. Das Team verlässt die Rangliste.`;
    const finalRanking = 'Der Lauf ist beendet. Die Endrangliste ändert sich.';
    const publicResult = 'Das Ergebnis ist veröffentlicht. Das öffentliche Ergebnis ändert sich sofort.';
    const teamUrl = '/api/orgs/test-org/runs/run-1/teams/team-1';
    const memberUrl = `${teamUrl}/members/m1`;
    const confirmed = { params: { confirm_dissolution: true } };
    // The server as the contract has it: a DELETE of the team without the
    // field is refused, with it the team is gone.
    const serverRemovesTeam = (facts: Record<string, unknown> = {}) =>
      vi.mocked(apiClient.delete).mockImplementation((_url: string, config?: unknown) => {
        if (config === undefined) return Promise.reject(wouldDissolve(facts));
        live = [beta];
        return Promise.resolve({ status: 204 });
      });

    const openDialog = async () => {
      await userEvent.click(await screen.findByRole('button', { name: 'Team Alpha verwalten' }));
      const dialog = await screen.findByRole('dialog');
      await within(dialog).findByText('Samira');
      return dialog;
    };
    // "Team entfernen", and the plain question behind it answered with yes.
    const askToRemoveTeam = async () => {
      const dialog = await openDialog();
      await userEvent.click(within(dialog).getByRole('button', { name: 'Team entfernen' }));
      await userEvent.click(within(dialog).getByRole('button', { name: 'Team entfernen' }));
      return dialog;
    };
    const askToRemoveSamira = async () => {
      const dialog = await openDialog();
      await userEvent.click(within(dialog).getByRole('button', { name: 'Samira aus dem Team entfernen' }));
      await userEvent.click(within(dialog).getByRole('button', { name: 'Entfernen' }));
      return dialog;
    };

    beforeEach(() => {
      live = [teamFixture, beta];
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    });

    it("\"Team entfernen\" is refused first, confirmed with the server's numbers, and the list and the count follow", async () => {
      mockRun();
      serverRemovesTeam({ run_ended: true, result_published: true });
      renderDashboard();
      expect(await screen.findByText('Teams (2)')).toBeInTheDocument();

      const dialog = await askToRemoveTeam();

      expect(await within(dialog).findByText(staff(2, 4))).toBeInTheDocument();
      // The page holds an active run: both sentences come from the refusal.
      expect(within(dialog).getByText(finalRanking)).toBeInTheDocument();
      expect(within(dialog).getByText(publicResult)).toBeInTheDocument();
      expect(apiClient.delete).toHaveBeenCalledTimes(1);
      expect(apiClient.delete).toHaveBeenLastCalledWith(teamUrl);
      expect(screen.getByText('Teams (2)')).toBeInTheDocument();

      await userEvent.click(within(dialog).getByRole('button', { name: 'Team entfernen' }));

      await waitFor(() => expect(apiClient.delete).toHaveBeenCalledTimes(2));
      expect(apiClient.delete).toHaveBeenLastCalledWith(teamUrl, confirmed);
      expect(await screen.findByText('Teams (1)')).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(screen.queryByRole('button', { name: 'Team Alpha verwalten' })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Team Beta verwalten' })).toBeInTheDocument();
    });

    it('the event team_dissolved reloads the teams and closes the dialog of that team', async () => {
      mockRun();
      renderDashboard();
      expect(await screen.findByText('Teams (2)')).toBeInTheDocument();
      await openDialog();

      const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
      live = [beta];
      act(() => {
        const data = { run_id: 'run-1', team_id: 'team-1' };
        handlers.team_dissolved(data, { type: 'team_dissolved', data, timestamp: '2026-01-01T10:00:00Z' });
      });

      expect(await screen.findByText('Teams (1)')).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(screen.queryByRole('button', { name: 'Team Alpha verwalten' })).not.toBeInTheDocument();
    });

    // The team list drops an older answer.
    it('a list answer from before the removal that arrives after it does not bring the team back', async () => {
      let hold = false;
      let release: (v: unknown) => void = () => {};
      let held: Promise<unknown> | undefined;
      mockRun({
        list: () => {
          if (!hold) return undefined;
          hold = false;
          held = new Promise((resolve) => {
            release = resolve;
          });
          return held;
        },
      });
      serverRemovesTeam();
      renderDashboard();
      expect(await screen.findByText('Teams (2)')).toBeInTheDocument();
      const dialog = await openDialog();

      // A member change starts a read of the list. Its answer is held.
      hold = true;
      const before = listAnswer();
      const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
      act(() => {
        const data = { run_id: 'run-1', team_id: 'team-1' };
        handlers.team_members_changed(data, { type: 'team_members_changed', data, timestamp: '2026-01-01T10:00:00Z' });
      });
      await waitFor(() => expect(held).toBeDefined());

      await userEvent.click(within(dialog).getByRole('button', { name: 'Team entfernen' }));
      await userEvent.click(within(dialog).getByRole('button', { name: 'Team entfernen' }));
      await within(dialog).findByText(staff(2, 4));
      await userEvent.click(within(dialog).getByRole('button', { name: 'Team entfernen' }));
      expect(await screen.findByText('Teams (1)')).toBeInTheDocument();

      // The answer from before the removal arrives last.
      await act(async () => {
        release(before);
        await held;
      });

      expect(screen.getByText('Teams (1)')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Team Alpha verwalten' })).not.toBeInTheDocument();
    });

    it('a removal the server does is one DELETE without the field, and no second question', async () => {
      mockRun();
      vi.mocked(apiClient.delete).mockResolvedValue({ status: 204 });
      renderDashboard();
      const dialog = await askToRemoveSamira();

      await waitFor(() => expect(apiClient.delete).toHaveBeenCalledTimes(1));
      expect(apiClient.delete).toHaveBeenLastCalledWith(memberUrl);
      expect(await within(dialog).findByText('Lea')).toBeInTheDocument();
      expect(within(dialog).queryByText(/aufgelöst/)).not.toBeInTheDocument();
    });

    it('a removal the server refuses asks with its numbers, and the second DELETE carries confirm_dissolution', async () => {
      mockRun();
      vi.mocked(apiClient.delete)
        .mockRejectedValueOnce(wouldDissolve({ member_count: 1, answer_count: 5, result_published: true }))
        .mockImplementation(() => {
          live = [beta];
          return Promise.resolve({ status: 204 });
        });
      renderDashboard();
      const dialog = await askToRemoveSamira();

      expect(await within(dialog).findByText(staff(1, 5))).toBeInTheDocument();
      expect(within(dialog).getByText(publicResult)).toBeInTheDocument();
      // No run_ended in the answer: read as false.
      expect(within(dialog).queryByText(finalRanking)).not.toBeInTheDocument();
      expect(apiClient.delete).toHaveBeenCalledTimes(1);
      expect(apiClient.delete).toHaveBeenLastCalledWith(memberUrl);

      await userEvent.click(within(dialog).getByRole('button', { name: 'Entfernen' }));

      await waitFor(() => expect(apiClient.delete).toHaveBeenCalledTimes(2));
      expect(apiClient.delete).toHaveBeenLastCalledWith(memberUrl, confirmed);
      expect(await screen.findByText('Teams (1)')).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    });

    it('a move the server refuses asks with its numbers, and the second call carries confirm_dissolution in its body', async () => {
      mockRun();
      vi.mocked(apiClient.post)
        .mockRejectedValueOnce(wouldDissolve({ member_count: 1, answer_count: 5, run_ended: true }))
        .mockResolvedValue({ status: 204 });
      renderDashboard();
      const dialog = await openDialog();
      await userEvent.click(within(dialog).getByRole('button', { name: 'Samira in ein anderes Team verschieben' }));
      await userEvent.click(await within(dialog).findByRole('button', { name: /Team Beta/ }));

      expect(await within(dialog).findByText(staff(1, 5))).toBeInTheDocument();
      expect(within(dialog).getByText(finalRanking)).toBeInTheDocument();
      expect(within(dialog).queryByText(publicResult)).not.toBeInTheDocument();
      expect(apiClient.post).toHaveBeenCalledTimes(1);
      expect(apiClient.post).toHaveBeenLastCalledWith(`${memberUrl}/move`, { target_team_id: 'team-2' });

      await userEvent.click(
        within(dialog).getByRole('button', { name: 'Samira in ein anderes Team verschieben' }),
      );

      await waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(2));
      expect(apiClient.post).toHaveBeenLastCalledWith(`${memberUrl}/move`, {
        target_team_id: 'team-2',
        confirm_dissolution: true,
      });
    });

    it('a cancel after the refusal sends no second call, and the team stays', async () => {
      mockRun();
      vi.mocked(apiClient.delete).mockRejectedValueOnce(wouldDissolve());
      renderDashboard();
      const dialog = await askToRemoveSamira();
      await within(dialog).findByText(staff(2, 4));

      await userEvent.click(within(dialog).getByRole('button', { name: 'Abbrechen' }));

      expect(await within(dialog).findByText('Samira')).toBeInTheDocument();
      expect(apiClient.delete).toHaveBeenCalledTimes(1);
      expect(screen.getByText('Teams (2)')).toBeInTheDocument();
    });

    it('opening the dialog reads neither the result link nor the run for a confirmation', async () => {
      mockRun();
      renderDashboard();
      await screen.findByText('Teams (2)');
      vi.mocked(apiClient.get).mockClear();
      await openDialog();

      const urls = vi.mocked(apiClient.get).mock.calls.map(([url]) => url);
      expect(urls).toEqual([teamUrl]);
    });

    it('a team renamed while its dialog is open is named by its new name', async () => {
      mockRun();
      vi.mocked(apiClient.delete).mockRejectedValue(wouldDissolve());
      renderDashboard();
      const dialog = await openDialog();
      await userEvent.click(within(dialog).getByRole('button', { name: 'Team entfernen' }));
      expect(within(dialog).getByText('Team Team Alpha wirklich entfernen?')).toBeInTheDocument();

      live = [{ ...teamFixture, name: 'Die Fugger' }, beta];
      const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
      act(() => {
        const data = { team_id: 'team-1' };
        handlers['team.name_updated'](data, {
          type: 'team.name_updated',
          data,
          timestamp: '2026-01-01T10:00:00Z',
        });
      });

      expect(await within(dialog).findByText('Team Die Fugger wirklich entfernen?')).toBeInTheDocument();
      await userEvent.click(within(dialog).getByRole('button', { name: 'Team entfernen' }));
      expect(await within(dialog).findByText(staff(2, 4, 'Die Fugger'))).toBeInTheDocument();
    });
  });

  // When teams can join, next to the button that opens joining.
  describe('when teams can join', () => {
    it.each([
      { state: 'configured' as RunState, run: {}, expected: /^Teams können beitreten, sobald du den Lauf startest\. Code und QR-Code/ },
      { state: 'scheduled' as RunState, run: { scheduled_for: '2026-01-02T09:00:00Z' }, expected: /von selbst zur geplanten Zeit oder vorher mit „Starten“/ },
      {
        state: 'scheduled' as RunState,
        run: { scheduled_for: '2026-01-01T09:00:00Z', settings: { auto_start_blocked: { code: 'SNAPSHOT_FAILED' } } },
        expected: /^Teams können beitreten, sobald du den Lauf startest\./,
      },
      { state: 'paused' as RunState, run: {}, expected: /^Während der Pause können keine Teams beitreten\./ },
    ])('$state $run says when teams can join', async ({ state, run, expected }) => {
      const runState = makeRunState(state);
      runState.run = { ...runState.run, ...run } as never;
      vi.mocked(useRunState).mockReturnValue(runState);
      renderDashboard();
      expect(await screen.findByTestId('join-window')).toHaveTextContent(expected);
    });

    it('says nothing once the run is active', async () => {
      vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
      renderDashboard();
      await screen.findByRole('heading', { name: 'Herbst-Rallye' });
      expect(screen.queryByTestId('join-window')).toBeNull();
    });
  });

  // A web address in a past notice is a link, as on the teams' side.
  it('shows a web address in a past notice as a link that opens in a new tab', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) return Promise.resolve({ data: { teams: [] } });
      if (url.includes('/feed/')) {
        const notice = {
          id: 'f-link',
          organization_id: 'org-1',
          run_id: 'run-1',
          event_type: 'announcement',
          content: { text: 'Plan unter https://www.augsburg.de/plan ansehen' },
          author_id: null,
          target_team_id: null,
          created_at: '2026-01-01T10:05:00Z',
        };
        return Promise.resolve({ data: { items: [notice], total: 1, skip: 0, limit: 100 } });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });
    renderDashboard();

    const link = await screen.findByRole('link', { name: 'https://www.augsburg.de/plan' });
    expect(link).toHaveAttribute('href', 'https://www.augsburg.de/plan');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link.closest('p')).toHaveTextContent('Plan unter https://www.augsburg.de/plan ansehen');
  });

  it('offers no reminder in the notice form and still shows past reminders', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('active'));
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/team-progress')) return Promise.resolve({ data: { teams: [] } });
      if (url.includes('/feed/')) {
        const old = {
          id: 'f-old',
          organization_id: 'org-1',
          run_id: 'run-1',
          event_type: 'reminder',
          content: { text: 'Denkt an die Fotos' },
          author_id: null,
          target_team_id: null,
          created_at: '2026-01-01T10:05:00Z',
        };
        return Promise.resolve({ data: { items: [old], total: 1, skip: 0, limit: 100 } });
      }
      return Promise.resolve({ data: { items: [], total: 0, skip: 0, limit: 100 } });
    });
    renderDashboard();

    expect(await screen.findByText('Denkt an die Fotos')).toBeInTheDocument();
    // The past reminder keeps its label in the list of past notices.
    expect(screen.getByText('Erinnerung')).toBeInTheDocument();
    // The type buttons are named by their tooltip, so read their labels.
    const types = screen.getByRole('group');
    expect(within(types).getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Mitteilung',
      'Countdown',
      'Alarm',
    ]);
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(useRunState).mockReturnValue(makeRunState('configured'));
    const { container } = renderDashboard();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Herbst-Rallye' })).toBeInTheDocument(),
    );
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
