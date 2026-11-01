import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined';
import { BarChart } from '@mui/x-charts/BarChart';
import { getAnalyticsComparison, exportAnalyticsComparisonCsv } from '../../api/runs';
import { ApiError } from '../../api/client';
import { localizedText } from '../../i18n/contentLocales';
import { formatDate } from '../../utils/formatDate';
import { csvBlob, downloadBlob, downloadJson } from '../../utils/download';
import type {
  AnalyticsCompareExportTable,
  AnalyticsCompareRun,
  AnalyticsComparison,
  Run,
} from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import ChartSection from './ChartSection';
import PrintHeader from './PrintHeader';
import WorkingTimeHelp from './WorkingTimeHelp';
import { countAxis } from './countAxis';
import {
  NO_VALUE,
  RUN_COLORS,
  assignSlots,
  formatDuration,
  formatNumber,
  formatPercent,
  toMinutes,
} from './analyticsFormat';

/**
 * A table box that scrolls sideways on a narrow screen. A scrolling box has to take the keyboard
 * (axe: scrollable-region-focusable), and a focusable box needs a role and a name: the name of the
 * table inside. The global :focus-visible ring is drawn inside the box so the clip of the card
 * cannot cut it.
 */
const ScrollRegion: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <TableContainer
    tabIndex={0}
    role="region"
    aria-label={label}
    sx={{ '&:focus-visible': { outlineOffset: -2 } }}
  >
    {children}
  </TableContainer>
);

const MAX_RUNS = 6;
// The app's blue and green, the pair the run view validated (RunAnalytics.tsx).
const TEAMS_COLOR = '#2850A9';
const PARTICIPANTS_COLOR = '#3E8E3E';
// Run names under the bars: slanted, so six names fit.
const X_AXIS_HEIGHT = 70;

interface AnalyticsCompareViewProps {
  orgSlug: string;
  runs: Run[];
  runsLoading: boolean;
  runsError: string | null;
  onRetryRuns: () => void;
}

const runDateOf = (run: Run) => run.started_at ?? run.scheduled_for ?? run.created_at;

/**
 * Two to six runs side by side: participation, points, working time and the
 * answers per station and task. Team level only.
 */
