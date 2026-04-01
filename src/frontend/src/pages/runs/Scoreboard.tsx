import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Box, Button, Stack, Typography } from '@mui/material';
import { getScoreboard } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import { useWebSocket } from '../../hooks/useWebSocket';
import type { Scoreboard as ScoreboardType, ScoreboardEntry } from '../../api/types';
import BackLink from '../../components/BackLink';
import DataTable, { type DataTableColumn } from '../../components/DataTable';

const AUTO_REFRESH_MS = 10_000;

const pctOf = (e: ScoreboardEntry) =>
  e.total_max_points > 0 ? (e.total_points / e.total_max_points) * 100 : 0;

const Scoreboard: React.FC = () => {
  const { t } = useTranslation('runs');
  const { user } = useAuth();
  const { runId } = useParams<{ runId: string }>();
  const location = useLocation();
  const isParticipantView = location.pathname.includes('/participant/');

  const orgSlug = user?.organization_slug ?? '';

  const [scoreboard, setScoreboard] = useState<ScoreboardType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    if (!orgSlug || !runId) return;
    setError(null);
    try {
      const data = await getScoreboard(orgSlug, runId);
      setScoreboard(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, runId]);

  useEffect(() => {
    void load();
    intervalRef.current = setInterval(() => void load(), AUTO_REFRESH_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [load]);

  // A dissolved team leaves the ranking, and the ranks of
  // the others are computed again. The run channel says when.
  useWebSocket({
    path: orgSlug && runId ? `/api/orgs/${orgSlug}/runs/${runId}/ws` : '',
    enabled: !!(orgSlug && runId),
    handlers: {
      team_dissolved: () => void load(),
    },
  });

  const entries = useMemo(() => scoreboard?.entries ?? [], [scoreboard]);

  // The place is the server's shared rank (equal points share a place: 1, 1, 3),
  // the same one the published result shows. The page and the export compute none.
  const toTsv = (rows: ScoreboardEntry[]) =>
    [
      ['Rank', 'Team', 'Points', 'Max Points', '%', 'Scored'].join('\t'),
      ...rows.map((e) => {
        const pct = e.total_max_points > 0 ? pctOf(e).toFixed(1) : '0.0';
        return [e.rank, e.team_name, e.total_points, e.total_max_points, pct, e.scored_count].join(
          '\t',
        );
      }),
    ].join('\n');

  const handleCopyTsv = () => {
    void navigator.clipboard.writeText(toTsv(entries));
  };

  const handleDownloadCsv = () => {
    const csv = toTsv(entries).replace(/\t/g, ',');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `scoreboard-${runId ?? 'run'}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const columns = useMemo<DataTableColumn<ScoreboardEntry>[]>(
    () => [
      {
        id: 'rank',
        header: t('scoreboard.rank'),
        render: (e) => e.rank,
      },
      {
        id: 'team',
        header: t('scoreboard.team'),
        render: (e) => e.team_name,
        sortValue: (e) => e.team_name,
        searchValue: (e) => e.team_name,
      },
      {
        id: 'points',
        header: t('scoreboard.points'),
        align: 'right',
        render: (e) => e.total_points,
        sortValue: (e) => e.total_points,
      },
      {
        id: 'maxPoints',
        header: t('scoreboard.maxPoints'),
        align: 'right',
        hideBelow: 'sm',
        render: (e) => e.total_max_points,
        sortValue: (e) => e.total_max_points,
      },
      {
        id: 'percentage',
        header: t('scoreboard.percentage'),
        align: 'right',
        render: (e) => `${pctOf(e).toFixed(1)}%`,
        sortValue: (e) => pctOf(e),
      },
      {
        id: 'scored',
        header: t('scoreboard.scored'),
        align: 'right',
        hideBelow: 'sm',
        render: (e) => e.scored_count,
        sortValue: (e) => e.scored_count,
      },
    ],
    [t],
  );

  return (
    <Box>
      <BackLink
        to={isParticipantView ? `/runs/${runId}/participant/stations` : `/runs/${runId}`}
        label={t('scoreboard.backToRun')}
      />
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{
          justifyContent: 'space-between',
          alignItems: { xs: 'stretch', sm: 'center' },
          mb: 3,
        }}
      >
        <Typography variant="h2" component="h1">
          {t('scoreboard.title')}
        </Typography>
      </Stack>

      <DataTable<ScoreboardEntry>
        rows={entries}
        columns={columns}
        getRowId={(e) => e.team_id}
        ariaLabel={t('scoreboard.title')}
        loading={loading}
        error={error}
        onRetry={load}
        defaultSort={{ columnId: 'points', dir: 'desc' }}
        toolbarActions={
          <Stack direction="row" spacing={1}>
            <Button variant="outlined" size="small" onClick={() => void load()}>
              {t('scoreboard.refresh')}
            </Button>
            <Button variant="outlined" size="small" onClick={handleCopyTsv}>
              {t('scoreboard.copy')}
            </Button>
            <Button variant="outlined" size="small" onClick={handleDownloadCsv}>
              {t('scoreboard.export')}
            </Button>
          </Stack>
        }
      />
    </Box>
  );
};

export default Scoreboard;
