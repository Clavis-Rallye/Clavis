import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router';
import { axe } from 'vitest-axe';
import StationList from './StationList';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';
import * as geocode from '../../utils/geocode';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

// The tiles of the map follow the installation's config. The default answer
// names the OSMF tiles, so the attribution credit of the map below stays the bare
// OpenStreetMap one.
vi.mock('../../utils/geocode', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../utils/geocode')>();
  return { ...actual, getGeocodeConfig: vi.fn() };
});

const authUser = {
  id: 'u1',
  email: 'participant@example.com',
  display_name: 'Teilnehmer',
  role: 'participant' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const makeStation = (
  id: string,
  titleDe: string,
  sortOrder: number,
  descriptionDe = '',
) => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  source_station_id: null,
  title: { de: titleDe },
  description: { de: descriptionDe },
  location_lat: null as number | null,
  location_lng: null as number | null,
  sort_order: sortOrder,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

const makeChallenge = (id: string, stationId: string, sortOrder: number) => ({
  id,
  organization_id: 'org-1',
  run_id: 'run-1',
  run_station_id: stationId,
  source_challenge_id: null,
  challenge_type: 'text',
  title: { de: `Aufgabe ${id}` },
  instructions: { de: '' },
  scoring_rules: { max_points: 10 },
  sort_order: sortOrder,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

/**
 * The one answer the list asks for: the stations, each with its challenges,
 * and the team's answered challenges.
 */
const overview = (
  stations: { station: ReturnType<typeof makeStation>; challenges: ReturnType<typeof makeChallenge>[] }[],
  answered: string[] = [],
) => ({
  data: {
    stations: stations.map(({ station, challenges }) => ({ ...station, challenges })),
    answered_challenge_ids: answered,
  },
});

const renderList = (entry = '/runs/run-1/participant/stations') =>
  render(
    <MemoryRouter initialEntries={[entry]}>
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
            path="/runs/:runId/participant/stations"
            element={<StationList />}
          />
          <Route
            path="/runs/:runId/participant/stations/:stationId"
            element={<div>Station challenges</div>}
          />
          <Route path="/no-runs" element={<div>No runs page</div>} />
        </Routes>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

describe('StationList', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(geocode.getGeocodeConfig).mockResolvedValue({
      typeahead: false,
      sources: [],
      centre: null,
      tile_url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
      tile_attribution: '',
    });
  });

  // One request per 15 s refresh: not one for the stations, one per station for
  // its tasks and one for the submissions.
  it('asks for one overview per load and per refresh', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    try {
      vi.mocked(apiClient.get).mockResolvedValue(
        overview([
          { station: makeStation('s1', 'Stadttor', 0), challenges: [makeChallenge('ch-1', 's1', 0)] },
          { station: makeStation('s2', 'Rathaus', 1), challenges: [makeChallenge('ch-2', 's2', 0)] },
        ]),
      );
      renderList();
      await waitFor(() => expect(screen.getByText('Rathaus')).toBeInTheDocument());
      expect(apiClient.get).toHaveBeenCalledTimes(1);
      expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/runs/run-1/participant-overview');

      await act(async () => {
        vi.advanceTimersByTime(15_000);
      });
      await waitFor(() => expect(apiClient.get).toHaveBeenCalledTimes(2));
    } finally {
      vi.useRealTimers();
    }
  });

  it('names the view toggle and the spinner in German', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(
      overview([{ station: makeStation('s1', 'Stadttor', 0), challenges: [] }]),
    );
    renderList();
    expect(screen.getByRole('progressbar', { name: 'Wird geladen …' })).toBeInTheDocument();

    expect(await screen.findByRole('button', { name: 'Liste' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Karte' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'list' })).not.toBeInTheDocument();
  });

  it('renders LinearProgress with done/total across challenges', async () => {
    // 1 station with 2 challenges; 1 answered → 1/2
    vi.mocked(apiClient.get).mockResolvedValueOnce(
      overview(
        [
          {
            station: makeStation('s1', 'Stadttor', 0),
            challenges: [makeChallenge('ch-1', 's1', 0), makeChallenge('ch-2', 's1', 1)],
          },
        ],
        ['ch-1'],
      ),
    );

    renderList();

    await waitFor(() => {
      expect(screen.getByText('Stadttor')).toBeInTheDocument();
    });
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });

  // The progress row needs a name, because "1 / 2" and "50%" name nothing alone.
  // The description needs a rule about where a clamped line may break.
  it('names the progress row and clamps the description on word boundaries', async () => {
    const description =
      'Der Brunnen steht mitten auf dem Rathausplatz und erzählt die Geschichte der Stadt.';
    vi.mocked(apiClient.get).mockResolvedValueOnce(
      overview(
        [
          {
            station: makeStation('s1', 'Augustusbrunnen', 0, description),
            challenges: [makeChallenge('ch-1', 's1', 0), makeChallenge('ch-2', 's1', 1)],
          },
        ],
        ['ch-1'],
      ),
    );

    renderList();

    const label = await screen.findByText('Aufgaben gelöst');
    expect(label).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toHaveAccessibleName('Aufgaben gelöst');
    expect(screen.getByText('1 / 2')).toBeInTheDocument();

    expect(screen.getByText(description)).toHaveStyle({
      display: '-webkit-box',
      overflow: 'hidden',
      overflowWrap: 'normal',
      wordBreak: 'normal',
      WebkitLineClamp: '2',
      WebkitBoxOrient: 'vertical',
    });
  });

  // The name is the text the card shows, in the order it shows it: number, title,
  // description, count of tasks, points. The axe rule label-content-name-mismatch
  // finds nothing in jsdom even on a wrong name, so the name itself is asserted.
  it('names each station card by the text it shows, in the same order', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(
      overview([
        {
          station: {
            ...makeStation('s1', 'Rathaus', 0),
            description: { de: 'Das Rathaus am Platz' },
          },
          challenges: [makeChallenge('ch-1', 's1', 0), makeChallenge('ch-2', 's1', 1)],
        },
      ]),
    );

    renderList();

    await waitFor(() => expect(screen.getByText('Rathaus')).toBeInTheDocument());
    const card = screen.getByRole('button', {
      name: /^1\s*Rathaus\s*Das Rathaus am Platz\s*0 von 2 Aufgaben\s*20 Pkt\.$/,
    });
    expect(card).not.toHaveAttribute('aria-label');
    expect(screen.queryByRole('button', { name: 'Station öffnen' })).not.toBeInTheDocument();
  });

  it('renders stations in sort_order order', async () => {
    // stations returned in reverse order from the API
    vi.mocked(apiClient.get).mockResolvedValueOnce(
      overview([
        { station: makeStation('s2', 'Zweite Station', 1), challenges: [] },
        { station: makeStation('s1', 'Erste Station', 0), challenges: [] },
      ]),
    );

    renderList();

    await waitFor(() => expect(screen.getByText('Erste Station')).toBeInTheDocument());
    expect(screen.getByText('Zweite Station')).toBeInTheDocument();
    const html = document.body.innerHTML;
    expect(html.indexOf('Erste Station')).toBeLessThan(html.indexOf('Zweite Station'));
  });

  it('all-done station gets outlined card variant and check icon', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(
      overview(
        [
          { station: makeStation('s1', 'Fertige Station', 0), challenges: [makeChallenge('ch-done', 's1', 0)] },
          { station: makeStation('s2', 'Offene Station', 1), challenges: [makeChallenge('ch-open', 's2', 0)] },
        ],
        ['ch-done'],
      ),
    );

    const { container } = renderList();

    await waitFor(() => expect(screen.getByText('Fertige Station')).toBeInTheDocument());
    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    // Singular with one task.
    const doneChip = screen.getByText('1 von 1 Aufgabe').closest('.MuiChip-root');
    const openChip = screen.getByText('0 von 1 Aufgabe').closest('.MuiChip-root');

    // The double tick marks the finished station, once as a badge on the avatar and
    // once in the chip. The open station has neither.
    expect(container.querySelectorAll('[data-testid="DoneAllIcon"]')).toHaveLength(2);
    // The finished station keeps its number in the avatar.
    const badge = screen.getByTestId('station-done-avatar-badge');
    expect(badge.querySelector('[data-testid="DoneAllIcon"]')).not.toBeNull();
    expect(badge.parentElement?.querySelector('.MuiAvatar-root')).toHaveTextContent('1');
    expect(screen.getAllByTestId('station-done-avatar-badge')).toHaveLength(1);
    expect(container.querySelectorAll('[data-testid="CheckIcon"]')).toHaveLength(0);
    expect(doneChip).toHaveClass('MuiChip-colorSuccess');
    expect(doneChip?.querySelector('[data-testid="DoneAllIcon"]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
    expect(openChip).not.toHaveClass('MuiChip-colorSuccess');
    expect(openChip?.querySelector('svg')).toBeNull();
  });

  it('empty state shows locked message when no stations', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(overview([]));

    renderList();

    await waitFor(() => {
      expect(screen.getByText(/Sobald die Rallye startet/i)).toBeInTheDocument();
    });
  });

  it('error path shows ErrorAlert with retry button', async () => {
    vi.mocked(apiClient.get).mockRejectedValueOnce(new Error('Netzwerkfehler'));

    renderList();

    await waitFor(() => {
      expect(screen.getByText(/netzwerkfehler/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /erneut versuchen/i })).toBeInTheDocument();
  });

  // A participant that the coordinator removed gets no station list. The overview
  // says so, and the list leaves.
  it('leaves the run when the overview says the participant has no team', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce({
      data: { stations: [], answered_challenge_ids: [], is_team_member: false },
    });

    renderList();

    await waitFor(() => expect(screen.getByText('No runs page')).toBeInTheDocument());
  });

  it('leaves the run on a NOT_TEAM_MEMBER refusal', async () => {
    const { ApiError } = await import('../../api/client');
    vi.mocked(apiClient.get).mockRejectedValueOnce(
      new ApiError(404, 'User is not a member of any team in this run', 'NOT_TEAM_MEMBER'),
    );

    renderList();

    await waitFor(() => expect(screen.getByText('No runs page')).toBeInTheDocument());
    expect(screen.queryByText(/not a member/)).not.toBeInTheDocument();
  });

  it('has no axe accessibility violations on initial render', async () => {
    vi.mocked(apiClient.get).mockResolvedValueOnce(overview([]));

    const { container } = renderList();
    await waitFor(() => expect(screen.getByText(/Sobald die Rallye startet/i)).toBeInTheDocument());
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  // The map container takes the focus (Leaflet gives it tabindex 0), so it needs a
  // role and a name.
  it('names the map of the stations as a region', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(
      overview([
        {
          station: { ...makeStation('s1', 'Rathaus', 0), location_lat: 48.36882, location_lng: 10.89862 },
          challenges: [],
        },
      ]),
    );

    renderList('/runs/run-1/participant/stations?view=map');

    expect(await screen.findByRole('region', { name: 'Karte der Stationen' })).toBeInTheDocument();
  });

  // Markers at one position must stay reachable. Each marker is a named button, a
  // station without a position is named below the map, and the map speaks the
  // interface language.
  it('shows the map with named station buttons, German zoom buttons and the bare OpenStreetMap credit', async () => {
    const placed = (id: string, title: string, order: number, lat: number, lng: number) => ({
      ...makeStation(id, title, order),
      location_lat: lat,
      location_lng: lng,
    });
    vi.mocked(apiClient.get).mockResolvedValue(
      overview(
        [
          { station: placed('s1', 'Rathaus', 0, 48.36882, 10.89862), challenges: [makeChallenge('ch-1', 's1', 0)] },
          { station: placed('s2', 'Perlachturm', 1, 48.36858, 10.89837), challenges: [makeChallenge('ch-2', 's2', 0)] },
          { station: makeStation('s3', 'Ohne Ort', 2), challenges: [makeChallenge('ch-3', 's3', 0)] },
        ],
        ['ch-2'],
      ),
    );

    const { container } = renderList('/runs/run-1/participant/stations?view=map');

    expect(await screen.findByRole('button', { name: 'Station 1: Rathaus' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Station 2: Perlachturm, Station abgeschlossen' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Ohne Ort/ })).not.toBeInTheDocument();
    expect(screen.getByText('1 Station hat keinen Standort und steht nur in der Liste.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Vergrößern' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Verkleinern' })).toBeInTheDocument();
    const attribution = container.querySelector('.leaflet-control-attribution');
    expect(attribution?.textContent?.trim()).toBe('\u00a9 OpenStreetMap');
    expect(attribution?.querySelector('a[href="https://www.openstreetmap.org/copyright"]')).not.toBeNull();
  });

  // The tiles follow the installation's MAP_TILE_URL, and the attribution follows
  // the tile host.
  it('credits the FOSSGIS tiles with the licence and the link to report errors', async () => {
    vi.mocked(geocode.getGeocodeConfig).mockResolvedValue({
      typeahead: false,
      sources: [],
      centre: null,
      tile_url: 'https://tile.openstreetmap.de/{z}/{x}/{y}.png',
      tile_attribution: '',
    });
    vi.mocked(apiClient.get).mockResolvedValue(
      overview([
        {
          station: { ...makeStation('s1', 'Rathaus', 0), location_lat: 48.36882, location_lng: 10.89862 },
          challenges: [],
        },
      ]),
    );

    const { container } = renderList('/runs/run-1/participant/stations?view=map');

    await screen.findByRole('button', { name: 'Station 1: Rathaus' });
    const attribution = container.querySelector('.leaflet-control-attribution');
    expect(attribution?.textContent).toContain('OpenStreetMap-Mitwirkende');
    expect(attribution?.textContent).toContain('FOSSGIS e.V., CC BY-SA 2.0');
    expect(attribution?.querySelector('a[href="https://www.openstreetmap.org/fixthemap"]')).not.toBeNull();
  });

  // When the installation did not answer, the map requests no tile of a server
  // nobody configured, and says so.
  it('shows no tiles but a notice when the config did not answer', async () => {
    vi.mocked(geocode.getGeocodeConfig).mockResolvedValue({
      typeahead: false,
      sources: [],
      centre: null,
      unknown: true,
    });
    vi.mocked(apiClient.get).mockResolvedValue(
      overview([
        {
          station: { ...makeStation('s1', 'Rathaus', 0), location_lat: 48.36882, location_lng: 10.89862 },
          challenges: [],
        },
      ]),
    );

    const { container } = renderList('/runs/run-1/participant/stations?view=map');

    await screen.findByRole('button', { name: 'Station 1: Rathaus' });
    expect(screen.getByText('Die Karte ist gerade nicht verfügbar.')).toBeInTheDocument();
    expect(container.querySelector('.leaflet-tile-container')).toBeNull();
  });

  // The FOSSGIS terms ask for an operator contact within one click of the map.
  it('offers the imprint link directly under the map, without a menu', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(
      overview([
        {
          station: { ...makeStation('s1', 'Rathaus', 0), location_lat: 48.36882, location_lng: 10.89862 },
          challenges: [],
        },
      ]),
    );

    renderList('/runs/run-1/participant/stations?view=map');

    const marker = await screen.findByRole('button', { name: 'Station 1: Rathaus' });
    const imprint = screen.getByRole('link', { name: 'Impressum' });
    expect(imprint).toHaveAttribute('href', '/legal/impressum');
    // One click: the link stands in the open page, not inside a popup or menu.
    expect(imprint.closest('[role="menu"], [role="dialog"], [role="tooltip"], .leaflet-popup')).toBeNull();
    expect(marker.closest('div')!.compareDocumentPosition(imprint)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  // Leaflet alone opens only the popup on Enter, and the popup holds no link.
  it('opens a station from its marker with Enter, as a tap does', async () => {
    vi.mocked(apiClient.get).mockResolvedValue(
      overview([
        {
          station: { ...makeStation('s1', 'Rathaus', 0), location_lat: 48.36882, location_lng: 10.89862 },
          challenges: [makeChallenge('ch-1', 's1', 0)],
        },
      ]),
    );
    renderList('/runs/run-1/participant/stations?view=map');
    const marker = await screen.findByRole('button', { name: 'Station 1: Rathaus' });
    marker.focus();
    fireEvent.keyDown(marker, { key: 'Enter', keyCode: 13 });
    expect(await screen.findByText('Station challenges')).toBeInTheDocument();
  });
});
