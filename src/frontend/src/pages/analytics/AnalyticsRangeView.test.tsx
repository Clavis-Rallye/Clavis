import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AnalyticsRangeView from './AnalyticsRangeView';
import apiClient from '../../api/client';
import type { AnalyticsTimeseries } from '../../api/types';
import { AuthContext } from '../../auth/AuthContext';
import i18n from '../../i18n';

vi.mock('../../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/client')>();
  return {
    ...actual,
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  };
});

// @mui/x-charts renders real SVG geometry that jsdom can't lay out — mocked
// down to a stub exposing its props, same approach as RunAnalytics.test.tsx.
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

const timeseriesFixture = (): AnalyticsTimeseries => ({
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
  ],
});

// The rows the screen shows: the rows that carry the print-only rule are hidden on screen.
// jsdom applies the @media print override to the computed style, so the rows are told
// apart by the marker the component sets, not by their role.
const screenRows = (table: HTMLElement) =>
  Array.from(table.querySelectorAll('tbody tr:not([data-print-only])'));

const mockApi = (data: AnalyticsTimeseries | null) => {
  vi.mocked(apiClient.get).mockImplementation((url: string) => {
    if (url.includes('/analytics/timeseries/export')) {
      return Promise.resolve({ data: 'bucket_start,run_count\n2026-07-18,1\n' });
    }
    if (url.includes('/analytics/timeseries')) {
      return data ? Promise.resolve({ data }) : Promise.reject(new Error('failed'));
    }
    return Promise.reject(new Error(`unexpected url ${url}`));
  });
};

const renderView = () =>
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
      <AnalyticsRangeView orgSlug="test-org" />
    </AuthContext.Provider>,
  );

describe('AnalyticsRangeView', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.print = vi.fn();
  });

  it('JSON button downloads the loaded timeseries without another request', async () => {
    mockApi(timeseriesFixture());

    const createObjectURL = vi.fn().mockReturnValue('blob:mock-url');
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, writable: true });
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, writable: true });

    renderView();

    await waitFor(() => expect(screen.getByTestId('bar-chart')).toBeInTheDocument());

    expect(screen.getByRole('button', { name: 'CSV exportieren' })).toBeEnabled();
    const jsonButton = screen.getByRole('button', { name: 'JSON exportieren' });
    expect(jsonButton).toBeEnabled();

    const callCountBefore = vi.mocked(apiClient.get).mock.calls.length;

    await userEvent.click(jsonButton);

    expect(createObjectURL).toHaveBeenCalledTimes(1);
    const blob = createObjectURL.mock.calls[0][0] as Blob;
    const parsed = JSON.parse(await blob.text());
    expect(parsed.generated_at).toEqual(expect.any(String));
    expect(parsed.date_from).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(parsed.date_to).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(parsed.bucket).toBe('day');
    expect(parsed.range).toEqual(timeseriesFixture().range);
    expect(parsed.totals).toEqual(timeseriesFixture().totals);
    expect(vi.mocked(apiClient.get).mock.calls.length).toBe(callCountBefore);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url');
  });

  // The day reads 18.07.2026 on a German screen, not 2026-07-18, and the scale
  // counts in whole numbers.
  it('labels the days in the interface format and counts in whole numbers', async () => {
    mockApi(timeseriesFixture());
    renderView();
    const chart = await screen.findByTestId('bar-chart');
    const props = JSON.parse(chart.getAttribute('data-props')!);
    expect(props.dataset[0].label).toBe('18.07.2026');
    expect(props.yAxis).toEqual([{ label: 'Anzahl', min: 0, tickMinStep: 1 }]);
  });

  it('reserves enough x-axis height for tick labels and skips animation for exports', async () => {
    mockApi(timeseriesFixture());
    renderView();

    const bar = await screen.findByTestId('bar-chart');
    const props = JSON.parse(bar.getAttribute('data-props')!);

    // >= 32 px so the library does not drop the 12 px Noto Sans date labels;
    // no animation so the PNG export and the print report capture the final
    // state.
    expect(props.xAxis).toHaveLength(1);
    expect(props.xAxis[0].height).toBeGreaterThanOrEqual(32);
    expect(props.skipAnimation).toBe(true);
  });

  it('disables the JSON button while no data is loaded or the load failed', async () => {
    mockApi(null);
    renderView();

    await waitFor(() => expect(screen.getByRole('button', { name: 'JSON exportieren' })).toBeDisabled());
  });

  it('prints the date-range report with its print header', async () => {
    mockApi(timeseriesFixture());
    renderView();

    const printButton = screen.getByRole('button', { name: 'Als PDF speichern' });
    expect(screen.getByText('test-org')).toBeInTheDocument();
    expect(screen.getByText(/Zeitraum:/)).toBeInTheDocument();
    expect(screen.getByText(/Erstellt am/)).toBeInTheDocument();

    await userEvent.click(printButton);

    expect(window.print).toHaveBeenCalledOnce();
  });

  // The print header's title is a paragraph, so the view carries no h1 of its
  // own; the page's single on-screen h1 lives in RunAnalytics above it.
  it('renders no h1: the print title is not a second level-1 heading', async () => {
    mockApi(timeseriesFixture());
    const { container } = renderView();

    await waitFor(() => expect(screen.getByTestId('bar-chart')).toBeInTheDocument());
    expect(container.querySelector('h1')).toBeNull();
  });

  it('still renders when the start date is cleared', async () => {
    mockApi(timeseriesFixture());
    renderView();
    const fromInput = screen.getByLabelText('Von');
    await userEvent.clear(fromInput);
    expect(screen.getByText(/Zeitraum:/)).toBeInTheDocument();
  });
});