const AnalyticsCompareView: React.FC<AnalyticsCompareViewProps> = ({
  orgSlug,
  runs,
  runsLoading,
  runsError,
  onRetryRuns,
}) => {
  const { t, i18n } = useTranslation(['runs', 'content']);
  const lang = i18n.language;

  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [slots, setSlots] = useState<Record<string, number>>({});
  const touched = useRef(false);

  const [data, setData] = useState<AnalyticsComparison | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const requestRef = useRef(0);

  const select = useCallback((ids: string[]) => {
    setSelectedIds(ids);
    setSlots((previous) => assignSlots(previous, ids));
  }, []);

  // Start with the two newest runs that took place, so the view shows a
  // comparison at once. The reader's own choice is never replaced.
  useEffect(() => {
    if (touched.current || selectedIds.length > 0 || runs.length < 2) return;
    const started = runs.filter((run) => run.started_at);
    const pick = (started.length >= 2 ? started : runs).slice(0, 2).map((run) => run.id);
    select(pick);
  }, [runs, selectedIds.length, select]);

  const load = useCallback(async () => {
    const requestId = ++requestRef.current;
    if (!orgSlug || selectedIds.length < 2) {
      setData(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await getAnalyticsComparison(orgSlug, selectedIds);
      if (requestId === requestRef.current) setData(result);
    } catch (err) {
      if (requestId !== requestRef.current) return;
      setData(null);
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [orgSlug, selectedIds]);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load]);

  const handleCsv = async (table: AnalyticsCompareExportTable) => {
    setExportError(null);
    try {
      const csv = await exportAnalyticsComparisonCsv(orgSlug, selectedIds, table);
      downloadBlob(t(`analytics.compare.filename.${table}`), csvBlob(csv));
    } catch (err) {
      setExportError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  const handleJson = () => {
    if (!data) return;
    downloadJson(t('analytics.compare.jsonFileName'), { generated_at: new Date().toISOString(), ...data });
  };

  const selectedRuns = selectedIds
    .map((id) => runs.find((run) => run.id === id))
    .filter((run): run is Run => Boolean(run));
  const colorOf = (runId: string) => RUN_COLORS[(slots[runId] ?? 0) % RUN_COLORS.length];
  const compared = data?.runs ?? [];
  const runLabel = (run: AnalyticsCompareRun) =>
    run.run_date ? `${run.name} (${formatDate(run.run_date, lang)})` : run.name;
  const stateLabel = (state: string) => t(`list.filter.${state}`, { defaultValue: state });
  const titleOf = (ml: Record<string, string>) => localizedText(ml, lang) || NO_VALUE;
  const printTitle = t('analytics.compare.printTitle', {
    names: compared.length ? compared.map((run) => run.name).join(', ') : selectedRuns.map((r) => r.name).join(', '),
  });

  // One bar per run on the axis, so one hue each chart. The axis label
  // carries the identity, not a colour.
  const runDataset = compared.map((run) => ({
    run: run.name,
    teams: run.team_count,
    participants: run.participant_count,
    perTeam: run.avg_points_per_team,
    minutes: toMinutes(run.working_time.median_seconds),
  }));
  const anyMeasured = compared.some((run) => run.working_time.teams_measured > 0);
  // Stations on the axis and one bar per run: the runs are the series here,
  // each in its own colour, named in the legend and in the table.
  const stations = data?.stations ?? [];
  const stationDataset = stations.map((station) => {
    const row: Record<string, string | number | null> = { station: titleOf(station.title) };
    compared.forEach((run) => {
      row[run.run_id] = station.cells[run.run_id]?.percent ?? null;
    });
    return row;
  });
  const tasks = data?.tasks ?? [];
  const differentContent =
    stations.some((station) => compared.some((run) => !station.cells[run.run_id])) ||
    tasks.some((task) => compared.some((run) => !task.cells[run.run_id]));

  const summaryRows: { key: string; label: string; value: (run: AnalyticsCompareRun) => React.ReactNode }[] = [
    { key: 'date', label: t('analytics.compare.summary.date'), value: (r) => (r.run_date ? formatDate(r.run_date, lang) : NO_VALUE) },
    { key: 'state', label: t('analytics.compare.summary.state'), value: (r) => stateLabel(r.state) },
    { key: 'stations', label: t('analytics.compare.summary.stations'), value: (r) => r.station_count },
    { key: 'tasks', label: t('analytics.compare.summary.tasks'), value: (r) => r.task_count },
    { key: 'teams', label: t('analytics.compare.summary.teams'), value: (r) => r.team_count },
    {
      key: 'participants',
      label: t('analytics.compare.summary.participants'),
      value: (r) => (
        <>
          {r.participant_count}
          <Typography variant="caption" color="text.secondary" component="span" sx={{ display: 'block' }}>
            {t('analytics.compare.summary.participantsDetail', {
              registered: r.registered_count,
              guests: r.guest_count,
            })}
          </Typography>
        </>
      ),
    },
    { key: 'answers', label: t('analytics.compare.summary.answers'), value: (r) => r.submission_count },
    { key: 'completion', label: t('analytics.compare.summary.completion'), value: (r) => formatPercent(r.completion_percent, lang) },
    { key: 'finished', label: t('analytics.compare.summary.teamsFinished'), value: (r) => r.teams_finished },
    { key: 'avgPoints', label: t('analytics.compare.summary.avgPoints'), value: (r) => formatNumber(r.avg_points_per_team, lang) },
    { key: 'medianPoints', label: t('analytics.compare.summary.medianPoints'), value: (r) => formatNumber(r.median_points_per_team, lang) },
    { key: 'achievable', label: t('analytics.compare.summary.achievable'), value: (r) => r.achievable_points },
    { key: 'pointsPercent', label: t('analytics.compare.summary.pointsPercent'), value: (r) => formatPercent(r.points_percent, lang) },
    {
      key: 'duration',
      label: t('analytics.compare.summary.durationMedian'),
      value: (r) => formatDuration(r.working_time.median_seconds, lang),
    },
    {
      key: 'durationRange',
      label: t('analytics.compare.summary.durationRange'),
      value: (r) =>
        r.working_time.teams_measured > 0
          ? `${formatDuration(r.working_time.min_seconds, lang)} ${lang.startsWith('de') ? 'bis' : 'to'} ${formatDuration(r.working_time.max_seconds, lang)}`
          : NO_VALUE,
    },
    { key: 'measured', label: t('analytics.compare.summary.teamsMeasured'), value: (r) => r.working_time.teams_measured },
  ];

  const runHeaderCell = (run: AnalyticsCompareRun) => (
    <TableCell key={run.run_id} align="right">
      <Stack direction="row" spacing={0.75} sx={{ alignItems: 'center', justifyContent: 'flex-end' }}>
        <Box
          aria-hidden
          sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: colorOf(run.run_id), flexShrink: 0 }}
        />
        <span>{run.name}</span>
      </Stack>
    </TableCell>
  );

  if (runsLoading) return <LoadingSpinner />;
  if (runsError) return <ErrorAlert message={runsError} onRetry={onRetryRuns} />;
  if (runs.length < 2) {
    return <Typography color="text.secondary">{t('analytics.compare.notEnoughRuns')}</Typography>;
  }

  return (
    <Box>
      <PrintHeader title={printTitle} />
      <Card className="print-hidden" sx={{ mb: 2 }}>
        <CardContent>
          <Autocomplete
            multiple
            options={runs}
            value={selectedRuns}
            onChange={(_, value) => {
              touched.current = true;
              select(value.slice(0, MAX_RUNS).map((run) => run.id));
            }}
            getOptionLabel={(run) => `${run.name} · ${formatDate(runDateOf(run), lang)}`}
            getOptionDisabled={(run) => selectedIds.length >= MAX_RUNS && !selectedIds.includes(run.id)}
            isOptionEqualToValue={(option, value) => option.id === value.id}
            filterSelectedOptions
            renderValue={(value, getItemProps) =>
              value.map((run, index) => {
                const { key, ...itemProps } = getItemProps({ index });
                return (
                  <Chip
                    key={key}
                    {...itemProps}
                    size="small"
                    label={run.name}
                    icon={
                      <Box
                        aria-hidden
                        sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: colorOf(run.id), ml: '6px !important' }}
                      />
                    }
                  />
                );
              })
            }
            renderInput={(params) => (
              <TextField
                {...params}
                label={t('analytics.compare.pickerLabel')}
                helperText={t('analytics.compare.pickerHelp')}
              />
            )}
          />
          <Stack direction="row" spacing={1} sx={{ mt: 2, flexWrap: 'wrap', gap: 1 }}>
            <Button variant="outlined" startIcon={<PrintOutlinedIcon />} onClick={() => window.print()} disabled={!data}>
              {t('analytics.export.pdf')}
            </Button>
            <Button variant="outlined" startIcon={<DownloadIcon />} onClick={handleJson} disabled={!data}>
              {t('analytics.export.json')}
            </Button>
            {(['runs', 'stations', 'tasks'] as AnalyticsCompareExportTable[]).map((table) => (
              <Button
                key={table}
                variant="outlined"
                startIcon={<DownloadIcon />}
                onClick={() => void handleCsv(table)}
                disabled={!data}
              >
                {t(`analytics.compare.export${table.charAt(0).toUpperCase()}${table.slice(1)}`)}
              </Button>
            ))}
          </Stack>
          {exportError && (
            <Box sx={{ mt: 2 }}>
              <ErrorAlert message={exportError} />
            </Box>
          )}
        </CardContent>
      </Card>

      {selectedIds.length < 2 && <Typography color="text.secondary">{t('analytics.compare.tooFew')}</Typography>}
      {loading && <LoadingSpinner />}
      {!loading && error && <ErrorAlert message={error} onRetry={() => void load()} />}

      {!loading && !error && data && selectedIds.length >= 2 && (
        <Stack spacing={3}>
          {/* Every measure of every run, in one table: the base of the report. */}
          <Card className="print-card">
            <CardContent>
              <Typography variant="h6" component="h2" gutterBottom>
                {t('analytics.compare.summary.title')}
              </Typography>
              <ScrollRegion label={t('analytics.compare.summary.title')}>
                <Table size="small" aria-label={t('analytics.compare.summary.title')} data-testid="compare-summary">
                  <TableHead>
                    <TableRow>
                      <TableCell>{t('analytics.compare.summary.measure')}</TableCell>
                      {compared.map(runHeaderCell)}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {summaryRows.map((row) => (
                      <TableRow key={row.key}>
                        <TableCell component="th" scope="row">
                          {row.label}
                        </TableCell>
                        {compared.map((run) => (
                          <TableCell key={run.run_id} align="right">
                            {row.value(run)}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollRegion>
            </CardContent>
          </Card>

          <WorkingTimeHelp />

          <ChartSection
            title={t('analytics.compare.participation.title')}
            toggleLabel={t('analytics.compare.participation.toggleTable')}
            fileName="analytics-compare-participation.png"
            legend={[
              { label: t('analytics.compare.participation.teams'), color: TEAMS_COLOR },
              { label: t('analytics.compare.participation.participants'), color: PARTICIPANTS_COLOR },
            ]}
            chart={
              <BarChart
                dataset={runDataset}
                xAxis={[
                  {
                    dataKey: 'run',
                    scaleType: 'band',
                    height: X_AXIS_HEIGHT,
                    tickLabelInterval: () => true,
                    tickLabelStyle: { angle: -20, textAnchor: 'end', fontSize: 11 },
                  },
                ]}
                yAxis={[
                  countAxis(
                    t('analytics.axis.count'),
                    runDataset.flatMap((row) => [row.teams, row.participants]),
                    lang,
                  ),
                ]}
                series={[
                  { dataKey: 'teams', label: t('analytics.compare.participation.teams'), color: TEAMS_COLOR },
                  {
                    dataKey: 'participants',
                    label: t('analytics.compare.participation.participants'),
                    color: PARTICIPANTS_COLOR,
                  },
                ]}
                height={320}
                skipAnimation
              />
            }
            table={
              <ScrollRegion label={t('analytics.compare.participation.title')}>
                <Table size="small" aria-label={t('analytics.compare.participation.title')}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{t('analytics.compare.participation.run')}</TableCell>
                      <TableCell align="right">{t('analytics.compare.participation.teams')}</TableCell>
                      <TableCell align="right">{t('analytics.compare.participation.participants')}</TableCell>
                      <TableCell align="right">{t('analytics.compare.participation.answers')}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {compared.map((run) => (
                      <TableRow key={run.run_id}>
                        <TableCell>{runLabel(run)}</TableCell>
                        <TableCell align="right">{run.team_count}</TableCell>
                        <TableCell align="right">{run.participant_count}</TableCell>
                        <TableCell align="right">{run.submission_count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollRegion>
            }
          />

          <ChartSection
            title={t('analytics.compare.points.title')}
            toggleLabel={t('analytics.compare.points.toggleTable')}
            fileName="analytics-compare-points.png"
            legend={[{ label: t('analytics.compare.points.title'), color: TEAMS_COLOR }]}
            chart={
              <BarChart
                dataset={runDataset}
                xAxis={[
                  {
                    dataKey: 'run',
                    scaleType: 'band',
                    height: X_AXIS_HEIGHT,
                    tickLabelInterval: () => true,
                    tickLabelStyle: { angle: -20, textAnchor: 'end', fontSize: 11 },
                  },
                ]}
                yAxis={[countAxis(t('analytics.axis.pointsPerTeam'), runDataset.map((row) => row.perTeam), lang)]}
                series={[
                  {
                    dataKey: 'perTeam',
                    label: t('analytics.compare.points.title'),
                    color: TEAMS_COLOR,
                    valueFormatter: (value: number | null) => formatNumber(value, lang),
                  },
                ]}
                height={320}
                hideLegend
                skipAnimation
              />
            }
            table={
              <ScrollRegion label={t('analytics.compare.points.title')}>
                <Table size="small" aria-label={t('analytics.compare.points.title')}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{t('analytics.compare.participation.run')}</TableCell>
                      <TableCell align="right">{t('analytics.compare.summary.avgPoints')}</TableCell>
                      <TableCell align="right">{t('analytics.compare.summary.medianPoints')}</TableCell>
                      <TableCell align="right">{t('analytics.compare.summary.achievable')}</TableCell>
                      <TableCell align="right">{t('analytics.compare.summary.pointsPercent')}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {compared.map((run) => (
                      <TableRow key={run.run_id}>
                        <TableCell>{runLabel(run)}</TableCell>
                        <TableCell align="right">{formatNumber(run.avg_points_per_team, lang)}</TableCell>
                        <TableCell align="right">{formatNumber(run.median_points_per_team, lang)}</TableCell>
                        <TableCell align="right">{run.achievable_points}</TableCell>
                        <TableCell align="right">{formatPercent(run.points_percent, lang)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollRegion>
            }
          />

          <ChartSection
            title={t('analytics.compare.duration.title')}
            toggleLabel={t('analytics.compare.duration.toggleTable')}
            fileName="analytics-compare-working-time.png"
            legend={[{ label: t('analytics.compare.duration.title'), color: PARTICIPANTS_COLOR }]}
            empty={!anyMeasured}
            emptyMessage={t('analytics.compare.duration.empty')}
            chart={
              <BarChart
                dataset={runDataset}
                xAxis={[
                  {
                    dataKey: 'run',
                    scaleType: 'band',
                    height: X_AXIS_HEIGHT,
                    tickLabelInterval: () => true,
                    tickLabelStyle: { angle: -20, textAnchor: 'end', fontSize: 11 },
                  },
                ]}
                yAxis={[countAxis(t('analytics.axis.minutes'), runDataset.map((row) => row.minutes), lang)]}
                series={[
                  {
                    dataKey: 'minutes',
                    label: t('analytics.compare.duration.title'),
                    color: PARTICIPANTS_COLOR,
                    valueFormatter: (value: number | null) => formatDuration(value == null ? null : value * 60, lang),
                  },
                ]}
                height={320}
                hideLegend
                skipAnimation
              />
            }
            table={
              <ScrollRegion label={t('analytics.compare.duration.title')}>
                <Table size="small" aria-label={t('analytics.compare.duration.title')}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{t('analytics.compare.participation.run')}</TableCell>
                      <TableCell align="right">{t('analytics.compare.summary.teamsMeasured')}</TableCell>
                      <TableCell align="right">{t('analytics.workingTime.taskTable.median')}</TableCell>
                      <TableCell align="right">{t('analytics.workingTime.taskTable.shortest')}</TableCell>
                      <TableCell align="right">{t('analytics.workingTime.taskTable.longest')}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {compared.map((run) => (
                      <TableRow key={run.run_id}>
                        <TableCell>{runLabel(run)}</TableCell>
                        <TableCell align="right">{run.working_time.teams_measured}</TableCell>
                        <TableCell align="right">{formatDuration(run.working_time.median_seconds, lang)}</TableCell>
                        <TableCell align="right">{formatDuration(run.working_time.min_seconds, lang)}</TableCell>
                        <TableCell align="right">{formatDuration(run.working_time.max_seconds, lang)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollRegion>
            }
          />

          {differentContent && (
            <Alert severity="info" variant="outlined" className="print-card">
              {t('analytics.compare.differentContent')}
            </Alert>
          )}

          <ChartSection
            title={t('analytics.compare.stations.title')}
            toggleLabel={t('analytics.compare.stations.toggleTable')}
            fileName="analytics-compare-stations.png"
            legend={compared.map((run) => ({ label: run.name, color: colorOf(run.run_id) }))}
            empty={stations.length === 0}
            emptyMessage={t('analytics.compare.stations.empty')}
            chart={
              <BarChart
                dataset={stationDataset}
                xAxis={[
                  {
                    dataKey: 'station',
                    scaleType: 'band',
                    height: 90,
                    tickLabelInterval: () => true,
                    tickLabelStyle: { angle: -30, textAnchor: 'end', fontSize: 11 },
                  },
                ]}
                yAxis={[{ label: t('analytics.axis.percent'), min: 0, max: 100, valueFormatter: (v: number | null) => formatPercent(v, lang) }]}
                series={compared.map((run) => ({
                  dataKey: run.run_id,
                  label: run.name,
                  color: colorOf(run.run_id),
                  valueFormatter: (value: number | null) => formatPercent(value, lang),
                }))}
                height={340}
                skipAnimation
              />
            }
            table={
              <ScrollRegion label={t('analytics.compare.stations.title')}>
                <Table size="small" aria-label={t('analytics.compare.stations.title')}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{t('analytics.charts.funnel.table.station')}</TableCell>
                      {compared.map(runHeaderCell)}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {stations.map((station) => (
                      <TableRow key={station.key}>
                        <TableCell>{titleOf(station.title)}</TableCell>
                        {compared.map((run) => {
                          const cell = station.cells[run.run_id];
                          return (
                            <TableCell key={run.run_id} align="right">
                              {cell
                                ? `${t('analytics.compare.stations.cell', {
                                    attempted: cell.teams_attempted,
                                    teams: cell.team_count,
                                  })} (${formatPercent(cell.percent, lang)})`
                                : t('analytics.compare.notInRun')}
                            </TableCell>
                          );
                        })}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollRegion>
            }
          />

          {/* Per task and run: answered, points and time, in one cell. */}
          <Card className="print-card">
            <CardContent>
              <Typography variant="h6" component="h2" gutterBottom>
                {t('analytics.compare.tasks.title')}
              </Typography>
              {tasks.length === 0 ? (
                <Typography color="text.secondary" variant="body2">
                  {t('analytics.compare.tasks.empty')}
                </Typography>
              ) : (
                <ScrollRegion label={t('analytics.compare.tasks.title')}>
                  <Table size="small" aria-label={t('analytics.compare.tasks.title')} data-testid="compare-tasks">
                    <TableHead>
                      <TableRow>
                        <TableCell>{t('analytics.compare.tasks.task')}</TableCell>
                        {compared.map(runHeaderCell)}
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {tasks.map((task) => (
                        <TableRow key={task.key}>
                          <TableCell>
                            {titleOf(task.title)}
                            <Typography variant="caption" color="text.secondary" component="span" sx={{ display: 'block' }}>
                              {titleOf(task.station_title)} · {t(`content:challenge.types.${task.type}`)}
                            </Typography>
                          </TableCell>
                          {compared.map((run) => {
                            const cell = task.cells[run.run_id];
                            return (
                              <TableCell key={run.run_id} align="right" sx={{ verticalAlign: 'top' }}>
                                {cell ? (
                                  <>
                                    {t('analytics.compare.tasks.cellAnswered', {
                                      answered: cell.submissions,
                                      teams: cell.team_count,
                                    })}
                                    <Typography variant="caption" component="span" sx={{ display: 'block' }}>
                                      {t('analytics.compare.tasks.cellPoints', {
                                        points: formatNumber(cell.avg_points, lang),
                                      })}
                                    </Typography>
                                    <Typography variant="caption" component="span" sx={{ display: 'block' }}>
                                      {t('analytics.compare.tasks.cellTime', {
                                        time: formatDuration(cell.time_median_seconds, lang),
                                      })}
                                    </Typography>
                                  </>
                                ) : (
                                  <Typography variant="caption" color="text.secondary">
                                    {t('analytics.compare.notInRun')}
                                  </Typography>
                                )}
                              </TableCell>
                            );
                          })}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </ScrollRegion>
              )}
            </CardContent>
          </Card>
        </Stack>
      )}
    </Box>
  );
};

export default AnalyticsCompareView;
