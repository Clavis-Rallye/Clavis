import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { excerpt } from '../../utils/richtext';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { localizedText } from '../../i18n/contentLocales';
import {
  Box,
  Button,
  Chip,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import DeleteForeverIcon from '@mui/icons-material/DeleteForever';
import DownloadIcon from '@mui/icons-material/Download';
import EditIcon from '@mui/icons-material/Edit';
import RestoreFromTrashIcon from '@mui/icons-material/RestoreFromTrash';
import {
  deleteRallye,
  listRallyeStations,
  listRallyes,
  permanentlyDeleteRallye,
  restoreRallye,
} from '../../api/content';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { Rallye } from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import DataTable, { type DataTableColumn, type DataTableFilter } from '../../components/DataTable';

const ROLES_WHO_CAN_CREATE = ['creator', 'org_admin'];

const RallyeList: React.FC = () => {
  const { t, i18n } = useTranslation('content');
  const { user } = useAuth();
  const navigate = useNavigate();

  const [rallyes, setRallyes] = useState<Rallye[]>([]);
  const [stationCounts, setStationCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<Rallye | null>(null);
  const [deleting, setDeleting] = useState(false);
  // The archive view of the org_admin, with restore and permanent delete.
  const isOrgAdmin = user?.role === 'org_admin';
  const [showArchived, setShowArchived] = useState(false);
  const [toDeleteForever, setToDeleteForever] = useState<Rallye | null>(null);

  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';
  const titleOf = (r: Rallye) => localizedText(r.title, activeLang) || r.id;
  // Published outranks draft outranks archived.
  const statusRank = (r: Rallye) => (r.is_deleted ? -1 : r.is_published ? 1 : 0);

  const load = useCallback(async () => {
    if (!user?.organization_slug) return;
    const slug = user.organization_slug;
    setLoading(true);
    setError(null);
    try {
      const data = await listRallyes(slug, { include_deleted: showArchived });
      // Status descending, name ascending inside each status group.
      const sorted = [...data.items].sort((a, b) => {
        const rankDiff = statusRank(b) - statusRank(a);
        return rankDiff !== 0
          ? rankDiff
          : titleOf(a).localeCompare(titleOf(b), undefined, { numeric: true, sensitivity: 'base' });
      });
      setRallyes(sorted);
      // Rallye has no embedded station count — fetch link counts per rallye.
      const counts = await Promise.all(
        sorted.map(async (r) => {
          try {
            const resp = await listRallyeStations(slug, r.id);
            return [r.id, resp.total] as const;
          } catch {
            return [r.id, 0] as const;
          }
        }),
      );
      setStationCounts(Object.fromEntries(counts));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.organization_slug, showArchived, activeLang]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleDelete = async () => {
    if (!user?.organization_slug || !toDelete) return;
    setDeleting(true);
    try {
      await deleteRallye(user.organization_slug, toDelete.id);
      setToDelete(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setDeleting(false);
    }
  };

  const handleRestore = async (r: Rallye) => {
    if (!user?.organization_slug) return;
    try {
      await restoreRallye(user.organization_slug, r.id);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  const handleDeleteForever = async () => {
    if (!user?.organization_slug || !toDeleteForever) return;
    setDeleting(true);
    try {
      await permanentlyDeleteRallye(user.organization_slug, toDeleteForever.id);
      setToDeleteForever(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setDeleting(false);
    }
  };

  const canCreate = !!user && ROLES_WHO_CAN_CREATE.includes(user.role);

  const descOf = (r: Rallye) =>
    excerpt(r.description[activeLang] ?? r.description.de ?? r.description.en ?? '', 200);

  const handleExportCsv = () => {
    const header = [
      t('rallye.list.table.name'),
      t('rallye.list.table.description'),
      t('rallye.list.table.status'),
      t('rallye.list.table.stations'),
      t('rallye.list.table.author'),
    ].join(',');
    const escape = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
    const rows = rallyes.map((r) =>
      [
        escape(titleOf(r)),
        escape(descOf(r)),
        r.is_published ? t('rallye.list.filter.published') : t('rallye.list.filter.draft'),
        String(stationCounts[r.id] ?? 0),
        escape(r.created_by_display_name ?? ''),
      ].join(','),
    );
    const csv = [header, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = t('rallye.list.exportCsvFilename', {
      org: user?.organization_slug ?? 'rallyes',
    });
    a.click();
    URL.revokeObjectURL(url);
  };

  const columns = useMemo<DataTableColumn<Rallye>[]>(
    () => [
      {
        id: 'name',
        header: t('rallye.list.table.name'),
        searchValue: (r) => titleOf(r),
        sortValue: (r) => titleOf(r),
        render: (r) => (
          <Box component="span" sx={{ fontWeight: 600 }}>
            {titleOf(r)}
          </Box>
        ),
      },
      {
        id: 'description',
        header: t('rallye.list.table.description'),
        hideBelow: 'md',
        render: (r) => (
          <Box component="span" sx={{ color: 'text.secondary' }}>
            {descOf(r)}
          </Box>
        ),
      },
      {
        id: 'stations',
        header: t('rallye.list.table.stations'),
        align: 'right',
        sortValue: (r) => stationCounts[r.id] ?? 0,
        render: (r) => stationCounts[r.id] ?? 0,
      },
      {
        id: 'author',
        header: t('rallye.list.table.author'),
        hideBelow: 'sm',
        sortValue: (r) => r.created_by_display_name ?? '',
        render: (r) => (
          <Box component="span" sx={{ color: 'text.secondary' }}>
            {r.created_by_display_name ?? '—'}
          </Box>
        ),
      },
      {
        id: 'status',
        header: t('rallye.list.table.status'),
        sortValue: statusRank,
        render: (r) =>
          r.is_deleted ? (
            <Chip label={t('archive.archivedChip')} color="warning" size="small" variant="outlined" />
          ) : (
            <Chip
              label={
                r.is_published ? t('rallye.list.filter.published') : t('rallye.list.filter.draft')
              }
              color={r.is_published ? 'success' : 'default'}
              size="small"
              variant={r.is_published ? 'filled' : 'outlined'}
            />
          ),
      },
      {
        id: 'actions',
        header: t('rallye.list.table.actions'),
        align: 'right',
        actions: true,
        width: 100,
        render: (r) =>
          r.is_deleted ? (
            <Stack direction="row" spacing={0.5} sx={{ justifyContent: 'flex-end' }}>
              <Tooltip title={t('archive.restore')}>
                <IconButton
                  size="small"
                  color="primary"
                  aria-label={t('archive.restore')}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleRestore(r);
                  }}
                >
                  <RestoreFromTrashIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title={t('archive.deleteForever')}>
                <IconButton
                  size="small"
                  color="error"
                  aria-label={t('archive.deleteForever')}
                  onClick={(e) => {
                    e.stopPropagation();
                    setToDeleteForever(r);
                  }}
                >
                  <DeleteForeverIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Stack>
          ) : (
            <Stack direction="row" spacing={0.5} sx={{ justifyContent: 'flex-end' }}>
              <Tooltip title={t('rallye.list.table.open')}>
                <IconButton
                  size="small"
                  aria-label={`${t('rallye.list.table.open')}: ${titleOf(r)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/rallyes/${r.id}`);
                  }}
                >
                  <EditIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title={t('archive.archive')}>
                <IconButton
                  size="small"
                  color="error"
                  aria-label={`${t('archive.archive')}: ${titleOf(r)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setToDelete(r);
                  }}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Stack>
          ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, activeLang, navigate, stationCounts],
  );

  const filters = useMemo<DataTableFilter<Rallye>[]>(
    () => [
      {
        id: 'status',
        label: t('rallye.list.filter.label'),
        options: [
          { value: 'published', label: t('rallye.list.filter.published') },
          { value: 'draft', label: t('rallye.list.filter.draft') },
        ],
        match: (r, value) => (value === 'published' ? r.is_published : !r.is_published),
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
          {t('rallye.list.title')}
        </Typography>
        {canCreate && (
          <Button variant="contained" onClick={() => navigate('/rallyes/new')}>
            {t('rallye.list.createButton')}
          </Button>
        )}
      </Stack>

      <DataTable<Rallye>
        rows={rallyes}
        columns={columns}
        getRowId={(r) => r.id}
        ariaLabel={t('rallye.list.title')}
        loading={loading}
        error={error}
        onRetry={load}
        emptyMessage={t('rallye.list.empty')}
        searchPlaceholder={t('rallye.list.search')}
        filters={filters}
        onRowClick={(r) => (r.is_deleted ? undefined : navigate(`/rallyes/${r.id}`))}
        toolbarActions={
          <>
            {isOrgAdmin && (
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={showArchived}
                    onChange={(e) => setShowArchived(e.target.checked)}
                  />
                }
                label={t('archive.showArchived')}
              />
            )}
            <Button
              variant="outlined"
              startIcon={<DownloadIcon />}
              onClick={handleExportCsv}
              disabled={rallyes.length === 0}
            >
              {t('rallye.list.exportCsv')}
            </Button>
          </>
        }
      />

      <ConfirmDialog
        open={!!toDelete}
        title={t('rallye.detail.archive')}
        message={t('rallye.detail.archiveConfirm')}
        destructive
        loading={deleting}
        confirmLabel={t('archive.archive')}
        onConfirm={handleDelete}
        onCancel={() => setToDelete(null)}
      />

      <ConfirmDialog
        open={!!toDeleteForever}
        title={t('archive.deleteForeverTitle')}
        message={t('archive.deleteForeverConfirm')}
        destructive
        loading={deleting}
        confirmLabel={t('archive.deleteForever')}
        onConfirm={handleDeleteForever}
        onCancel={() => setToDeleteForever(null)}
      />
    </Box>
  );
};

export default RallyeList;
