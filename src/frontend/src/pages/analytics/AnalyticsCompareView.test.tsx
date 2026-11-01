import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'vitest-axe';
import AnalyticsCompareView from './AnalyticsCompareView';
import apiClient from '../../api/client';
import { AuthContext } from '../../auth/AuthContext';
import type { AnalyticsComparison, Run } from '../../api/types';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

// jsdom cannot lay out the chart SVG. The stub exposes the props instead.
vi.mock('@mui/x-charts/BarChart', () => ({
  BarChart: (props: Record<string, unknown>) => (
    <div data-testid="bar-chart" data-props={JSON.stringify(props)} />
  ),
}));

const run = (id: string, name: string, startedAt: string | null, createdAt: string): Run =>
  ({
    id,
    organization_id: 'org-1',
    rallye_id: 'r1',
    name,
    state: startedAt ? 'ended' : 'configured',
    join_code: 'JOIN42',
    scheduled_for: null,
    started_at: startedAt,
    paused_at: null,
    ended_at: null,
    settings: {},
    max_teams: null,
    content_languages: ['de'],
    created_at: createdAt,
    updated_at: createdAt,
  }) as unknown as Run;

// Newest first, as the page sorts them. The newest run never started.
const RUNS = [
  run('run-new', 'Entwurf', null, '2026-04-01T00:00:00Z'),
  run('run-b', 'Lauf B', '2026-03-17T10:00:00Z', '2026-03-01T00:00:00Z'),
  run('run-a', 'Lauf A', '2026-03-10T10:00:00Z', '2026-02-01T00:00:00Z'),
];

const working = (median: number | null, measured: number) => ({
  teams_measured: measured,
  median_seconds: median,
  p25_seconds: median,
  p75_seconds: median,
  min_seconds: median == null ? null : median - 600,
  max_seconds: median == null ? null : median + 600,
});

const comparison = (): AnalyticsComparison => ({
  runs: [
    {
      run_id: 'run-b',
      name: 'Lauf B',
      state: 'ended',
      run_date: '2026-03-17T10:00:00Z',
      started_at: '2026-03-17T10:00:00Z',
      ended_at: null,
      station_count: 2,
      task_count: 2,
      team_count: 1,
      participant_count: 1,
      guest_count: 1,
      registered_count: 0,
      submission_count: 2,
      scored_count: 1,
      completion_percent: 100,
      teams_finished: 1,
      total_points: 10,
      achievable_points: 30,
      avg_points_per_team: 10,
      median_points_per_team: 10,
      points_percent: 33.3,
      working_time: working(2400, 1),
    },
    {
      run_id: 'run-a',
      name: 'Lauf A',
      state: 'ended',
      run_date: '2026-03-10T10:00:00Z',
      started_at: '2026-03-10T10:00:00Z',
      ended_at: null,
      station_count: 3,
      task_count: 3,
      team_count: 3,
      participant_count: 7,
      guest_count: 3,
      registered_count: 4,
      submission_count: 3,
      scored_count: 3,
      completion_percent: 33.3,
      teams_finished: 1,
      total_points: 32,
      achievable_points: 35,
      avg_points_per_team: 10.67,
      median_points_per_team: 4,
      points_percent: 30.5,
      working_time: working(5700, 2),
    },
  ],
  stations: [
    {
      key: 's1',
      title: { de: 'Rathaus' },
      cells: {
        'run-b': { team_count: 1, teams_attempted: 1, percent: 100 },
        'run-a': { team_count: 3, teams_attempted: 2, percent: 66.7 },
      },
    },
    // Only run A holds the Dom.
    { key: 's3', title: { de: 'Dom' }, cells: { 'run-a': { team_count: 3, teams_attempted: 1, percent: 33.3 } } },
  ],
  tasks: [
    {
      key: 's1/t1',
      station_key: 's1',
      station_title: { de: 'Rathaus' },
      title: { de: 'Baujahr' },
      type: 'text',
      cells: {
        'run-b': {
          team_count: 1,
          submissions: 1,
          percent: 100,
          scored: 1,
          avg_points: 10,
          max_points: 10,
          time_team_count: 1,
          time_median_seconds: 600,
        },
        'run-a': {
          team_count: 3,
          submissions: 2,
          percent: 66.7,
          scored: 2,
          avg_points: 6,
          max_points: 10,
          time_team_count: 2,
          time_median_seconds: 1500,
        },
      },
    },
  ],
});

const mockApi = () => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url.includes('/analytics/compare/export')) return Promise.resolve({ data: 'Lauf;Datum\n' });
    if (url.includes('/analytics/compare')) return Promise.resolve({ data: comparison() });
    return Promise.reject(new Error(`unexpected url ${url}`));
  });
};

