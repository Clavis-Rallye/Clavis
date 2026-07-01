import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { axe } from 'vitest-axe';
import RunAnalytics from './RunAnalytics';
import { AuthContext } from '../../auth/AuthContext';
import apiClient from '../../api/client';
import type { RunAnalytics as RunAnalyticsData } from '../../api/types';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

// @mui/x-charts renders real SVG geometry that jsdom can't lay out and that
// isn't the thing under test here — mock both chart components down to a
// stub that exposes the props they were given, so assertions can check the
// series/dataset shape without paying for full chart rendering.
vi.mock('@mui/x-charts/BarChart', () => ({
  BarChart: (props: Record<string, unknown>) => (
    <div data-testid="bar-chart" data-props={JSON.stringify(props)} />
  ),
}));
vi.mock('@mui/x-charts/LineChart', () => ({
  LineChart: (props: Record<string, unknown>) => (
    <div data-testid="line-chart" data-props={JSON.stringify(props)} />
  ),
}));

const authUser = {
  id: 'u1',
  email: 'coord@example.com',
  display_name: 'Coordinator',
  role: 'coordinator' as const,
  organization_id: 'org-1',
  organization_slug: 'test-org',
};

const runFixture = (overrides: Record<string, unknown> = {}) => ({
  id: 'run-1',
  organization_id: 'org-1',
  rallye_id: 'r1',
  name: 'Herbst-Rallye',
  state: 'ended',
  join_code: 'JOIN42',
  scheduled_for: null,
  started_at: '2026-01-01T09:00:00Z',
  paused_at: null,
  ended_at: '2026-01-01T12:00:00Z',
  settings: {},
  max_teams: null,
  content_languages: ['de', 'en'],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

// Distinct numbers everywhere on purpose — avoids getByText collisions
// between e.g. team_count and the submissions total.
const analyticsFixture = (overrides: Partial<RunAnalyticsData> = {}): RunAnalyticsData => ({
  run_id: 'run-1',
  participation: {
    team_count: 3,
    participant_count: 10,
    guest_count: 4,
    registered_count: 6,
  },
  stations: [
    { run_station_id: 'st-1', title: { de: 'Rathaus', en: 'Town Hall' }, teams_attempted: 3, teams_scored: 2 },
    { run_station_id: 'st-2', title: { de: 'Marktplatz', en: 'Market Square' }, teams_attempted: 2, teams_scored: 1 },
  ],
  challenges: [
    {
      run_station_id: 'rs-1',
      run_challenge_id: 'ch-1',
      station_title: { de: 'Rathaus', en: 'Town Hall' },
      title: { de: 'Foto-Aufgabe', en: 'Photo task' },
      type: 'photo',
      submissions: 3,
      scored: 2,
      avg_points: 8.5,
      max_points: 10,
    },
    {
      run_station_id: 'rs-1',
      run_challenge_id: 'ch-2',
      station_title: { de: 'Marktplatz', en: 'Market Square' },
      title: { de: 'Quiz-Aufgabe', en: 'Quiz task' },
      type: 'quiz',
      submissions: 2,
      scored: 1,
      avg_points: 5,
      max_points: 5,
    },
  ],
  // totals: submissions 3+2=5, scored 2+1=3 -> 60%
  teams: [
    { team_id: 'team-1',
      team_name: 'Team Alpha', member_count: 4, guest_count: 1, total_points: 42, challenges_attempted: 5, challenges_scored: 4 },
    { team_id: 'team-2',
      team_name: 'Team Beta', member_count: 3, guest_count: 2, total_points: 30, challenges_attempted: 4, challenges_scored: 3 },
  ],
  timeline: [
    { bucket_start: '2026-01-01T09:00:00Z', submissions: 2 },
    { bucket_start: '2026-01-01T09:15:00Z', submissions: 5 },
  ],
  feedback: { average_rating: 4.2, total_count: 5, distribution: { 4: 3, 5: 2 } },
  ...overrides,
});

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/analytics']}>
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
        <RunAnalytics />
      </AuthContext.Provider>
    </MemoryRouter>,
  );

const timeseriesFixture = () => ({
  range: { date_from: '2026-06-27', date_to: '2026-07-27', bucket: 'day' },
  totals: {
    run_count: 4,
    participant_count: 8,
    guest_count: 8,
    registered_count: 0,
    team_count: 8,
    submission_count: 5,
    scored_count: 1,
    unscored_count: 4,
  },
  buckets: [
    {
      bucket_start: '2026-07-18T00:00:00',
      run_count: 1,
      participant_count: 1,
      guest_count: 1,
      registered_count: 0,
      team_count: 1,
      submission_count: 0,
      scored_count: 0,
      unscored_count: 0,
    },
    {
      bucket_start: '2026-07-19T00:00:00',
      run_count: 2,
      participant_count: 6,
      guest_count: 6,
      registered_count: 0,
      team_count: 6,
      submission_count: 4,
      scored_count: 1,
      unscored_count: 3,
    },
  ],
});

const mockApi = (analytics: RunAnalyticsData | null, runs = [runFixture()]) => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url.includes('/analytics/timeseries/export')) {
      return Promise.resolve({ data: 'bucket_start,run_count\n2026-07-18,1\n' });
    }
    if (url.includes('/analytics/timeseries')) {
      return Promise.resolve({ data: timeseriesFixture() });
    }
    if (url.includes('/analytics/export')) {
      return Promise.resolve({ data: 'header\nrow\n' });
    }
    if (url.includes('/analytics')) {
      return analytics
        ? Promise.resolve({ data: analytics })
        : Promise.reject(new Error('not found'));
    }
    return Promise.resolve({ data: { items: runs, total: runs.length, skip: 0, limit: 100 } });
  });
};

