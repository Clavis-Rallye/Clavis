import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  FormControl,
  FormControlLabel,
  FormGroup,
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
  TextField,
  Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import PrintOutlinedIcon from '@mui/icons-material/PrintOutlined';
import { BarChart } from '@mui/x-charts/BarChart';
import { LineChart } from '@mui/x-charts/LineChart';
import { formatDate } from '../../utils/formatDate';
import { countAxis } from './countAxis';
import { getAnalyticsTimeseries, exportAnalyticsTimeseriesCsv } from '../../api/runs';
import { ApiError } from '../../api/client';
import { csvBlob, downloadBlob, downloadJson } from '../../utils/download';
import type {
  AnalyticsBucketGranularity,
  AnalyticsMetricKey,
  AnalyticsPeriodExportTable,
  AnalyticsTimeseries,
} from '../../api/types';
import { localizedText } from '../../i18n/contentLocales';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import ChartSection from './ChartSection';
import PrintHeader from './PrintHeader';
import WorkingTimeHelp from './WorkingTimeHelp';
import { formatDuration, formatNumber, formatPercent, formatSpread, toMinutes } from './analyticsFormat';

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

// Distinct hues for the grouped series. The first five are the default-on set
// (Runs, Participants, Submissions, Scored, Unscored); the rest are opt-in.
const METRICS: { key: AnalyticsMetricKey; defaultOn: boolean; color: string }[] = [
  { key: 'run_count', defaultOn: true, color: '#2850A9' },
  { key: 'participant_count', defaultOn: true, color: '#2E8FA9' },
  { key: 'submission_count', defaultOn: true, color: '#C0562B' },
  { key: 'scored_count', defaultOn: true, color: '#3E8E3E' },
  { key: 'unscored_count', defaultOn: true, color: '#8A8D91' },
  { key: 'team_count', defaultOn: false, color: '#7A4FBF' },
  { key: 'guest_count', defaultOn: false, color: '#8A5A2B' },
  { key: 'registered_count', defaultOn: false, color: '#B23A6F' },
];

// The x-axis height: the library default of 25 px leaves 16 px for tick
// labels, but a 12 px Noto Sans label needs 17 px — the library then drops
// the label entirely. 32 px keeps every date bucket named.
// The chart also passes skipAnimation so the PNG export and the print
// report capture the final state instead of a mid-animation frame.
const X_AXIS_HEIGHT = 32;

const isoDate = (d: Date): string => d.toISOString().slice(0, 10);

// The two measures of the course over time. The app's blue and
// green, the pair the run view validated.
const POINTS_COLOR = '#2850A9';
const DURATION_COLOR = '#3E8E3E';
// The team list shows this many rows until the reader asks for all.
const TEAM_PREVIEW = 10;

interface AnalyticsRangeViewProps {
  orgSlug: string;
}

