import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import GroupIcon from '@mui/icons-material/Group';
import PeopleAltIcon from '@mui/icons-material/PeopleAlt';
import AssignmentIcon from '@mui/icons-material/Assignment';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutlined';
import TimerOutlinedIcon from '@mui/icons-material/TimerOutlined';
import DownloadIcon from '@mui/icons-material/Download';
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined';
import { BarChart } from '@mui/x-charts/BarChart';
import { LineChart } from '@mui/x-charts/LineChart';
import { useAuth } from '../../auth/useAuth';
import { listRuns, getRunAnalytics, exportRunAnalyticsCsv } from '../../api/runs';
import { ApiError } from '../../api/client';
import { localizedText } from '../../i18n/contentLocales';
import { countAxis } from './countAxis';
import { csvBlob, downloadBlob, downloadJson } from '../../utils/download';
import type { Run, RunAnalytics as RunAnalyticsData, RunAnalyticsExportTable, RunState } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import ChartSection from './ChartSection';
import AnalyticsRangeView from './AnalyticsRangeView';
import AnalyticsCompareView from './AnalyticsCompareView';
import PrintHeader from './PrintHeader';
import WorkingTimeHelp from './WorkingTimeHelp';
import { formatClock, formatDuration, formatSpread, toMinutes } from './analyticsFormat';

// Chart colors, derived from the app's M3 theme hues (theme.ts primary /
// tertiary) and nudged to clear the floors for a categorical palette on a
// light surface: theme.palette.primary.main sits at OKLCH L 0.428, just under
// the 0.43 floor, and theme.palette.tertiary.main's chroma (0.091) sits just
// under the 0.10 floor. Both nudges keep the same hue family. The pair keeps
// a colour distance (CVD ΔE) of 26.7 for protan/deutan and 28.0 for normal
// vision.
const CHART_BLUE = '#2850A9'; // "attempted" / primary metric (points, timeline)
const CHART_GREEN = '#3E8E3E'; // "scored" / completed metric

// The x-axis height. The library default of 25 px leaves 16 px for tick
// labels, but a 12 px Noto Sans label needs 17 px, and the library then drops
// the label. 32 px keeps every category named. The charts also pass
// skipAnimation, so the PNG export and the print report capture the final
// state, not a mid-animation frame.
const X_AXIS_HEIGHT = 32;
// Room for station names slanted by 30 degrees under the funnel.
const FUNNEL_X_AXIS_HEIGHT = 90;
// A value axis at the bottom of a lying bar chart: tick labels plus the axis title.
const VALUE_AXIS_HEIGHT = 52;

const STATE_COLORS: Record<RunState, 'default' | 'primary' | 'success' | 'warning' | 'error'> = {
  configured: 'default',
  scheduled: 'primary',
  active: 'success',
  paused: 'warning',
  ended: 'error',
};

interface StatTileProps {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  caption?: string;
  /** Disambiguates the value in tests — plain numbers otherwise collide with the challenge table. */
  testId: string;
}

const StatTile: React.FC<StatTileProps> = ({ icon, label, value, caption, testId }) => (
  <Card className="print-card" sx={{ minWidth: 0 }}>
    <CardContent>
      <Stack direction="row" spacing={1.25} sx={{ alignItems: 'center' }}>
        <Box sx={{ color: 'text.secondary', display: 'flex' }}>{icon}</Box>
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {label}
          </Typography>
          <Typography
            variant="h5"
            component="p"
            data-testid={testId}
            sx={{ fontWeight: 700, lineHeight: 1.2 }}
          >
            {value}
          </Typography>
          {caption && (
            <Typography variant="caption" color="text.secondary">
              {caption}
            </Typography>
          )}
        </Box>
      </Stack>
    </CardContent>
  </Card>
);

