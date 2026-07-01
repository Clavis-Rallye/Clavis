import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router';
import StationChallengeList from './StationChallengeList';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
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

const makeChallenge = (id: string, titleDe: string, sortOrder: number) => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  run_station_id: 's1',
  source_challenge_id: null,
  challenge_type: 'text',
  title: { de: titleDe },
  instructions: { de: '' },
  scoring_rules: { max_points: 10 },
  sort_order: sortOrder,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

const makeStation = (overrides: Record<string, unknown> = {}) => ({
  id: 's1',
  organization_id: 'org-1',
  run_id: 'run-1',
  source_station_id: null,
  title: { de: 'Stadttor' },
  description: { de: '' },
  location_lat: null,
  location_lng: null,
  sort_order: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

const TIPS = {
  header: { de: '<p>Mehr über das Stadttor</p>' },
  links: [{ label: { de: 'Stadtarchiv' }, url: 'https://archiv.example' }],
};

const makeSubmission = (
  runChallengeId: string,
  scored: { points: number; max_points: number } | null = null,
) => ({
  id: `sub-${runChallengeId}`,
  organization_id: 'org-1',
  run_id: 'run-1',
  team_id: 'team-1',
  run_challenge_id: runChallengeId,
  content: {},
  submitted_at: '2026-01-01T00:00:00Z',
  is_scored: scored !== null,
  points: scored?.points ?? null,
  max_points: scored?.max_points ?? null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

/** challenges, stations, submissions — the order StationChallengeList requests them in. */
const mockLoad = (
  submissions: ReturnType<typeof makeSubmission>[],
  station: ReturnType<typeof makeStation> = makeStation(),
) => {
  vi.mocked(apiClient.get)
    .mockResolvedValueOnce({
      data: {
        items: [
          makeChallenge('ch-1', 'Offene Aufgabe', 0),
          makeChallenge('ch-2', 'Eingereichte Aufgabe', 1),
          makeChallenge('ch-3', 'Bewertete Aufgabe', 2),
        ],
        total: 3,
        skip: 0,
        limit: 100,
      },
    })
    .mockResolvedValueOnce({ data: { items: [station], total: 1, skip: 0, limit: 100 } })
    .mockResolvedValueOnce({
      data: { items: submissions, total: submissions.length, skip: 0, limit: 100 },
    });
};

function LocationDisplay() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const renderList = (initialEntry = '/runs/run-1/participant/stations/s1') =>
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
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
            path="/runs/:runId/participant/stations/:stationId"
            element={<StationChallengeList />}
          />
          <Route
            path="/runs/:runId/participant/stations/:stationId/challenges/:challengeId"
            element={<LocationDisplay />}
          />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('StationChallengeList', () => {
  beforeEach(() => vi.resetAllMocks());

  it('distinguishes open, submitted and scored challenges', async () => {
    mockLoad([
      makeSubmission('ch-2'),
      makeSubmission('ch-3', { points: 7, max_points: 10 }),
    ]);

    const { container } = renderList();

    // Open: shows what is obtainable, not a result.
    await waitFor(() => expect(screen.getByText('10 Pkt.')).toBeInTheDocument());
    // Delivered but not yet graded.
    expect(screen.getByText('Eingereicht')).toBeInTheDocument();
    // Graded: says so, and says what was earned, not "Eingereicht".
    expect(screen.getByText('Bewertet · 7/10')).toBeInTheDocument();

    // One sign per state: empty circle, hourglass, tick. The trophy means ranking
    // only and does not mark a scored answer.
    expect(container.querySelectorAll('[data-testid="EmojiEventsIcon"]')).toHaveLength(0);
    expect(container.querySelectorAll('[data-testid="CheckCircleIcon"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-testid="HourglassEmptyIcon"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-testid="RadioButtonUncheckedIcon"]')).toHaveLength(1);

    // The chips name the submitted and the scored state, so those two icons
    // are hidden. The open circle has no such text and carries its name.
    expect(screen.getByRole('img', { name: 'Offen' })).toHaveAttribute(
      'data-testid',
      'RadioButtonUncheckedIcon',
    );
    expect(container.querySelector('[data-testid="HourglassEmptyIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(container.querySelector('[data-testid="CheckCircleIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    // The submitted chip is not the filled green one.
    expect(screen.getByText('Eingereicht').closest('.MuiChip-root')).not.toHaveClass(
      'MuiChip-colorSuccess',
    );
    expect(screen.getByText('Bewertet · 7/10').closest('.MuiChip-root')).toHaveClass(
      'MuiChip-colorSuccess',
    );
  });

  // The challenge card titles are headings of level 2, so the cards are
  // navigable by heading under the station's h1.
  it('renders the challenge titles as level-2 headings', async () => {
    mockLoad([]);
    renderList();

    await waitFor(() => expect(screen.getByText('1. Offene Aufgabe')).toBeInTheDocument());
    const headings = screen.getAllByRole('heading', { level: 2 });
    expect(headings).toHaveLength(3);
    expect(headings[0]).toHaveTextContent('1. Offene Aufgabe');
    expect(headings[1]).toHaveTextContent('2. Eingereichte Aufgabe');
    expect(headings[2]).toHaveTextContent('3. Bewertete Aufgabe');
  });

  // The challenge view needs the map provenance to return a finished station to
  // the map, so this list passes it on.
  it('opens a challenge carrying ?from=map when the station page has it', async () => {
    mockLoad([]);
    renderList('/runs/run-1/participant/stations/s1?from=map');

    await waitFor(() => expect(screen.getByText('1. Offene Aufgabe')).toBeInTheDocument());
    await userEvent.click(screen.getByText('1. Offene Aufgabe'));

    expect(screen.getByTestId('loc')).toHaveTextContent(
      '/runs/run-1/participant/stations/s1/challenges/ch-1?from=map',
    );
  });

  it('opens a challenge without ?from=map when the station page has none', async () => {
    mockLoad([]);
    renderList();

    await waitFor(() => expect(screen.getByText('1. Offene Aufgabe')).toBeInTheDocument());
    await userEvent.click(screen.getByText('1. Offene Aufgabe'));

    expect(screen.getByTestId('loc')).toHaveTextContent(
      '/runs/run-1/participant/stations/s1/challenges/ch-1',
    );
  });

  // The tips come with a sentence that says what they hold, and nothing shows
  // without tips.
  it('introduces the tips with a sentence, and shows neither without tips', async () => {
    mockLoad([], makeStation({ tips: TIPS }));
    const { unmount } = renderList();
    expect(
      await screen.findByText('Du willst mehr zum Thema wissen? Dann tippe auf Tipps & Links.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tipps & Links' })).toBeInTheDocument();
    unmount();

    mockLoad([], makeStation({ tips: null }));
    renderList();
    await screen.findByText('Offene Aufgabe', { exact: false });
    expect(screen.queryByText(/Du willst mehr zum Thema wissen/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tipps & Links' })).not.toBeInTheDocument();
  });

  // Every task card says that it opens, so teams know to tap it.
  it('says on every task card that it opens', async () => {
    mockLoad([makeSubmission('ch-2')]);
    renderList();
    const cards = await screen.findAllByRole('button', { name: /Öffnen/ });
    expect(cards).toHaveLength(3);
  });

  it('shows the place as a framed link to the maps app with its hint', async () => {
    mockLoad([], makeStation({ address: 'Rathausplatz 2', location_lat: 48.37, location_lng: 10.9 }));
    renderList();
    const link = await screen.findByRole('link', { name: 'Rathausplatz 2: in der Karten-App öffnen' });
    expect(link.getAttribute('href')).toMatch(/48\.37/);
    expect(screen.getByText('Antippen öffnet die Karten-App.')).toBeInTheDocument();
  });

  // The station and its tasks are frozen, so a refresh asks for the answers only.
  it('refreshes with one request for the answers only', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockLoad([]);
      renderList();
      await screen.findByText('Offene Aufgabe', { exact: false });
      expect(apiClient.get).toHaveBeenCalledTimes(3);
      vi.mocked(apiClient.get).mockResolvedValueOnce({
        data: { items: [makeSubmission('ch-1')], total: 1, skip: 0, limit: 100 },
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(apiClient.get).toHaveBeenCalledTimes(4);
      expect(vi.mocked(apiClient.get).mock.calls[3][0]).toBe('/api/orgs/test-org/runs/run-1/submissions/');
    } finally {
      vi.useRealTimers();
    }
  });

  // A grading that the coordinator takes back sends no live signal. The standing
  // follows it by asking every ten seconds, and this page asks at the same pace:
  // the old grade is gone within one round, no reload.
  it('drops the grade when the Spielleitung takes the grading back, at the pace of the standing', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockLoad([makeSubmission('ch-3', { points: 7, max_points: 10 })]);
      renderList();
      expect(await screen.findByText('Bewertet · 7/10')).toBeInTheDocument();

      // The score is gone, the answer is still there.
      vi.mocked(apiClient.get).mockResolvedValueOnce({
        data: { items: [makeSubmission('ch-3')], total: 1, skip: 0, limit: 100 },
      });
      // Not yet: one second short of a round.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(9_000);
      });
      expect(apiClient.get).toHaveBeenCalledTimes(3);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });

      expect(apiClient.get).toHaveBeenCalledTimes(4);
      expect(screen.queryByText(/Bewertet ·/)).not.toBeInTheDocument();
      expect(screen.getByText('Eingereicht')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  // The address link must be 24 px high. As a block of its own it takes the line
  // height of its text.
  it('gives the address link a touch target of 24 px', async () => {
    mockLoad([], makeStation({ address: 'Rathausplatz 2', location_lat: 48.37, location_lng: 10.9 }));
    renderList();
    const link = await screen.findByRole('link', { name: 'Rathausplatz 2: in der Karten-App öffnen' });
    expect(link).toHaveStyle({ display: 'inline-block', minHeight: '24px' });
  });
});