const AnalyticsRangeView: React.FC<AnalyticsRangeViewProps> = ({ orgSlug }) => {
  const { t, i18n } = useTranslation(['runs', 'content']);
  const lang = i18n.language;
  const [allTeams, setAllTeams] = useState(false);

  const today = useMemo(() => new Date(), []);
  const [dateFrom, setDateFrom] = useState(() => isoDate(new Date(today.getTime() - 30 * 86_400_000)));
  const [dateTo, setDateTo] = useState(() => isoDate(today));
  const [bucket, setBucket] = useState<AnalyticsBucketGranularity>('day');
  const [enabled, setEnabled] = useState<Set<AnalyticsMetricKey>>(
    () => new Set(METRICS.filter((m) => m.defaultOn).map((m) => m.key)),
  );

  const [data, setData] = useState<AnalyticsTimeseries | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!orgSlug) return;
    setLoading(true);
    setError(null);
    try {
      setData(await getAnalyticsTimeseries(orgSlug, { dateFrom, dateTo, bucket }));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [orgSlug, dateFrom, dateTo, bucket]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleMetric = (key: AnalyticsMetricKey) =>
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const enabledMetrics = METRICS.filter((m) => enabled.has(m.key));
  // The day in the interface's format, 19.10.2026 on a German screen, not
  // the ISO date of the API.
  const dataset = (data?.buckets ?? []).map((b) => ({ ...b, label: formatDate(b.bucket_start, i18n.language) }));

  const handleExport = async (table: AnalyticsPeriodExportTable = 'metrics') => {
    setExportError(null);
    try {
      const csv = await exportAnalyticsTimeseriesCsv(orgSlug, { dateFrom, dateTo, bucket }, table);
      // The period is in the name, like the JSON and PNG exports.
      const name = table === 'metrics' ? 'timeseries' : `period-${table}`;
      downloadBlob(`analytics-${name}-${dateFrom}-${dateTo}.csv`, csvBlob(csv));
    } catch (err) {
      setExportError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  const handleExportJson = () => {
    if (!data) return;
    downloadJson(t('analytics.range.jsonFileName', { from: dateFrom, to: dateTo }), {
      generated_at: new Date().toISOString(),
      date_from: dateFrom,
      date_to: dateTo,
      bucket,
      ...data,
    });
  };

  const totals = data?.totals;
  // Points and working time per bucket. A bucket without a team
  // has no value, so the line has a gap there and not a false zero.
  const pointsDataset = dataset.map((b) => ({
    label: b.label,
    perTeam: b.avg_points_per_team ?? null,
    minutes: toMinutes(b.median_duration_seconds),
    b,
  }));
  const hasPoints = pointsDataset.some((row) => row.perTeam != null);
  const hasDuration = pointsDataset.some((row) => row.minutes != null);
  const tasks = data?.tasks ?? [];
  const teams = data?.teams ?? [];
  const csvButton = (table: AnalyticsPeriodExportTable, label: string) => (
    <Button
      className="print-hidden"
      size="small"
      variant="outlined"
      startIcon={<DownloadIcon />}
      onClick={() => void handleExport(table)}
    >
      {label}
    </Button>
  );
  const dateFormatter = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium' });
  const formatPrintDate = (value: string) => {
    if (!value) return '';
    const date = new Date(`${value}T00:00:00`);
    return Number.isNaN(date.getTime()) ? value : dateFormatter.format(date);
  };
  const printPer = {
    day: t('analytics.range.printPerDay'),
    week: t('analytics.range.printPerWeek'),
    month: t('analytics.range.printPerMonth'),
  }[bucket];
  // The head line names the range and the grouping: with "Woche" a row dated 26.10.2026
  // would read like a day.
  const printTitle = t('analytics.range.printTitle', {
    mode: t('analytics.modeRange'),
    from: formatPrintDate(dateFrom),
    to: formatPrintDate(dateTo),
    per: printPer,
  });

  return (
    <Box>
      <PrintHeader title={printTitle} />
      {/* Range + bucket controls */}
      <Card className="print-hidden" sx={{ mb: 2 }}>
        <CardContent>
          <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
            <TextField
              type="date"
              label={t('analytics.range.from', { defaultValue: 'Von' })}
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
              size="small"
            />
            <TextField
              type="date"
              label={t('analytics.range.to', { defaultValue: 'Bis' })}
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
              size="small"
            />
            <FormControl size="small" sx={{ minWidth: 140 }}>
              <InputLabel id="bucket-label">{t('analytics.range.bucket', { defaultValue: 'Gruppierung' })}</InputLabel>
              <Select
                labelId="bucket-label"
                label={t('analytics.range.bucket', { defaultValue: 'Gruppierung' })}
                value={bucket}
                onChange={(e) => setBucket(e.target.value as AnalyticsBucketGranularity)}
              >
                <MenuItem value="day">{t('analytics.range.bucketDay', { defaultValue: 'Tag' })}</MenuItem>
                <MenuItem value="week">{t('analytics.range.bucketWeek', { defaultValue: 'Woche' })}</MenuItem>
                <MenuItem value="month">{t('analytics.range.bucketMonth', { defaultValue: 'Monat' })}</MenuItem>
              </Select>
            </FormControl>
            <Button variant="outlined" startIcon={<DownloadIcon />} onClick={() => void handleExport('metrics')}>
              {t('analytics.range.exportCsv', { defaultValue: 'CSV exportieren' })}
            </Button>
            <Button
              variant="outlined"
              startIcon={<DownloadIcon />}
              onClick={handleExportJson}
              disabled={!data}
            >
              {t('analytics.range.exportJson', { defaultValue: 'JSON exportieren' })}
            </Button>
            <Button
              variant="outlined"
              startIcon={<PrintOutlinedIcon />}
              onClick={() => window.print()}
            >
              {t('analytics.range.exportPdf')}
            </Button>
          </Stack>
          {exportError && (
            <Box sx={{ mt: 2 }}>
              <ErrorAlert message={exportError} />
            </Box>
          )}
        </CardContent>
      </Card>

      {error && (
        <Box sx={{ mb: 2 }}>
          <ErrorAlert message={error} />
        </Box>
      )}

      {loading ? (
        <LoadingSpinner />
      ) : (
        <>
          {/* Range-total tiles */}
          {totals && (
            <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', mb: 2 }}>
              {(['run_count', 'participant_count', 'submission_count', 'scored_count'] as AnalyticsMetricKey[]).map(
                (k) => (
                  <Card className="print-card" key={k} sx={{ flex: '1 1 150px', minWidth: 150 }}>
                    <CardContent>
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {t(`analytics.range.metric.${k}`, { defaultValue: k })}
                      </Typography>
                      <Typography variant="h5" component="p" data-testid={`total-${k}`} sx={{ fontWeight: 700 }}>
                        {totals[k]}
                      </Typography>
                    </CardContent>
                  </Card>
                ),
              )}
              <Card className="print-card" sx={{ flex: '1 1 150px', minWidth: 150 }}>
                <CardContent>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {t('analytics.range.tiles.avgPointsPerTeam')}
                  </Typography>
                  <Typography variant="h5" component="p" data-testid="total-avg-points" sx={{ fontWeight: 700 }}>
                    {formatNumber(totals.avg_points_per_team, lang)}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {totals.points_percent != null
                      ? t('analytics.range.tiles.pointsCaption', { percent: formatPercent(totals.points_percent, lang) })
                      : t('analytics.range.tiles.pointsCaptionNone')}
                  </Typography>
                </CardContent>
              </Card>
              <Card className="print-card" sx={{ flex: '1 1 150px', minWidth: 150 }}>
                <CardContent>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {t('analytics.workingTime.tile')}
                  </Typography>
                  <Typography variant="h5" component="p" data-testid="total-working-time" sx={{ fontWeight: 700 }}>
                    {formatDuration(totals.median_duration_seconds, lang)}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {totals.teams_measured
                      ? t('analytics.range.duration.table.teams') + `: ${totals.teams_measured}`
                      : t('analytics.workingTime.tileCaptionNone')}
                  </Typography>
                </CardContent>
              </Card>
            </Stack>
          )}

          {/* Metric toggles */}
          <Card className="print-hidden" sx={{ mb: 2 }}>
            <CardContent>
              <Typography variant="subtitle2" component="p" sx={{ mb: 1 }}>
                {t('analytics.range.metricsLabel', { defaultValue: 'Metriken' })}
              </Typography>
              <FormGroup row>
                {METRICS.map((m) => (
                  <FormControlLabel
                    key={m.key}
                    control={<Checkbox checked={enabled.has(m.key)} onChange={() => toggleMetric(m.key)} size="small" />}
                    label={t(`analytics.range.metric.${m.key}`, { defaultValue: m.key })}
                  />
                ))}
              </FormGroup>
            </CardContent>
          </Card>

          {/* Grouped bars: the measures differ in kind, so stacking them would
              give a bar height and an axis that mean nothing, and would count
              scored submissions twice. */}
          <ChartSection
            title={t('analytics.range.chartTitle', { defaultValue: 'Kennzahlen pro Zeitraum' })}
            toggleLabel={t('analytics.range.toggleTable', { defaultValue: 'Datentabelle anzeigen' })}
            fileName={`analytics-timeseries-${dateFrom}-${dateTo}-metrics.png`}
            legend={enabledMetrics.map((m) => ({
              label: t(`analytics.range.metric.${m.key}`, { defaultValue: m.key }),
              color: m.color,
            }))}
            empty={dataset.length === 0 || enabledMetrics.length === 0}
            emptyMessage={
              enabledMetrics.length === 0
                ? t('analytics.range.selectMetric', { defaultValue: 'Bitte mindestens eine Metrik auswählen.' })
                : t('analytics.range.empty', { defaultValue: 'Keine Läufe im gewählten Zeitraum.' })
            }
            chart={
              <BarChart
                dataset={dataset}
                xAxis={[{ dataKey: 'label', scaleType: 'band', height: X_AXIS_HEIGHT }]}
                yAxis={[
                  countAxis(
                    t('analytics.axis.count'),
                    dataset.flatMap((b) => enabledMetrics.map((m) => b[m.key])),
                    i18n.language,
                  ),
                ]}
                series={enabledMetrics.map((m) => ({
                  dataKey: m.key,
                  label: t(`analytics.range.metric.${m.key}`, { defaultValue: m.key }),
                  color: m.color,
                }))}
                height={340}
                skipAnimation
              />
            }
            table={
              <ScrollRegion label={t('analytics.range.chartTitle', { defaultValue: 'Kennzahlen pro Zeitraum' })}>
                <Table size="small" aria-label={t('analytics.range.chartTitle', { defaultValue: 'Kennzahlen pro Zeitraum' })}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{t('analytics.range.date', { defaultValue: 'Datum' })}</TableCell>
                      {enabledMetrics.map((m) => (
                        <TableCell key={m.key} align="right">
                          {t(`analytics.range.metric.${m.key}`, { defaultValue: m.key })}
                        </TableCell>
                      ))}
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {dataset.map((b) => (
                      <TableRow key={b.label}>
                        <TableCell>{b.label}</TableCell>
                        {enabledMetrics.map((m) => (
                          <TableCell key={m.key} align="right">
                            {b[m.key]}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </ScrollRegion>
            }
          />

          {/* Points per team over time */}
          <Box sx={{ mt: 2 }}>
            <ChartSection
              title={t('analytics.range.points.title')}
              toggleLabel={t('analytics.range.points.toggleTable')}
              fileName={`analytics-timeseries-${dateFrom}-${dateTo}-points.png`}
              legend={[{ label: t('analytics.range.points.series'), color: POINTS_COLOR }]}
              empty={!hasPoints}
              emptyMessage={t('analytics.range.empty')}
              chart={
                <LineChart
                  dataset={pointsDataset}
                  xAxis={[{ dataKey: 'label', scaleType: 'point', height: X_AXIS_HEIGHT }]}
                  yAxis={[
                    countAxis(
                      t('analytics.axis.pointsPerTeam'),
                      pointsDataset.map((row) => row.perTeam),
                      lang,
                    ),
                  ]}
                  series={[
                    {
                      dataKey: 'perTeam',
                      // Runs take place on some days only. The line joins the
                      // buckets that have a value. A bucket without one has no mark.
                      connectNulls: true,
                      label: t('analytics.range.points.series'),
                      color: POINTS_COLOR,
                      showMark: true,
                      valueFormatter: (value: number | null) => formatNumber(value, lang),
                    },
                  ]}
                  height={300}
                  // Room for the last date label at the right edge.
                  margin={{ right: 40 }}
                  hideLegend
                  skipAnimation
                />
              }
              table={
                <ScrollRegion label={t('analytics.range.points.title')}>
                  <Table size="small" aria-label={t('analytics.range.points.title')}>
                    <TableHead>
                      <TableRow>
                        <TableCell>{t('analytics.range.date', { defaultValue: 'Datum' })}</TableCell>
                        <TableCell align="right">{t('analytics.range.points.table.runs')}</TableCell>
                        <TableCell align="right">{t('analytics.range.points.table.teams')}</TableCell>
                        <TableCell align="right">{t('analytics.range.points.table.total')}</TableCell>
                        <TableCell align="right">{t('analytics.range.points.table.perTeam')}</TableCell>
                        <TableCell align="right">{t('analytics.range.points.table.perAnswer')}</TableCell>
                        <TableCell align="right">{t('analytics.range.points.table.percent')}</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {pointsDataset.map(({ label, b }) => (
                        <TableRow key={label}>
                          <TableCell>{label}</TableCell>
                          <TableCell align="right">{b.run_count}</TableCell>
                          <TableCell align="right">{b.team_count}</TableCell>
                          <TableCell align="right">{b.total_points ?? 0}</TableCell>
                          <TableCell align="right">{formatNumber(b.avg_points_per_team, lang)}</TableCell>
                          <TableCell align="right">{formatNumber(b.avg_points_per_answer, lang)}</TableCell>
                          <TableCell align="right">{formatPercent(b.points_percent, lang)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                  <Typography variant="caption" color="text.secondary" component="p" sx={{ mt: 1 }}>
                    {t('analytics.range.points.percentHint')}
                  </Typography>
                </ScrollRegion>
              }
            />
          </Box>
          <Stack direction="row" sx={{ mt: 1, justifyContent: 'flex-end' }}>
            {csvButton('points', t('analytics.range.points.exportCsv'))}
          </Stack>

          {/* Working time over time */}
          <Box sx={{ mt: 2 }}>
            <ChartSection
              title={t('analytics.range.duration.title')}
              toggleLabel={t('analytics.range.duration.toggleTable')}
              fileName={`analytics-timeseries-${dateFrom}-${dateTo}-working-time.png`}
              legend={[{ label: t('analytics.range.duration.series'), color: DURATION_COLOR }]}
              empty={!hasDuration}
              emptyMessage={t('analytics.workingTime.empty')}
              chart={
                <LineChart
                  dataset={pointsDataset}
                  xAxis={[{ dataKey: 'label', scaleType: 'point', height: X_AXIS_HEIGHT }]}
                  yAxis={[countAxis(t('analytics.axis.minutes'), pointsDataset.map((row) => row.minutes), lang)]}
                  series={[
                    {
                      dataKey: 'minutes',
                      // Runs take place on some days only. The line joins the
                      // buckets that have a value. A bucket without one has no mark.
                      connectNulls: true,
                      label: t('analytics.range.duration.series'),
                      color: DURATION_COLOR,
                      showMark: true,
                      valueFormatter: (value: number | null) => formatDuration(value == null ? null : value * 60, lang),
                    },
                  ]}
                  height={300}
                  // Room for the last date label at the right edge.
                  margin={{ right: 40 }}
                  hideLegend
                  skipAnimation
                />
              }
              table={
                <ScrollRegion label={t('analytics.range.duration.title')}>
                  <Table size="small" aria-label={t('analytics.range.duration.title')}>
                    <TableHead>
                      <TableRow>
                        <TableCell>{t('analytics.range.date', { defaultValue: 'Datum' })}</TableCell>
                        <TableCell align="right">{t('analytics.range.duration.table.teams')}</TableCell>
                        <TableCell align="right">{t('analytics.range.duration.table.median')}</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {pointsDataset.map(({ label, b }) => (
                        <TableRow key={label}>
                          <TableCell>{label}</TableCell>
                          <TableCell align="right">{b.teams_measured ?? 0}</TableCell>
                          <TableCell align="right">{formatDuration(b.median_duration_seconds, lang)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </ScrollRegion>
              }
            />
          </Box>

          <Box sx={{ mt: 2 }}>
            <WorkingTimeHelp />
          </Box>

          {/* Points and time per task over the period */}
          <Card className="print-card" sx={{ mt: 2 }}>
            <CardContent>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1, flexWrap: 'wrap' }}>
                <Typography variant="h6" component="h2" sx={{ flexGrow: 1 }}>
                  {t('analytics.range.tasks.title')}
                </Typography>
                {tasks.length > 0 && csvButton('tasks', t('analytics.range.tasks.exportCsv'))}
              </Stack>
              {tasks.length === 0 ? (
                <Typography color="text.secondary" variant="body2">
                  {t('analytics.range.tasks.empty')}
                </Typography>
              ) : (
                <>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                    {t('analytics.range.tasks.hint')}
                  </Typography>
                  <ScrollRegion label={t('analytics.range.tasks.title')}>
                    <Table size="small" aria-label={t('analytics.range.tasks.title')}>
                      <TableHead>
                        <TableRow>
                          <TableCell>{t('analytics.range.tasks.table.station')}</TableCell>
                          <TableCell>{t('analytics.range.tasks.table.task')}</TableCell>
                          <TableCell>{t('analytics.range.tasks.table.type')}</TableCell>
                          <TableCell align="right">{t('analytics.range.tasks.table.runs')}</TableCell>
                          <TableCell align="right">{t('analytics.range.tasks.table.answers')}</TableCell>
                          <TableCell align="right">{t('analytics.range.tasks.table.scored')}</TableCell>
                          <TableCell align="right">{t('analytics.range.tasks.table.avgPoints')}</TableCell>
                          <TableCell align="right">{t('analytics.range.tasks.table.maxPoints')}</TableCell>
                          <TableCell align="right">{t('analytics.range.tasks.table.timeMedian')}</TableCell>
                          <TableCell align="right">{t('analytics.range.tasks.table.timeSpread')}</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {tasks.map((task) => (
                          <TableRow key={task.key}>
                            <TableCell>{localizedText(task.station_title, lang) || '—'}</TableCell>
                            <TableCell>{localizedText(task.title, lang) || '—'}</TableCell>
                            <TableCell>{t(`content:challenge.types.${task.type}`)}</TableCell>
                            <TableCell align="right">{task.run_count}</TableCell>
                            <TableCell align="right">{task.submissions}</TableCell>
                            <TableCell align="right">{task.scored}</TableCell>
                            <TableCell align="right">{formatNumber(task.avg_points, lang)}</TableCell>
                            <TableCell align="right">{task.max_points ?? '—'}</TableCell>
                            <TableCell align="right">{formatDuration(task.time_median_seconds, lang)}</TableCell>
                            <TableCell align="right">
                              {formatSpread(task.time_p25_seconds, task.time_p75_seconds, lang)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </ScrollRegion>
                </>
              )}
            </CardContent>
          </Card>

          {/* Points and working time per team over the period */}
          <Card className="print-card" sx={{ mt: 2 }}>
            <CardContent>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1, flexWrap: 'wrap' }}>
                <Typography variant="h6" component="h2" sx={{ flexGrow: 1 }}>
                  {t('analytics.range.teams.title')}
                </Typography>
                {teams.length > 0 && csvButton('teams', t('analytics.range.teams.exportCsv'))}
              </Stack>
              {teams.length === 0 ? (
                <Typography color="text.secondary" variant="body2">
                  {t('analytics.range.teams.empty')}
                </Typography>
              ) : (
                <>
                  <ScrollRegion label={t('analytics.range.teams.title')}>
                    <Table size="small" aria-label={t('analytics.range.teams.title')}>
                      <TableHead>
                        <TableRow>
                          <TableCell align="right">{t('analytics.range.teams.table.rank')}</TableCell>
                          <TableCell>{t('analytics.range.teams.table.team')}</TableCell>
                          <TableCell>{t('analytics.range.teams.table.run')}</TableCell>
                          <TableCell>{t('analytics.range.teams.table.date')}</TableCell>
                          <TableCell align="right">{t('analytics.range.teams.table.points')}</TableCell>
                          <TableCell align="right">{t('analytics.range.teams.table.answered')}</TableCell>
                          <TableCell align="right">{t('analytics.range.teams.table.duration')}</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {/* Every team is in the DOM. The rows beyond the preview are hidden on
                            screen while the preview is closed, and shown in print: the PDF holds
                            every team of the range. */}
                        {teams.map((team, index) => (
                          <TableRow
                            key={team.team_id}
                            data-print-only={!allTeams && index >= TEAM_PREVIEW ? '' : undefined}
                            sx={
                              !allTeams && index >= TEAM_PREVIEW
                                ? { display: 'none', '@media print': { display: 'table-row' } }
                                : undefined
                            }
                          >
                            <TableCell align="right">{index + 1}</TableCell>
                            <TableCell>
                              {team.team_name || t('results.public.unnamedTeam', { index: index + 1 })}
                            </TableCell>
                            <TableCell>{team.run_name}</TableCell>
                            <TableCell>{team.run_date ? formatDate(team.run_date, lang) : '—'}</TableCell>
                            <TableCell align="right">{team.points}</TableCell>
                            <TableCell align="right">{team.answers}</TableCell>
                            <TableCell align="right">{formatDuration(team.duration_seconds, lang)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </ScrollRegion>
                  {teams.length > TEAM_PREVIEW && (
                    <Button
                      className="print-hidden"
                      size="small"
                      sx={{ mt: 1 }}
                      aria-expanded={allTeams}
                      onClick={() => setAllTeams((v) => !v)}
                    >
                      {allTeams
                        ? t('analytics.range.teams.showFewer', { count: TEAM_PREVIEW })
                        : t('analytics.range.teams.showAll', { count: teams.length })}
                    </Button>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </Box>
  );
};

export default AnalyticsRangeView;