// The page defaults to the date-range view. The single-run tests switch to run
// mode first.
const switchToRun = () => userEvent.click(screen.getByRole('button', { name: 'Einzelner Lauf' }));

describe('RunAnalytics', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.print = vi.fn();
  });

  it('range mode (default) shows range totals and a grouped bar chart', async () => {
    mockApi(analyticsFixture());
    renderPage();

    await waitFor(() => expect(screen.getByTestId('total-run_count')).toHaveTextContent('4'));
    expect(screen.getByTestId('total-participant_count')).toHaveTextContent('8');
    expect(screen.getByTestId('total-submission_count')).toHaveTextContent('5');

    const bar = screen.getByTestId('bar-chart');
    const props = JSON.parse(bar.getAttribute('data-props')!);
    const keys = props.series.map((s: { dataKey: string }) => s.dataKey);
    expect(keys).toContain('run_count');
    expect(keys).toContain('submission_count');
    // Unlike measures stand side by side, never stacked into one bar.
    expect(props.series.some((s: { stack?: string }) => s.stack !== undefined)).toBe(false);

    // Enough x-axis height for tick labels, no animation for exports.
    expect(props.xAxis[0].height).toBeGreaterThanOrEqual(32);
    expect(props.skipAnimation).toBe(true);
  });

  // An unnamed team gets a name, the task table uses the library type names like
  // the participant app, and the funnel keeps every station label.
  it('names an unnamed team, uses the library type names and keeps every station label', async () => {
    mockApi(
      analyticsFixture({
        teams: [
          { team_id: 'team-1', team_name: null, member_count: 1, guest_count: 1, total_points: 5,
            challenges_attempted: 1, challenges_scored: 1 },
        ],
      } as never),
    );
    renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(2));

    const [funnel, points] = screen.getAllByTestId('bar-chart');
    const pointsProps = JSON.parse(points.getAttribute('data-props')!);
    expect(pointsProps.dataset.map((d: { team: string }) => d.team)).toEqual(['Team 1']);
    expect(JSON.stringify(pointsProps)).not.toContain('null');

    const funnelAxis = JSON.parse(funnel.getAttribute('data-props')!).xAxis[0];
    expect(funnelAxis.tickLabelStyle.angle).toBe(-30);
    expect(funnelAxis.height).toBeGreaterThan(32);

    const table = screen.getByRole('table', { name: /Aufgaben-Statistik/i });
    expect(within(table).getByText('Quiz')).toBeInTheDocument();
    expect(within(table).queryByText('Quizfrage')).not.toBeInTheDocument();
  });

  it('auto-selects the newest run and renders tiles + chart data from the fixture', async () => {
    mockApi(analyticsFixture());
    renderPage();
    await switchToRun();

    // Gate on the mocked charts actually mounting — a more reliable "loaded"
    // signal than the run name (which the Select can render before the
    // analytics fetch resolves).
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(2));

    expect(screen.getByText('Herbst-Rallye')).toBeInTheDocument();

    // Stat tiles (data-testid disambiguates from the always-visible
    // challenge-stats table, which shares small integers like "3"/"10").
    expect(screen.getByTestId('stat-teams')).toHaveTextContent('3');
    expect(screen.getByTestId('stat-participants')).toHaveTextContent('10');
    expect(screen.getByTestId('stat-submissions')).toHaveTextContent('5'); // 3+2
    expect(screen.getByTestId('stat-scored-percent')).toHaveTextContent('60%'); // scored 3 of 5
    expect(screen.getByText('3 von 5')).toBeInTheDocument(); // scoredCaption

    // BarChart (funnel) received both series with the fixture's two stations
    const barCharts = screen.getAllByTestId('bar-chart');
    const funnelProps = JSON.parse(barCharts[0].getAttribute('data-props')!);
    expect(funnelProps.dataset).toHaveLength(2);
    expect(funnelProps.series.map((s: { dataKey: string }) => s.dataKey)).toEqual([
      'teams_attempted',
      'teams_scored',
    ]);
    // Teams are counted: whole numbers on the scale, and the axis says what it counts.
    expect(funnelProps.yAxis).toEqual([{ label: 'Teams', min: 0, tickMinStep: 1 }]);

    // Points distribution — sorted descending by total_points
    const pointsProps = JSON.parse(barCharts[1].getAttribute('data-props')!);
    expect(pointsProps.dataset.map((d: { team: string }) => d.team)).toEqual([
      'Team Alpha',
      'Team Beta',
    ]);

    // Timeline LineChart
    const lineChart = screen.getByTestId('line-chart');
    const lineProps = JSON.parse(lineChart.getAttribute('data-props')!);
    expect(lineProps.dataset).toHaveLength(2);

    // Feedback section renders when present
    // A German screen writes the decimal with a comma.
    expect(screen.getByText('4,2 / 5', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('8,5')).toBeInTheDocument();
    expect(screen.getByText('5,0')).toBeInTheDocument();
  });

  it('configures every chart so labels, single points and exports survive', async () => {
    mockApi(analyticsFixture());
    renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(2));

    // Every chart reserves >= 32 px of x-axis height (the default 25 px makes
    // the library drop 12 px Noto Sans tick labels) and skips animation so
    // the PNG export and the print report capture the final state.
    for (const chart of screen.getAllByTestId('bar-chart')) {
      const props = JSON.parse(chart.getAttribute('data-props')!);
      expect(props.xAxis).toHaveLength(1);
      expect(props.xAxis[0].height).toBeGreaterThanOrEqual(32);
      expect(props.skipAnimation).toBe(true);
    }

    // The timeline series shows marks: a single-bucket timeline draws no
    // line, the mark is the only visible point.
    const lineProps = JSON.parse(screen.getByTestId('line-chart').getAttribute('data-props')!);
    expect(lineProps.xAxis[0].height).toBeGreaterThanOrEqual(32);
    expect(lineProps.skipAnimation).toBe(true);
    expect(lineProps.series[0].showMark).toBe(true);
  });

  it('prints the single-run report with its print header', async () => {
    mockApi(analyticsFixture());
    renderPage();
    await switchToRun();

    const printButton = await screen.findByRole('button', { name: 'Als PDF speichern' });
    expect(screen.getByText('test-org')).toBeInTheDocument();
    expect(screen.getByText(/Herbst-Rallye ·/)).toBeInTheDocument();
    expect(screen.getByText(/Erstellt am/)).toBeInTheDocument();

    await userEvent.click(printButton);

    expect(window.print).toHaveBeenCalledOnce();
  });

  it('shows the accessible data table for a chart once toggled', async () => {
    mockApi(analyticsFixture());
    renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(2));

    // The funnel's own accessible table is unmounted (Collapse unmountOnExit)
    // until toggled — station names otherwise also appear in the always-on
    // challenge-stats table below, so scope to this table specifically.
    expect(screen.queryByRole('table', { name: 'Stationen-Trichter' })).not.toBeInTheDocument();

    const toggleButtons = screen.getAllByRole('button', { name: /ein-\/ausblenden/ });
    await userEvent.click(toggleButtons[0]);

    const table = await screen.findByRole('table', { name: 'Stationen-Trichter' });
    expect(within(table).getByText('Rathaus')).toBeInTheDocument();
    expect(within(table).getByText('Marktplatz')).toBeInTheDocument();
  });

  it('renders an empty state (not NaN) when the run has no teams/submissions yet', async () => {
    mockApi(
      analyticsFixture({
        participation: { team_count: 0, participant_count: 0, guest_count: 0, registered_count: 0 },
        stations: [],
        challenges: [],
        teams: [],
        timeline: [],
        feedback: null,
      }),
    );
    renderPage();
    await switchToRun();
    await waitFor(() =>
      expect(
        screen.getByText('Für diesen Lauf liegen noch keine Stationsdaten vor.'),
      ).toBeInTheDocument(),
    );

    expect(screen.getByText('0%')).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
    expect(screen.getByText('Für diesen Lauf liegen noch keine Teampunkte vor.')).toBeInTheDocument();
    expect(screen.getByText('Für diesen Lauf liegen noch keine Einreichungen vor.')).toBeInTheDocument();
    // No feedback section when null
    expect(screen.queryByText('Feedback')).not.toBeInTheDocument();
  });

  it('shows the no-runs empty state when the org has no runs at all', async () => {
    mockApi(null, []);
    renderPage();
    await switchToRun();

    await waitFor(() =>
      expect(
        screen.getByText('Noch keine Läufe vorhanden. Erstelle zunächst einen Lauf.'),
      ).toBeInTheDocument(),
    );
  });

  it('export button fetches the CSV from the export endpoint and triggers a download', async () => {
    mockApi(analyticsFixture());

    const createObjectURL = vi.fn().mockReturnValue('blob:mock-url');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true });

    renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(2));

    await userEvent.click(screen.getByRole('button', { name: 'Teams als CSV exportieren' }));

    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenCalledWith(
        '/api/orgs/test-org/runs/run-1/analytics/export',
        { params: { table: 'teams' }, responseType: 'text' },
      ),
    );
    expect(createObjectURL).toHaveBeenCalled();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
  });

  it('JSON button downloads the loaded analytics without another request', async () => {
    mockApi(analyticsFixture());

    const createObjectURL = vi.fn().mockReturnValue('blob:mock-url');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true });

    renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(2));

    // All three export actions are present and enabled once analytics have loaded.
    expect(screen.getByRole('button', { name: 'Teams als CSV exportieren' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Aufgaben als CSV exportieren' })).toBeEnabled();
    const jsonButton = screen.getByRole('button', { name: 'JSON' });
    expect(jsonButton).toBeEnabled();

    const callCountBefore = vi.mocked(apiClient.get).mock.calls.length;

    await userEvent.click(jsonButton);

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    const parsed = JSON.parse(await blob.text());
    expect(parsed.run_id).toBe('run-1');
    expect(parsed.generated_at).toEqual(expect.any(String));
    expect(vi.mocked(apiClient.get).mock.calls.length).toBe(callCountBefore);
  });

  it('a failed reload for a newly selected run disables JSON and hides CSV, without exporting stale data', async () => {
    const runOld = runFixture({ id: 'run-1', name: 'Herbst-Rallye', created_at: '2026-01-01T00:00:00Z' });
    const runNew = runFixture({ id: 'run-2', name: 'Winter-Rallye', created_at: '2026-02-01T00:00:00Z' });

    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/runs/run-1/analytics')) {
        return Promise.resolve({ data: analyticsFixture({ run_id: 'run-1' }) });
      }
      if (url.includes('/runs/run-2/analytics')) {
        return Promise.reject(new Error('server error'));
      }
      if (url.includes('/analytics/timeseries')) {
        return Promise.resolve({ data: timeseriesFixture() });
      }
      return Promise.resolve({ data: { items: [runOld, runNew], total: 2, skip: 0, limit: 100 } });
    });

    renderPage();
    await switchToRun();

    // The newest run (Winter-Rallye) is auto-selected and its analytics fail
    // to load: the CSV buttons never render and JSON stays disabled.
    await waitFor(() => expect(screen.getByText('Winter-Rallye')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole('button', { name: 'JSON' })).toBeDisabled());
    expect(screen.queryByRole('button', { name: 'Teams als CSV exportieren' })).not.toBeInTheDocument();

    // Switching to the other run, whose analytics load successfully, must
    // enable JSON and reveal the CSV buttons for that run — not leave the
    // failed run's (nonexistent) data exportable, and not export stale data
    // from a previously-loaded run.
    await userEvent.click(screen.getByRole('combobox', { name: 'Lauf auswählen' }));
    await userEvent.click(
      within(screen.getByRole('listbox')).getByText('Herbst-Rallye'),
    );

    await waitFor(() => expect(screen.getByRole('button', { name: 'JSON' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Teams als CSV exportieren' })).toBeInTheDocument();
  });

  it('ignores an earlier run response that resolves after a newer run was requested', async () => {
    const runA = runFixture({ id: 'run-a', name: 'Run A', created_at: '2026-02-01T00:00:00Z' });
    const runB = runFixture({ id: 'run-b', name: 'Run B', created_at: '2026-01-01T00:00:00Z' });
    let resolveA!: (value: { data: RunAnalyticsData }) => void;
    let resolveB!: (value: { data: RunAnalyticsData }) => void;
    const analyticsA = new Promise<{ data: RunAnalyticsData }>((resolve) => {
      resolveA = resolve;
    });
    const analyticsB = new Promise<{ data: RunAnalyticsData }>((resolve) => {
      resolveB = resolve;
    });

    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/runs/run-a/analytics')) return analyticsA;
      if (url.includes('/runs/run-b/analytics')) return analyticsB;
      if (url.includes('/analytics/timeseries')) {
        return Promise.resolve({ data: timeseriesFixture() });
      }
      return Promise.resolve({ data: { items: [runA, runB], total: 2, skip: 0, limit: 100 } });
    });

    const createObjectURL = vi.fn().mockReturnValue('blob:mock-url');
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), writable: true });

    renderPage();
    await switchToRun();
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenCalledWith('/api/orgs/test-org/runs/run-a/analytics'),
    );

    await userEvent.click(screen.getByRole('combobox', { name: 'Lauf auswählen' }));
    await userEvent.click(within(screen.getByRole('listbox')).getByText('Run B'));
    await waitFor(() =>
      expect(vi.mocked(apiClient.get)).toHaveBeenCalledWith('/api/orgs/test-org/runs/run-b/analytics'),
    );

    await act(async () => {
      resolveA({ data: analyticsFixture({ run_id: 'run-a' }) });
      await Promise.resolve();
    });

    expect(screen.getByRole('button', { name: 'JSON' })).toBeDisabled();
    expect(createObjectURL).not.toHaveBeenCalled();

    await act(async () => {
      resolveB({ data: analyticsFixture({ run_id: 'run-b' }) });
      await Promise.resolve();
    });

    const jsonButton = await screen.findByRole('button', { name: 'JSON' });
    await waitFor(() => expect(jsonButton).toBeEnabled());
    await userEvent.click(jsonButton);

    const blob = createObjectURL.mock.calls[0][0] as Blob;
    expect(JSON.parse(await blob.text()).run_id).toBe('run-b');
  });

  it('has no axe accessibility violations once loaded', async () => {
    mockApi(analyticsFixture());
    const { container } = renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(2));
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});