// Points per team and per task, and the working time, over a freely chosen
// period.
describe('AnalyticsRangeView points and working time', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.print = vi.fn();
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
  });

  const bucket = (day: string, values: Record<string, number | null>) => ({
    bucket_start: `2026-07-${day}T00:00:00`,
    run_count: 0,
    participant_count: 0,
    guest_count: 0,
    registered_count: 0,
    team_count: 0,
    submission_count: 0,
    scored_count: 0,
    unscored_count: 0,
    total_points: 0,
    avg_points_per_team: null,
    avg_points_per_answer: null,
    points_percent: null,
    teams_measured: 0,
    median_duration_seconds: null,
    ...values,
  });

  const team = (index: number, points: number) => ({
    run_id: 'run-a',
    run_name: 'Lauf A',
    run_date: '2026-07-18T10:00:00Z',
    team_id: `team-${index}`,
    team_name: `Team ${index}`,
    points,
    answers: 2,
    scored: 2,
    duration_seconds: index === 1 ? 2700 : null,
  });

  const withPoints = (): AnalyticsTimeseries => ({
    ...timeseriesFixture(),
    totals: {
      ...timeseriesFixture().totals,
      total_points: 64,
      avg_points_per_team: 10.666,
      avg_points_per_answer: 8,
      points_percent: 80,
      teams_measured: 5,
      median_duration_seconds: 2700,
    },
    buckets: [
      bucket('18', { run_count: 1, team_count: 2, total_points: 20, avg_points_per_team: 10, avg_points_per_answer: 10,
        points_percent: 100, teams_measured: 2, median_duration_seconds: 2400 }),
      bucket('19', {}),
      bucket('20', { run_count: 1, team_count: 4, total_points: 44, avg_points_per_team: 11, avg_points_per_answer: 7.33,
        points_percent: 73.3, teams_measured: 3, median_duration_seconds: 3000 }),
    ],
    tasks: [
      {
        key: 'lib-1',
        station_title: { de: 'Rathaus' },
        title: { de: 'Baujahr' },
        type: 'text',
        run_count: 2,
        submissions: 5,
        scored: 4,
        avg_points: 7.5,
        max_points: 10,
        time_team_count: 5,
        time_median_seconds: 1200,
        time_p25_seconds: 900,
        time_p75_seconds: 1500,
      },
    ],
    teams: Array.from({ length: 12 }, (_, i) => team(i + 1, 30 - i)),
  });

  it('shows the average points per team and the median working time of the period', async () => {
    mockApi(withPoints());
    renderView();
    expect(await screen.findByTestId('total-avg-points')).toHaveTextContent('10,7');
    // Intl puts a no-break space before the percent sign.
    expect(screen.getByText(/^80\s%\sder möglichen Punkte$/)).toBeInTheDocument();
    expect(screen.getByTestId('total-working-time')).toHaveTextContent('45 Min.');
  });

  it('draws the course over time with a gap for a day without a team', async () => {
    mockApi(withPoints());
    renderView();
    const lines = await screen.findAllByTestId('line-chart');
    expect(lines).toHaveLength(2);
    const [points, duration] = lines.map((el) => JSON.parse(el.getAttribute('data-props')!));
    expect(points.dataset.map((d: { perTeam: number | null }) => d.perTeam)).toEqual([10, null, 11]);
    expect(points.yAxis[0].label).toBe('Punkte je Team');
    expect(duration.dataset.map((d: { minutes: number | null }) => d.minutes)).toEqual([40, null, 50]);
    expect(duration.yAxis[0].label).toBe('Minuten');
    expect(points.skipAnimation).toBe(true);
  });

  it('lists the tasks and the teams of the period, the first ten until asked', async () => {
    mockApi(withPoints());
    renderView();
    const tasks = await screen.findByRole('table', { name: 'Aufgaben im Zeitraum' });
    const row = within(tasks).getByText('Baujahr').closest('tr')!;
    expect(row).toHaveTextContent('7,5');
    expect(row).toHaveTextContent('20 Min.');
    expect(row).toHaveTextContent('15 Min. bis 25 Min.');

    const teams = screen.getByRole('table', { name: 'Teams im Zeitraum' });
    expect(screenRows(teams)).toHaveLength(10);
    expect(within(teams).getByText('Team 1').closest('tr')).toHaveTextContent('45 Min.');
    await userEvent.click(screen.getByRole('button', { name: 'Alle 12 Teams anzeigen' }));
    expect(screenRows(teams)).toHaveLength(12);
    expect(within(teams).getAllByRole('row')).toHaveLength(1 + 12);
  });

  // The axe rule scrollable-region-focusable: a box that scrolls sideways at
  // 390 px must take the keyboard, be a region and carry the name of its table.
  it('gives every table box the focus, the role region and the name of its table', async () => {
    mockApi(withPoints());
    const { container } = renderView();
    await screen.findByRole('table', { name: 'Teams im Zeitraum' });
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

  it('exports the points, the tasks and the teams as their own CSV tables', async () => {
    mockApi(withPoints());
    renderView();
    await screen.findByRole('table', { name: 'Teams im Zeitraum' });
    await userEvent.click(screen.getByRole('button', { name: 'Teams als CSV exportieren' }));
    await userEvent.click(screen.getByRole('button', { name: 'Punkte als CSV exportieren' }));
    const exportCalls = vi
      .mocked(apiClient.get)
      .mock.calls.filter(([url]) => String(url).includes('/timeseries/export'))
      .map(([, config]) => (config as { params: { table?: string } }).params.table);
    expect(exportCalls).toEqual(['teams', 'points']);
  });

  it('keeps the old metrics CSV without a table parameter', async () => {
    mockApi(withPoints());
    renderView();
    await screen.findByTestId('bar-chart');
    await userEvent.click(screen.getByRole('button', { name: 'CSV exportieren' }));
    const call = vi.mocked(apiClient.get).mock.calls.find(([url]) => String(url).includes('/timeseries/export'))!;
    expect((call[1] as { params: Record<string, string> }).params).not.toHaveProperty('table');
  });
});

