import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Box, Button, Chip, IconButton, Stack, Tooltip, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import DownloadIcon from '@mui/icons-material/Download';
import { listRuns } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { Run, RunState } from '../../api/types';
import DataTable, { type DataTableColumn, type DataTableFilter } from '../../components/DataTable';
import { formatDate, formatDateTime } from '../../utils/formatDate';

const ROLES_WHO_CAN_CREATE = ['coordinator', 'creator', 'org_admin'];

const STATE_COLORS: Record<RunState, 'default' | 'primary' | 'success' | 'warning' | 'error'> = {
  configured: 'default',
  scheduled: 'primary',
  active: 'success',
  paused: 'warning',
  ended: 'error',
};

const STATES: RunState[] = ['configured', 'scheduled', 'active', 'paused', 'ended'];

const RunList: React.FC = () => {
  const { t, i18n } = useTranslation('runs');
  const { user } = useAuth();
  const navigate = useNavigate();

  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user?.organization_slug) return;
    setLoading(true);
    setError(null);
    try {
      const data = await listRuns(user.organization_slug);
      setRuns(data.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [user?.organization_slug]);

  useEffect(() => {
    void load();
  }, [load]);

  const canCreate = !!user && ROLES_WHO_CAN_CREATE.includes(user.role);

  const handleExportCsv = () => {
    const header = [
      t('list.table.name'),
      t('list.table.joinCode'),
      t('list.table.state'),
      t('list.table.scheduledFor'),
      t('list.table.createdAt'),
    ].join(',');
    const escape = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
    const rows = runs.map((r) =>
      [
        escape(r.name),
        r.join_code,
        t(`list.filter.${r.state}`),
        r.scheduled_for ? new Date(r.scheduled_for).toISOString() : '',
        new Date(r.created_at).toISOString(),
      ].join(','),
    );
    const csv = [header, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = t('list.exportCsvFilename', { org: user?.organization_slug ?? 'runs' });
    a.click();
    URL.revokeObjectURL(url);
  };

  const columns = useMemo<DataTableColumn<Run>[]>(
    () => [
      {
        id: 'name',
        header: t('list.table.name'),
        render: (r) => <Box sx={{ fontWeight: 600 }}>{r.name}</Box>,
        sortValue: (r) => r.name,
        searchValue: (r) => r.name,
      },
      {
        id: 'joinCode',
        header: t('list.table.joinCode'),
        render: (r) => <Box sx={{ fontFamily: 'monospace' }}>{r.join_code}</Box>,
        sortValue: (r) => r.join_code,
        hideBelow: 'sm',
      },
      {
        id: 'state',
        header: t('list.table.state'),
        render: (r) => (
          <Chip label={t(`list.filter.${r.state}`)} color={STATE_COLORS[r.state]} size="small" />
        ),
        sortValue: (r) => r.state,
      },
      {
        id: 'scheduledFor',
        header: t('list.table.scheduledFor'),
        render: (r) => (
          <Box sx={{ color: 'text.secondary' }}>
            {/* No seconds, and a four-digit year. */}
            {r.scheduled_for
              ? formatDateTime(r.scheduled_for, i18n.language)
              : t('list.table.neverScheduled')}
          </Box>
        ),
        sortValue: (r) => r.scheduled_for ?? '',
        hideBelow: 'sm',
      },
      {
        id: 'createdAt',
        header: t('list.table.createdAt'),
        render: (r) => (
          <Box sx={{ color: 'text.secondary' }}>{formatDate(r.created_at, i18n.language)}</Box>
        ),
        sortValue: (r) => r.created_at,
        hideBelow: 'md',
      },
      {
        id: 'actions',
        header: t('list.table.actions'),
        align: 'right',
        actions: true,
        render: (r) => (
          <Tooltip title={t('list.table.open')}>
            <IconButton
              size="small"
              aria-label={`${t('list.table.open')}: ${r.name}`}
              onClick={(e) => {
                e.stopPropagation();
                navigate(`/runs/${r.id}`);
              }}
            >
              <ChevronRightIcon />
            </IconButton>
          </Tooltip>
        ),
      },
    ],
    [navigate, t, i18n.language],
  );

  const filters = useMemo<DataTableFilter<Run>[]>(
    () => [
      {
        id: 'state',
        label: t('list.filter.label'),
        options: STATES.map((s) => ({ value: s, label: t(`list.filter.${s}`) })),
        match: (r, value) => r.state === value,
      },
    ],
    [t],
  );

  return (
    <Box>
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
          {t('list.title')}
        </Typography>
        {canCreate && (
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => navigate('/runs/new')}
          >
            {t('list.createButton')}
          </Button>
        )}
      </Stack>

      <DataTable<Run>
        rows={runs}
        columns={columns}
        getRowId={(r) => r.id}
        ariaLabel={t('list.title')}
        loading={loading}
        error={error}
        onRetry={load}
        emptyMessage={t('list.empty')}
        searchPlaceholder={t('list.search')}
        filters={filters}
        defaultSort={{ columnId: 'createdAt', dir: 'desc' }}
        onRowClick={(r) => navigate(`/runs/${r.id}`)}
        toolbarActions={
          <Button
            variant="outlined"
            startIcon={<DownloadIcon />}
            onClick={handleExportCsv}
            disabled={runs.length === 0}
          >
            {t('list.exportCsv')}
          </Button>
        }
      />
    </Box>
  );
};

export default RunList;