const renderView = (runs: Run[] = RUNS) =>
  render(
    <AuthContext.Provider
      value={{
        user: {
          id: 'u1',
          email: 'coord@example.com',
          display_name: 'Coordinator',
          role: 'coordinator',
          organization_id: 'org-1',
          organization_slug: 'test-org',
        },
        isAuthenticated: true,
        isLoading: false,
        login: vi.fn(),
        logout: vi.fn(),
        checkAuth: vi.fn(),
        needsTwoFactorSetup: () => false,
      }}
    >
      <AnalyticsCompareView orgSlug="test-org" runs={runs} runsLoading={false} runsError={null} onRetryRuns={vi.fn()} />
    </AuthContext.Provider>,
  );

const plain = (text: string | null) => (text ?? '').replace(/\s/g, ' ');

describe('AnalyticsCompareView', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.print = vi.fn();
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
  });

  it('starts with the two newest runs that took place and asks for both', async () => {
    mockApi();
    renderView();
    await screen.findByTestId('compare-summary');
    expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/analytics/compare', {
      params: { run_ids: ['run-b', 'run-a'] },
    });
  });

  it('shows every measure per run in German formats, with the working time', async () => {
    mockApi();
    renderView();
    const table = await screen.findByTestId('compare-summary');
    const rowOf = (label: string) => plain(within(table).getByRole('rowheader', { name: label }).closest('tr')!.textContent);
    expect(rowOf('Ø Punkte je Team')).toContain('1010,7');
    expect(rowOf('Beantwortet')).toContain('100 %33,3 %');
    expect(rowOf('Bearbeitungsdauer (Median)')).toContain('40 Min.1 Std. 35 Min.');
    expect(rowOf('Teilnehmende')).toContain('4 registriert · 3 Gäste');
    // The definition stands next to the numbers.
    expect(screen.getByTestId('working-time-help')).toHaveTextContent('vom Start des Laufs bis zur letzten Antwort');
  });

  it('marks a station a run does not hold and says why', async () => {
    mockApi();
    renderView();
    await screen.findByTestId('compare-summary');
    expect(
      screen.getByText('Die Läufe haben nicht dieselben Inhalte.', { exact: false }),
    ).toBeInTheDocument();
    // The station chart has one series per run, each named, and leaves the Dom empty for run B.
    const stationChart = screen
      .getAllByTestId('bar-chart')
      .map((el) => JSON.parse(el.getAttribute('data-props')!))
      .find((props) => props.dataset?.[0]?.station === 'Rathaus');
    expect(stationChart.series.map((s: { label: string }) => s.label)).toEqual(['Lauf B', 'Lauf A']);
    expect(stationChart.dataset[1]).toEqual({ station: 'Dom', 'run-b': null, 'run-a': 33.3 });
    // The task table puts answered, points and time into one cell per run.
    const tasks = screen.getByTestId('compare-tasks');
    expect(plain(tasks.textContent)).toContain('2 von 3 TeamsØ 6 PunkteMedian 25 Min.');
  });

  it('exports a comparison table as CSV for the chosen runs', async () => {
    mockApi();
    renderView();
    await screen.findByTestId('compare-summary');
    await userEvent.click(screen.getByRole('button', { name: 'Stationen als CSV exportieren' }));
    await waitFor(() =>
      expect(apiClient.get).toHaveBeenCalledWith('/api/orgs/test-org/analytics/compare/export', {
        params: { run_ids: ['run-b', 'run-a'], table: 'stations' },
        responseType: 'text',
      }),
    );
  });

  it('asks for a second run when only one is chosen', async () => {
    mockApi();
    renderView();
    await screen.findByTestId('compare-summary');
    const chip = screen.getAllByRole('button', { name: /Lauf A/ })[0];
    await userEvent.click(within(chip).getByTestId('CancelIcon'));
    expect(await screen.findByText('Wähle mindestens zwei Läufe, um sie zu vergleichen.')).toBeInTheDocument();
    expect(screen.queryByTestId('compare-summary')).not.toBeInTheDocument();
  });

  it('says that a comparison needs two runs when the organization has one', () => {
    mockApi();
    renderView([RUNS[1]]);
    expect(screen.getByText('Für einen Vergleich braucht es mindestens zwei Läufe.')).toBeInTheDocument();
    expect(apiClient.get).not.toHaveBeenCalled();
  });

  // The axe rule scrollable-region-focusable: a table box that scrolls takes the focus,
  // is a region and carries the name of its table.
  it('gives every table box the focus, the role region and the name of its table', async () => {
    mockApi();
    const { container } = renderView();
    await screen.findByTestId('compare-summary');
    const tables = Array.from(container.querySelectorAll('table'));
    expect(tables.length).toBeGreaterThanOrEqual(5);
    for (const table of tables) {
      const box = table.parentElement!;
      expect(box).toHaveClass('MuiTableContainer-root');
      expect(box).toHaveAttribute('tabindex', '0');
      expect(box).toHaveAttribute('role', 'region');
      expect(box.getAttribute('aria-label')).toBeTruthy();
      expect(box.getAttribute('aria-label')).toBe(table.getAttribute('aria-label'));
    }
  });

  it('has no axe violations', async () => {
    mockApi();
    const { container } = renderView();
    await screen.findByTestId('compare-summary');
    expect(await axe(container)).toHaveNoViolations();
  });
});