// The print names its grouping and holds every team. jsdom does not print and
// applies no @media print rule, so these tests read the head line and the
// markup. The print itself is proved by a real print to PDF.
describe('AnalyticsRangeView print', () => {
  const FROM = '2026-10-19';
  const TO = '2026-11-22';

  const teamRow = (index: number) => ({
    run_id: 'run-a',
    run_name: 'Lauf A',
    run_date: '2026-10-20T10:00:00Z',
    team_id: `team-${index}`,
    team_name: `Team ${index}`,
    points: 40 - index,
    answers: 2,
    scored: 2,
    duration_seconds: null,
  });

  const fourteenTeams = (): AnalyticsTimeseries => ({
    ...timeseriesFixture(),
    tasks: [],
    teams: Array.from({ length: 14 }, (_, i) => teamRow(i + 1)),
  });

  beforeEach(() => {
    vi.resetAllMocks();
    window.print = vi.fn();
  });

  afterEach(async () => {
    await i18n.changeLanguage('de');
  });

  const setRange = async (from = 'Von', to = 'Bis') => {
    await screen.findByTestId('bar-chart');
    fireEvent.change(screen.getByLabelText(from), { target: { value: FROM } });
    fireEvent.change(screen.getByLabelText(to), { target: { value: TO } });
    await screen.findByTestId('bar-chart');
  };

  const pickGrouping = async (name: string) => {
    await userEvent.click(screen.getByRole('combobox', { name: /Gruppierung/ }));
    await userEvent.click(screen.getByRole('option', { name }));
  };

  it.each([
    ['Tag', 'je Tag'],
    ['Woche', 'je Woche'],
    ['Monat', 'je Monat'],
  ])('names the range and the grouping %s in the print head line', async (option, per) => {
    mockApi(timeseriesFixture());
    renderView();
    await setRange();
    await pickGrouping(option);
    await screen.findByTestId('bar-chart');
    expect(screen.getByText(`Zeitraum: 19.10.2026 bis 22.11.2026, ${per}`)).toBeInTheDocument();
  });

  it('names the range and the grouping in English', async () => {
    await i18n.changeLanguage('en');
    mockApi(timeseriesFixture());
    renderView();
    await setRange('From', 'To');
    await userEvent.click(screen.getByRole('combobox', { name: /Group by/ }));
    await userEvent.click(screen.getByRole('option', { name: 'Week' }));
    await screen.findByTestId('bar-chart');
    expect(screen.getByText('Date range: Oct 19, 2026 to Nov 22, 2026, per week')).toBeInTheDocument();
  });

  it('keeps every team row in the DOM, the rows beyond the preview print-only', async () => {
    mockApi(fourteenTeams());
    const { container } = renderView();
    await screen.findByRole('table', { name: 'Teams im Zeitraum' });

    // The screen: 10 rows and the button.
    const screenTeams = screen.getByRole('table', { name: 'Teams im Zeitraum' });
    expect(screenRows(screenTeams)).toHaveLength(10);
    expect(screen.getByRole('button', { name: 'Alle 14 Teams anzeigen' })).toBeInTheDocument();

    // The DOM: all 14, rows 11 to 14 carry the print-only rule, ranks go on from 11.
    const body = container.querySelector('table[aria-label="Teams im Zeitraum"] tbody')!;
    const rows = Array.from(body.querySelectorAll('tr'));
    expect(rows).toHaveLength(14);
    expect(rows.filter((r) => r.hasAttribute('data-print-only'))).toEqual(rows.slice(10));
    expect(rows.slice(0, 10).some((r) => r.hasAttribute('data-print-only'))).toBe(false);
    expect(rows.map((r) => r.querySelector('td')!.textContent)).toEqual(
      Array.from({ length: 14 }, (_, i) => String(i + 1)),
    );
    expect(rows[13]).toHaveTextContent('Team 14');

    // The rule itself: hidden on screen, a table row in print.
    const cls = Array.from(rows[10].classList).find((c) => c.startsWith('css-'))!;
    const rules = Array.from(document.styleSheets).flatMap((sheet) => Array.from(sheet.cssRules));
    const screenRule = rules.find(
      (r): r is CSSStyleRule => r instanceof CSSStyleRule && r.selectorText === `.${cls}`,
    );
    expect(screenRule?.style.display).toBe('none');
    const printRule = rules.find(
      (r): r is CSSMediaRule =>
        r instanceof CSSMediaRule && r.media.mediaText === 'print' && r.cssText.includes(`.${cls}`),
    );
    expect(printRule?.cssText).toContain('display: table-row');
  });

  it('shows all 14 rows on screen once the preview is open', async () => {
    mockApi(fourteenTeams());
    const { container } = renderView();
    await screen.findByRole('table', { name: 'Teams im Zeitraum' });
    await userEvent.click(screen.getByRole('button', { name: 'Alle 14 Teams anzeigen' }));
    const teams = screen.getByRole('table', { name: 'Teams im Zeitraum' });
    expect(within(teams).getAllByRole('row')).toHaveLength(1 + 14);
    expect(container.querySelectorAll('table[aria-label="Teams im Zeitraum"] tbody tr[data-print-only]')).toHaveLength(0);
  });
});