// The working time of the teams and the time to the answer per task.
describe('RunAnalytics working time', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.print = vi.fn();
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
  });

  const timed = () =>
    analyticsFixture({
      started_at: '2026-01-01T09:00:00Z',
      ended_at: '2026-01-01T12:00:00Z',
      working_time: {
        teams_measured: 2,
        median_seconds: 2400,
        p25_seconds: 2100,
        p75_seconds: 2700,
        min_seconds: 1800,
        max_seconds: 3000,
      },
      teams: [
        { team_id: 'team-1', team_name: 'Team Alpha', member_count: 4, guest_count: 1, total_points: 42,
          challenges_attempted: 5, challenges_scored: 4, started_at: '2026-01-01T09:00:00Z',
          last_answer_at: '2026-01-01T09:30:00Z', duration_seconds: 1800, longest_gap_seconds: 900 },
        { team_id: 'team-2', team_name: 'Team Beta', member_count: 3, guest_count: 2, total_points: 30,
          challenges_attempted: 4, challenges_scored: 3, started_at: '2026-01-01T09:00:00Z',
          last_answer_at: '2026-01-01T09:50:00Z', duration_seconds: 3000, longest_gap_seconds: 1200 },
        { team_id: 'team-3', team_name: 'Team Gamma', member_count: 2, guest_count: 0, total_points: 0,
          challenges_attempted: 0, challenges_scored: 0, started_at: null, last_answer_at: null,
          duration_seconds: null, longest_gap_seconds: null },
      ],
      challenges: analyticsFixture().challenges.map((c, index) =>
        index === 0
          ? { ...c, time_team_count: 2, time_median_seconds: 1500, time_p25_seconds: 1350, time_p75_seconds: 1650,
              time_min_seconds: 1200, time_max_seconds: 1800 }
          : { ...c, time_team_count: 0, time_median_seconds: null, time_p25_seconds: null, time_p75_seconds: null,
              time_min_seconds: null, time_max_seconds: null },
      ),
    });

  it('shows the median working time, a chart per team and per task, and the time per task', async () => {
    mockApi(timed());
    renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(4));

    expect(screen.getByTestId('stat-working-time')).toHaveTextContent('40 Min.');
    expect(screen.getByText('2 Teams gemessen · 30 Min. bis 50 Min.')).toBeInTheDocument();
    expect(screen.getByTestId('working-time-help')).toHaveTextContent('Pausen des Laufs zählen mit');

    const props = screen.getAllByTestId('bar-chart').map((el) => JSON.parse(el.getAttribute('data-props')!));
    const perTeam = props.find((p) => p.dataset?.[0]?.team && p.layout === 'horizontal');
    // Measured teams only, the longest first, in minutes. Team Gamma has no time.
    expect(perTeam.dataset).toEqual([
      { team: 'Team Beta', minutes: 50 },
      { team: 'Team Alpha', minutes: 30 },
    ]);
    expect(perTeam.xAxis[0].label).toBe('Minuten');
    const perTask = props.find((p) => p.dataset?.[0]?.task);
    expect(perTask.dataset).toEqual([{ task: 'Rathaus · Foto-Aufgabe', minutes: 25 }]);

    // The task table names the median and the middle half, a dash without a time.
    const table = screen.getByRole('table', { name: /Aufgaben-Statistik/i });
    const firstRow = within(table).getByText('Foto-Aufgabe').closest('tr')!;
    expect(firstRow).toHaveTextContent('25 Min.');
    expect(firstRow).toHaveTextContent('23 Min. bis 28 Min.');
    const secondRow = within(table).getByText('Quiz-Aufgabe').closest('tr')!;
    expect(secondRow).toHaveTextContent('—');

    // The data table behind the team chart lists every team, also the one without a time.
    // It sits in the print copy until the reader opens it.
    const teamTable = screen.getAllByRole('table', { name: 'Bearbeitungsdauer je Team', hidden: true })[0];
    expect(within(teamTable).getByText('Team Gamma').closest('tr')).toHaveTextContent('—');
  });

  it('says "1 Team gemessen" with one value when the shortest and the longest time are equal', async () => {
    const base = timed();
    mockApi({
      ...base,
      working_time: {
        teams_measured: 1,
        median_seconds: 1800,
        p25_seconds: 1800,
        p75_seconds: 1800,
        min_seconds: 1800,
        max_seconds: 1800,
      },
    });
    renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(4));

    expect(screen.getByText('1 Team gemessen · 30 Min.')).toBeInTheDocument();
    expect(screen.queryByText(/bis 30 Min\./)).not.toBeInTheDocument();
  });

  it('exports the working time and the time per task as their own CSV tables', async () => {
    mockApi(timed());
    renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(4));

    await userEvent.click(screen.getByRole('button', { name: 'Bearbeitungsdauer als CSV exportieren' }));
    await userEvent.click(screen.getByRole('button', { name: 'Zeit je Aufgabe als CSV exportieren' }));
    expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/runs/run-1/analytics/export', {
      params: { table: 'working_time' },
      responseType: 'text',
    });
    expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/runs/run-1/analytics/export', {
      params: { table: 'task_times' },
      responseType: 'text',
    });
  });

  it('says why there is no working time yet', async () => {
    mockApi(analyticsFixture());
    renderPage();
    await switchToRun();
    await waitFor(() => expect(screen.getAllByTestId('bar-chart')).toHaveLength(2));
    expect(screen.getByTestId('stat-working-time')).toHaveTextContent('—');
    expect(screen.getByText('Noch kein Team gemessen')).toBeInTheDocument();
    expect(
      screen.getByText('Noch keine Bearbeitungsdauer. Kein Team hat geantwortet, oder der Lauf wurde nicht gestartet.'),
    ).toBeInTheDocument();
  });

  it('offers the comparison as a third view', async () => {
    mockApi(timed());
    renderPage();
    expect(screen.getByRole('button', { name: 'Vergleich' })).toBeInTheDocument();
  });
});