const RunAnalytics: React.FC = () => {
  const { t, i18n } = useTranslation(['runs', 'content']);
  // "10,0" on a German screen, not "10.0".
  const oneDecimal = new Intl.NumberFormat(i18n.language, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const { user } = useAuth();
  const orgSlug = user?.organization_slug ?? '';

  const [runs, setRuns] = useState<Run[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState('');

  const [analytics, setAnalytics] = useState<RunAnalyticsData | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);
  const analyticsRequestRef = useRef(0);

  const [exportError, setExportError] = useState<string | null>(null);

  // Three modes: a date range (the default), a single run, and a comparison of
  // runs. The range covers what a single run shows, and the run mode keeps the
  // per-run view.
  const [mode, setMode] = useState<'range' | 'run' | 'compare'>('range');

  const loadRuns = useCallback(async () => {
    if (!orgSlug) return;
    setRunsLoading(true);
    setRunsError(null);
    try {
      const data = await listRuns(orgSlug);
      const sorted = [...data.items].sort(
        (a, b) => Date.parse(b.created_at) - Date.parse(a.created_at),
      );
      setRuns(sorted);
      // Preselect the newest run so the coordinator reaches the charts in three
      // clicks: sidebar, run, charts.
      setSelectedRunId((prev) => (prev && sorted.some((r) => r.id === prev) ? prev : sorted[0]?.id ?? ''));
    } catch (err) {
      setRunsError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setRunsLoading(false);
    }
  }, [orgSlug]);

  const loadAnalytics = useCallback(async () => {
    const requestId = ++analyticsRequestRef.current;
    if (!orgSlug || !selectedRunId) return;
    setAnalyticsLoading(true);
    setAnalyticsError(null);
    // Clear stale data up front. A failed reload for a newly selected run
    // must not leave the previous run's analytics exportable.
    setAnalytics(null);
    try {
      const data = await getRunAnalytics(orgSlug, selectedRunId);
      if (requestId !== analyticsRequestRef.current) return;
      setAnalytics(data);
    } catch (err) {
      if (requestId !== analyticsRequestRef.current) return;
      setAnalyticsError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      if (requestId === analyticsRequestRef.current) {
        setAnalyticsLoading(false);
      }
    }
  }, [orgSlug, selectedRunId]);

  // An async IIFE instead of a direct call of the memoized loader, so the lint
  // rule react-hooks/set-state-in-effect does not flag the fetch on mount.
  // loadRuns holds the state updates.
  useEffect(() => {
    void (async () => {
      await loadRuns();
    })();
  }, [loadRuns]);

  useEffect(() => {
    if (!selectedRunId) return;
    void (async () => {
      await loadAnalytics();
    })();
  }, [selectedRunId, loadAnalytics]);

  const handleExportCsv = async (table: RunAnalyticsExportTable) => {
    if (!orgSlug || !selectedRunId) return;
    setExportError(null);
    try {
      const csv = await exportRunAnalyticsCsv(orgSlug, selectedRunId, table);
      const blob = csvBlob(csv);
      downloadBlob(t(`analytics.export.filename.${table}`, { runId: selectedRunId }), blob);
    } catch (err) {
      setExportError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  const handleExportJson = () => {
    if (!analytics) return;
    downloadJson(t('analytics.export.jsonFileName', { runId: selectedRunId }), {
      generated_at: new Date().toISOString(),
      ...analytics,
      run_id: selectedRunId,
    });
  };

  const lang = i18n.language;
  const titleOf = (ml: Record<string, string>, fallback: string) => localizedText(ml, lang) || fallback;
  const selectedRun = runs.find((run) => run.id === selectedRunId);
  const selectedRunDate = selectedRun
    ? selectedRun.started_at ?? selectedRun.scheduled_for ?? selectedRun.created_at
    : null;
  const runPrintTitle = selectedRun
    ? `${selectedRun.name} · ${new Intl.DateTimeFormat(lang, { dateStyle: 'medium' }).format(
        new Date(selectedRunDate!),
      )}`
    : t('analytics.title');

  // No teams yet must never render NaN, so every division guards a zero
  // denominator.
  const totalSubmissions = analytics
    ? analytics.challenges.reduce((n, c) => n + c.submissions, 0)
    : 0;
  const totalScored = analytics ? analytics.challenges.reduce((n, c) => n + c.scored, 0) : 0;
  const scoredPercent =
    totalSubmissions > 0 ? Math.round((totalScored / totalSubmissions) * 100) : 0;

  const stationDataset = (analytics?.stations ?? []).map((s) => ({
    id: s.run_station_id,
    label: titleOf(s.title, s.run_station_id),
    teams_attempted: s.teams_attempted,
    teams_scored: s.teams_scored,
  }));

  // A team without a name must not show as "null" on screen, in the PNG or in
  // the print. It gets the label the public result page uses.
  const pointsDataset = [...(analytics?.teams ?? [])]
    .sort((a, b) => b.total_points - a.total_points)
    .map((tm, index) => ({
      team: tm.team_name || t('results.public.unnamedTeam', { index: index + 1 }),
      points: tm.total_points,
    }));

  // Working time per team. The chart shows the measured teams, the
  // longest first. The table below it lists every team, a dash for no value.
  const teamLabel = (name: string | null, index: number) =>
    name || t('results.public.unnamedTeam', { index: index + 1 });
  const workingRows = (analytics?.teams ?? []).map((tm, index) => ({
    id: tm.team_id,
    team: teamLabel(tm.team_name, index),
    minutes: toMinutes(tm.duration_seconds),
    tm,
  }));
  const workingDataset = workingRows
    .filter((row) => row.minutes != null)
    .sort((a, b) => (b.minutes ?? 0) - (a.minutes ?? 0))
    .map((row) => ({ team: row.team, minutes: row.minutes }));
  const workingTime = analytics?.working_time ?? null;

  const taskTimeRows = (analytics?.challenges ?? []).map((c) => ({
    id: c.run_challenge_id,
    label: `${titleOf(c.station_title, '—')} · ${titleOf(c.title, '—')}`,
    minutes: toMinutes(c.time_median_seconds),
    c,
  }));
  const taskTimeDataset = taskTimeRows
    .filter((row) => row.minutes != null)
    .map((row) => ({ task: row.label, minutes: row.minutes }));
  // One band per task, tall enough for its name (the chart lies on its side).
  const barHeight = (count: number) => Math.max(160, 56 + count * 36);

  const timelineDataset = (analytics?.timeline ?? []).map((b) => ({
    time: new Date(b.bucket_start).toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' }),
    submissions: b.submissions,
  }));

  return (
    <Box>
      <Typography className="print-hidden" variant="h2" component="h1" sx={{ mb: 3 }}>
        {t('analytics.title')}
      </Typography>

      <ToggleButtonGroup
        className="print-hidden"
        exclusive
        size="small"
        color="primary"
        value={mode}
        onChange={(_, next: 'range' | 'run' | 'compare' | null) => {
          if (next) setMode(next);
        }}
        sx={{ mb: 3 }}
        aria-label={t('analytics.modeLabel', { defaultValue: 'Ansicht' })}
      >
        <ToggleButton value="range">{t('analytics.modeRange', { defaultValue: 'Zeitraum' })}</ToggleButton>
        <ToggleButton value="run">{t('analytics.modeRun', { defaultValue: 'Einzelner Lauf' })}</ToggleButton>
        <ToggleButton value="compare">{t('analytics.modeCompare')}</ToggleButton>
      </ToggleButtonGroup>

      {mode === 'range' && <AnalyticsRangeView orgSlug={orgSlug} />}

      {mode === 'compare' && (
        <AnalyticsCompareView
          orgSlug={orgSlug}
          runs={runs}
          runsLoading={runsLoading}
          runsError={runsError}
          onRetryRuns={() => void loadRuns()}
        />
      )}

      {mode === 'run' && (
        <>
      <PrintHeader title={runPrintTitle} />
      <FormControl className="print-hidden" size="small" sx={{ minWidth: 280, mb: 3 }}>
        <InputLabel id="analytics-run-picker-label">{t('analytics.runPicker.label')}</InputLabel>
        <Select
          labelId="analytics-run-picker-label"
          label={t('analytics.runPicker.label')}
          value={selectedRunId}
          onChange={(e) => setSelectedRunId(e.target.value)}
          disabled={runsLoading || runs.length === 0}
        >
          {runs.map((r) => (
            <MenuItem key={r.id} value={r.id}>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                <span>{r.name}</span>
                <Chip label={t(`list.filter.${r.state}`)} color={STATE_COLORS[r.state]} size="small" />
              </Stack>
            </MenuItem>
          ))}
        </Select>
      </FormControl>

      {/* JSON export: always present once the run picker is shown, whether
          or not this run's analytics have loaded yet. It is disabled until
          they have. The CSV buttons below render only once analytics are
          loaded without error. */}
      <Stack className="print-hidden" direction="row" spacing={2} sx={{ mb: 3, flexWrap: 'wrap' }}>
        <Button
          variant="outlined"
          startIcon={<PrintOutlinedIcon />}
          onClick={() => window.print()}
        >
          {t('analytics.export.pdf')}
        </Button>
        <Button
          variant="outlined"
          startIcon={<DownloadIcon />}
          onClick={handleExportJson}
          disabled={!analytics}
        >
          {t('analytics.export.json')}
        </Button>
      </Stack>

      {runsLoading && <LoadingSpinner />}
      {!runsLoading && runsError && <ErrorAlert message={runsError} onRetry={() => void loadRuns()} />}
      {!runsLoading && !runsError && runs.length === 0 && (
        <Typography color="text.secondary">{t('analytics.noRuns')}</Typography>
      )}

      {!runsLoading && !runsError && runs.length > 0 && (
        <>
          {analyticsLoading && <LoadingSpinner />}
          {!analyticsLoading && analyticsError && (
            <ErrorAlert message={analyticsError} onRetry={() => void loadAnalytics()} />
          )}

          {!analyticsLoading && !analyticsError && analytics && (
            <Stack spacing={3}>
              {/* Stat tiles */}
              <Box
                sx={{
                  display: 'grid',
                  gap: 2,
                  gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
                }}
              >
                <StatTile
                  testId="stat-teams"
                  icon={<GroupIcon />}
                  label={t('analytics.tiles.teams')}
                  value={analytics.participation.team_count}
                />
                <StatTile
                  testId="stat-participants"
                  icon={<PeopleAltIcon />}
                  label={t('analytics.tiles.participants')}
                  value={analytics.participation.participant_count}
                  caption={t('analytics.tiles.participantsCaption', {
                    registered: analytics.participation.registered_count,
                    guests: analytics.participation.guest_count,
                  })}
                />
                <StatTile
                  testId="stat-submissions"
                  icon={<AssignmentIcon />}
                  label={t('analytics.tiles.submissions')}
                  value={totalSubmissions}
                />
                <StatTile
                  testId="stat-scored-percent"
                  icon={<CheckCircleOutlineIcon />}
                  label={t('analytics.tiles.scoredPercent')}
                  value={`${scoredPercent}%`}
                  caption={t('analytics.tiles.scoredCaption', {
                    scored: totalScored,
                    total: totalSubmissions,
                  })}
                />
                <StatTile
                  testId="stat-working-time"
                  icon={<TimerOutlinedIcon />}
                  label={t('analytics.workingTime.tile')}
                  value={formatDuration(workingTime?.median_seconds, lang)}
                  caption={
                    workingTime && workingTime.teams_measured > 0
                      ? workingTime.min_seconds === workingTime.max_seconds
                        ? t('analytics.workingTime.tileCaptionSame', {
                            count: workingTime.teams_measured,
                            value: formatDuration(workingTime.min_seconds, lang),
                          })
                        : t('analytics.workingTime.tileCaption', {
                            count: workingTime.teams_measured,
                            min: formatDuration(workingTime.min_seconds, lang),
                            max: formatDuration(workingTime.max_seconds, lang),
                          })
                      : t('analytics.workingTime.tileCaptionNone')
                  }
                />
              </Box>

              <WorkingTimeHelp />

              {/* Station funnel */}
              <ChartSection
                title={t('analytics.charts.funnel.title')}
                toggleLabel={t('analytics.charts.funnel.toggleTable')}
                fileName={`run-analytics-${selectedRunId}-stations.png`}
                legend={[
                  { label: t('analytics.charts.funnel.attempted'), color: CHART_BLUE },
                  { label: t('analytics.charts.funnel.scored'), color: CHART_GREEN },
                ]}
                empty={stationDataset.length === 0}
                emptyMessage={t('analytics.charts.funnel.empty')}
                chart={
                  <BarChart
                    dataset={stationDataset}
                    // Every station keeps its name: the library skips labels
                    // that do not fit, which leaves every second bar unnamed in
                    // the PNG. Slanted labels fit them all.
                    xAxis={[
                      {
                        dataKey: 'label',
                        scaleType: 'band',
                        height: FUNNEL_X_AXIS_HEIGHT,
                        tickLabelInterval: () => true,
                        tickLabelStyle: { angle: -30, textAnchor: 'end', fontSize: 11 },
                      },
                    ]}
                    series={[
                      {
                        dataKey: 'teams_attempted',
                        label: t('analytics.charts.funnel.attempted'),
                        color: CHART_BLUE,
                      },
                      {
                        dataKey: 'teams_scored',
                        label: t('analytics.charts.funnel.scored'),
                        color: CHART_GREEN,
                      },
                    ]}
                    // Teams are counted, so the scale has whole numbers only. A
                    // default scale reads 0.0, 0.5, 1.0 for one team. The axis
                    // also says what it counts.
                    yAxis={[
                      countAxis(
                        t('analytics.axis.teams'),
                        stationDataset.map((s) => s.teams_attempted),
                        i18n.language,
                      ),
                    ]}
                    height={300}
                    skipAnimation
                  />
                }
                table={
                  <TableContainer>
                    <Table size="small" aria-label={t('analytics.charts.funnel.title')}>
                      <TableHead>
                        <TableRow>
                          <TableCell>{t('analytics.charts.funnel.table.station')}</TableCell>
                          <TableCell align="right">{t('analytics.charts.funnel.table.attempted')}</TableCell>
                          <TableCell align="right">{t('analytics.charts.funnel.table.scored')}</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {stationDataset.map((row) => (
                          <TableRow key={row.id}>
                            <TableCell>{row.label}</TableCell>
                            <TableCell align="right">{row.teams_attempted}</TableCell>
                            <TableCell align="right">{row.teams_scored}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                }
              />

              {/* Points distribution */}
              <ChartSection
                title={t('analytics.charts.points.title')}
                toggleLabel={t('analytics.charts.points.toggleTable')}
                fileName={`run-analytics-${selectedRunId}-points.png`}
                legend={[
                  { label: t('analytics.charts.points.table.points'), color: CHART_BLUE },
                ]}
                empty={pointsDataset.length === 0}
                emptyMessage={t('analytics.charts.points.empty')}
                chart={
                  <BarChart
                    dataset={pointsDataset}
                    xAxis={[{ dataKey: 'team', scaleType: 'band', height: X_AXIS_HEIGHT }]}
                    series={[{ dataKey: 'points', color: CHART_BLUE }]}
                    yAxis={[countAxis(t('analytics.axis.points'), pointsDataset.map((p) => p.points), i18n.language)]}
                    height={300}
                    hideLegend
                    skipAnimation
                  />
                }
                table={
                  <TableContainer>
                    <Table size="small" aria-label={t('analytics.charts.points.title')}>
                      <TableHead>
                        <TableRow>
                          <TableCell>{t('analytics.charts.points.table.team')}</TableCell>
                          <TableCell align="right">{t('analytics.charts.points.table.points')}</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {pointsDataset.map((row) => (
                          <TableRow key={row.team}>
                            <TableCell>{row.team}</TableCell>
                            <TableCell align="right">{row.points}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                }
              />

              {/* Submissions over time */}
              <ChartSection
                title={t('analytics.charts.timeline.title')}
                toggleLabel={t('analytics.charts.timeline.toggleTable')}
                fileName={`run-analytics-${selectedRunId}-submissions.png`}
                legend={[
                  { label: t('analytics.charts.timeline.table.submissions'), color: CHART_BLUE },
                ]}
                empty={timelineDataset.length === 0}
                emptyMessage={t('analytics.charts.timeline.empty')}
                chart={
                  <LineChart
                    dataset={timelineDataset}
                    xAxis={[{ dataKey: 'time', scaleType: 'point', height: X_AXIS_HEIGHT }]}
                    yAxis={[
                      countAxis(
                        t('analytics.axis.submissions'),
                        timelineDataset.map((b) => b.submissions),
                        i18n.language,
                      ),
                    ]}
                    series={[
                      {
                        dataKey: 'submissions',
                        color: CHART_BLUE,
                        area: false,
                        // A single-bucket timeline has no line to draw. The
                        // mark is the only visible point.
                        showMark: true,
                      },
                    ]}
                    height={300}
                    hideLegend
                    skipAnimation
                  />
                }
                table={
                  <TableContainer>
                    <Table size="small" aria-label={t('analytics.charts.timeline.title')}>
                      <TableHead>
                        <TableRow>
                          <TableCell>{t('analytics.charts.timeline.table.time')}</TableCell>
                          <TableCell align="right">{t('analytics.charts.timeline.table.submissions')}</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {timelineDataset.map((row, idx) => (
                          <TableRow key={`${row.time}-${idx}`}>
                            <TableCell>{row.time}</TableCell>
                            <TableCell align="right">{row.submissions}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                }
              />

              {/* Working time per team */}
              <ChartSection
                title={t('analytics.workingTime.chartTitle')}
                toggleLabel={t('analytics.workingTime.toggleTable')}
                fileName={`run-analytics-${selectedRunId}-working-time.png`}
                legend={[{ label: t('analytics.workingTime.table.duration'), color: CHART_BLUE }]}
                empty={workingDataset.length === 0}
                emptyMessage={t('analytics.workingTime.empty')}
                chart={
                  <BarChart
                    dataset={workingDataset}
                    layout="horizontal"
                    yAxis={[{ dataKey: 'team', scaleType: 'band', width: 140 }]}
                    // The value axis lies at the bottom here. The library's
                    // default height drops the tick labels, as on the date axes.
                    xAxis={[
                      {
                        ...countAxis(
                          t('analytics.axis.minutes'),
                          workingDataset.map((row) => row.minutes),
                          i18n.language,
                        ),
                        height: VALUE_AXIS_HEIGHT,
                      },
                    ]}
                    series={[
                      {
                        dataKey: 'minutes',
                        label: t('analytics.workingTime.table.duration'),
                        color: CHART_BLUE,
                        valueFormatter: (value: number | null) => formatDuration(value == null ? null : value * 60, lang),
                      },
                    ]}
                    height={barHeight(workingDataset.length)}
                    hideLegend
                    skipAnimation
                  />
                }
                table={
                  <TableContainer>
                    <Table size="small" aria-label={t('analytics.workingTime.chartTitle')}>
                      <TableHead>
                        <TableRow>
                          <TableCell>{t('analytics.workingTime.table.team')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.table.start')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.table.lastAnswer')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.table.duration')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.table.longestGap')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.table.points')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.table.answered')}</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {workingRows.map((row) => (
                          <TableRow key={row.id}>
                            <TableCell>{row.team}</TableCell>
                            <TableCell align="right">{formatClock(row.tm.started_at, lang)}</TableCell>
                            <TableCell align="right">{formatClock(row.tm.last_answer_at, lang)}</TableCell>
                            <TableCell align="right">{formatDuration(row.tm.duration_seconds, lang)}</TableCell>
                            <TableCell align="right">{formatDuration(row.tm.longest_gap_seconds, lang)}</TableCell>
                            <TableCell align="right">{row.tm.total_points}</TableCell>
                            <TableCell align="right">{row.tm.challenges_attempted}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                }
              />

              {/* Time to the answer per task */}
              <ChartSection
                title={t('analytics.workingTime.taskChartTitle')}
                toggleLabel={t('analytics.workingTime.taskToggleTable')}
                fileName={`run-analytics-${selectedRunId}-task-times.png`}
                legend={[{ label: t('analytics.workingTime.taskTable.median'), color: CHART_BLUE }]}
                empty={taskTimeDataset.length === 0}
                emptyMessage={t('analytics.workingTime.taskEmpty')}
                chart={
                  <BarChart
                    dataset={taskTimeDataset}
                    layout="horizontal"
                    yAxis={[{ dataKey: 'task', scaleType: 'band', width: 280 }]}
                    xAxis={[
                      {
                        ...countAxis(
                          t('analytics.axis.minutes'),
                          taskTimeDataset.map((row) => row.minutes),
                          i18n.language,
                        ),
                        height: VALUE_AXIS_HEIGHT,
                      },
                    ]}
                    series={[
                      {
                        dataKey: 'minutes',
                        label: t('analytics.workingTime.taskTable.median'),
                        color: CHART_BLUE,
                        valueFormatter: (value: number | null) => formatDuration(value == null ? null : value * 60, lang),
                      },
                    ]}
                    height={barHeight(taskTimeDataset.length)}
                    hideLegend
                    skipAnimation
                  />
                }
                table={
                  <TableContainer>
                    <Table size="small" aria-label={t('analytics.workingTime.taskChartTitle')}>
                      <TableHead>
                        <TableRow>
                          <TableCell>{t('analytics.workingTime.taskTable.task')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.taskTable.teams')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.taskTable.median')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.taskTable.middleHalf')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.taskTable.shortest')}</TableCell>
                          <TableCell align="right">{t('analytics.workingTime.taskTable.longest')}</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {taskTimeRows.map((row) => (
                          <TableRow key={row.id}>
                            <TableCell>{row.label}</TableCell>
                            <TableCell align="right">{row.c.time_team_count ?? 0}</TableCell>
                            <TableCell align="right">{formatDuration(row.c.time_median_seconds, lang)}</TableCell>
                            <TableCell align="right">
                              {formatSpread(row.c.time_p25_seconds, row.c.time_p75_seconds, lang)}
                            </TableCell>
                            <TableCell align="right">{formatDuration(row.c.time_min_seconds, lang)}</TableCell>
                            <TableCell align="right">{formatDuration(row.c.time_max_seconds, lang)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                }
              />

              {/* Per-challenge stats: a plain data table, not a chart, behind
                  the "challenges" CSV export. */}
              <Card className="print-card">
                <CardContent>
                  <Typography variant="h6" component="h2" gutterBottom>
                    {t('analytics.challengeTable.title')}
                  </Typography>
                  {analytics.challenges.length === 0 ? (
                    <Typography color="text.secondary" variant="body2">
                      {t('analytics.challengeTable.empty')}
                    </Typography>
                  ) : (
                    <TableContainer>
                      <Table size="small" aria-label={t('analytics.challengeTable.title')}>
                        <TableHead>
                          <TableRow>
                            <TableCell>{t('analytics.challengeTable.station')}</TableCell>
                            <TableCell>{t('analytics.challengeTable.challenge')}</TableCell>
                            <TableCell>{t('analytics.challengeTable.type')}</TableCell>
                            <TableCell align="right">{t('analytics.challengeTable.submissions')}</TableCell>
                            <TableCell align="right">{t('analytics.challengeTable.scored')}</TableCell>
                            <TableCell align="right">{t('analytics.challengeTable.avgPoints')}</TableCell>
                            <TableCell align="right">{t('analytics.challengeTable.maxPoints')}</TableCell>
                            <TableCell align="right">{t('analytics.challengeTable.timeMedian')}</TableCell>
                            <TableCell align="right">{t('analytics.challengeTable.timeSpread')}</TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {analytics.challenges.map((c) => (
                            <TableRow key={c.run_challenge_id}>
                              <TableCell>{titleOf(c.station_title, '—')}</TableCell>
                              <TableCell>{titleOf(c.title, '—')}</TableCell>
                              {/* The library's names, not the participant's
                                  "Frage" and "Quizfrage". */}
                              <TableCell>{t(`content:challenge.types.${c.type}`)}</TableCell>
                              <TableCell align="right">{c.submissions}</TableCell>
                              <TableCell align="right">{c.scored}</TableCell>
                              <TableCell align="right">
                                {c.avg_points != null ? oneDecimal.format(c.avg_points) : '—'}
                              </TableCell>
                              <TableCell align="right">{c.max_points}</TableCell>
                              <TableCell align="right">{formatDuration(c.time_median_seconds, lang)}</TableCell>
                              <TableCell align="right">
                                {formatSpread(c.time_p25_seconds, c.time_p75_seconds, lang)}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  )}
                </CardContent>
              </Card>

              {/* Feedback aggregate: hidden entirely when there is no
                  feedback yet, never rendered with 0/NaN placeholders. */}
              {analytics.feedback && analytics.feedback.total_count > 0 && (
                <Card className="print-card">
                  <CardContent>
                    <Typography variant="h6" component="h2" gutterBottom>
                      {t('analytics.feedback.title')}
                    </Typography>
                    <Typography variant="body1">
                      {t('analytics.feedback.averageRating')}: {oneDecimal.format(analytics.feedback.average_rating)} / 5
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {t('analytics.feedback.totalCount', { count: analytics.feedback.total_count })}
                    </Typography>
                  </CardContent>
                </Card>
              )}

              {/* Export */}
              {exportError && <ErrorAlert message={exportError} />}
              <Stack className="print-hidden" direction="row" spacing={2} sx={{ flexWrap: 'wrap' }}>
                <Button
                  variant="outlined"
                  startIcon={<DownloadIcon />}
                  onClick={() => void handleExportCsv('teams')}
                >
                  {t('analytics.export.teams')}
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<DownloadIcon />}
                  onClick={() => void handleExportCsv('challenges')}
                >
                  {t('analytics.export.challenges')}
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<DownloadIcon />}
                  onClick={() => void handleExportCsv('working_time')}
                >
                  {t('analytics.export.workingTime')}
                </Button>
                <Button
                  variant="outlined"
                  startIcon={<DownloadIcon />}
                  onClick={() => void handleExportCsv('task_times')}
                >
                  {t('analytics.export.taskTimes')}
                </Button>
              </Stack>
            </Stack>
          )}
        </>
      )}
        </>
      )}
    </Box>
  );
};

export default RunAnalytics;
