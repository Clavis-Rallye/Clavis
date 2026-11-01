import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router';
import Scoreboard from './Scoreboard';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';
import { useWebSocket } from '../../hooks/useWebSocket';
import type { WsMessage } from '../../hooks/useWebSocket';
import type { Scoreboard as ScoreboardType, ScoreboardEntry } from '../../api/types';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

vi.mock('../../hooks/useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

const authUser = {
  id: 'u1',
  email: 'coord@example.com',
  display_name: 'Coordinator',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const entry = (
  team_id: string,
  team_name: string,
  total_points: number,
  rank: number,
): ScoreboardEntry => ({
  team_id,
  team_name,
  total_points,
  total_max_points: 150,
  scored_count: 2,
  last_scored_at: null,
  rank,
  is_winner: false,
});

/** The server's answer: Gamma and Delta have equal points and share place 3, Epsilon is 5th. */
const tiedStanding = (): ScoreboardType => ({
  run_id: 'run-1',
  is_final: false,
  scored_count: 10,
  submission_count: 12,
  entries: [
    entry('t1', 'Team Alpha', 120, 1),
    entry('t2', 'Team Beta', 100, 2),
    entry('t3', 'Team Gamma', 90, 3),
    entry('t4', 'Team Delta', 90, 3),
    entry('t5', 'Team Epsilon', 60, 5),
  ],
});

const mockStanding = (standing: ScoreboardType) => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url.includes('/scores/scoreboard')) return Promise.resolve({ data: standing });
    return Promise.resolve({ data: {} });
  });
};

const renderScoreboard = () =>
  render(
    <MemoryRouter initialEntries={['/runs/run-1/scoreboard']}>
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
          <Route path="/runs/:runId/scoreboard" element={<Scoreboard />} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

/** The place the page shows for a team: the first cell of the team's row. */
const placeOf = (teamName: string): string => {
  const row = screen.getByText(teamName).closest('tr');
  if (!row) throw new Error(`no table row for ${teamName}`);
  return within(row).getAllByRole('cell')[0]?.textContent ?? '';
};

/** Team name to place, read from the exported lines (header skipped). */
const placesIn = (exported: string, separator: string): Record<string, string> =>
  Object.fromEntries(
    exported
      .split('\n')
      .slice(1)
      .map((line) => {
        const [place = '', team = ''] = line.split(separator);
        return [team, place];
      }),
  );

const sharedPlaces = {
  'Team Alpha': '1',
  'Team Beta': '2',
  'Team Gamma': '3',
  'Team Delta': '3',
  'Team Epsilon': '5',
};

describe('Scoreboard (staff standing)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('two teams with equal points share a place, the next team stands after the gap', async () => {
    mockStanding(tiedStanding());
    renderScoreboard();

    await waitFor(() => expect(screen.getByText('Team Gamma')).toBeInTheDocument());

    expect(placeOf('Team Alpha')).toBe('1');
    expect(placeOf('Team Beta')).toBe('2');
    expect(placeOf('Team Gamma')).toBe('3');
    expect(placeOf('Team Delta')).toBe('3');
    expect(placeOf('Team Epsilon')).toBe('5');
  });

  it('the place is the one the server sent, the page computes none of its own', async () => {
    // Places no numbering by position and no ranking by points would produce.
    mockStanding({
      ...tiedStanding(),
      entries: [entry('t1', 'Team Alpha', 120, 4), entry('t2', 'Team Beta', 100, 9)],
    });
    renderScoreboard();

    await waitFor(() => expect(screen.getByText('Team Alpha')).toBeInTheDocument());

    expect(placeOf('Team Alpha')).toBe('4');
    expect(placeOf('Team Beta')).toBe('9');
  });

  it('the copied table carries the same places as the page', async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, 'writeText');
    mockStanding(tiedStanding());
    renderScoreboard();

    await waitFor(() => expect(screen.getByText('Team Gamma')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Kopieren' }));

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(placesIn(writeText.mock.calls[0]?.[0] ?? '', '\t')).toEqual(sharedPlaces);
  });

  it('the CSV export carries the same places as the page', async () => {
    const user = userEvent.setup();
    const exported: Blob[] = [];
    // jsdom has neither object URLs nor downloads.
    const createObjectURL = vi.fn((blob: Blob) => {
      exported.push(blob);
      return 'blob:scoreboard';
    });
    const originalCreate = Object.getOwnPropertyDescriptor(URL, 'createObjectURL');
    const originalRevoke = Object.getOwnPropertyDescriptor(URL, 'revokeObjectURL');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    try {
      mockStanding(tiedStanding());
      renderScoreboard();

      await waitFor(() => expect(screen.getByText('Team Gamma')).toBeInTheDocument());
      await user.click(screen.getByRole('button', { name: 'Exportieren' }));

      expect(createObjectURL).toHaveBeenCalledTimes(1);
      const csv = await exported[0]!.text();
      expect(csv.split('\n')[0]).toBe('Rank,Team,Points,Max Points,%,Scored');
      expect(placesIn(csv, ',')).toEqual(sharedPlaces);
      // The page shows the same places as the file.
      for (const [team, place] of Object.entries(sharedPlaces)) {
        expect(placeOf(team)).toBe(place);
      }
    } finally {
      if (originalCreate) Object.defineProperty(URL, 'createObjectURL', originalCreate);
      else Reflect.deleteProperty(URL, 'createObjectURL');
      if (originalRevoke) Object.defineProperty(URL, 'revokeObjectURL', originalRevoke);
      else Reflect.deleteProperty(URL, 'revokeObjectURL');
    }
  });

  it('reloads the standing when the run channel sends team_dissolved', async () => {
    mockStanding(tiedStanding());
    renderScoreboard();

    await waitFor(() => expect(screen.getByText('Team Gamma')).toBeInTheDocument());
    const standingCalls = () =>
      vi.mocked(apiClient.get).mock.calls.filter(([url]) => url.includes('/scores/scoreboard'))
        .length;
    const before = standingCalls();

    // The dissolved team has left the ranking; the server's next answer says so.
    mockStanding({
      ...tiedStanding(),
      entries: [entry('t1', 'Team Alpha', 120, 1), entry('t2', 'Team Beta', 100, 2)],
    });
    const handlers = vi.mocked(useWebSocket).mock.calls.at(-1)![0].handlers!;
    act(() => {
      handlers.team_dissolved!(
        {},
        { type: 'team_dissolved', data: {}, timestamp: '2026-01-01T12:00:00Z' } as WsMessage,
      );
    });

    await waitFor(() => expect(standingCalls()).toBe(before + 1));
    await waitFor(() => expect(screen.queryByText('Team Gamma')).not.toBeInTheDocument());
    expect(placeOf('Team Beta')).toBe('2');
  });
});
