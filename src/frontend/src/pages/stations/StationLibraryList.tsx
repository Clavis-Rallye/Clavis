import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { localizedText } from '../../i18n/contentLocales';
import {
  Box,
  Button,
  Chip,
  FormControlLabel,
  IconButton,
  Snackbar,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import DeleteForeverIcon from '@mui/icons-material/DeleteForever';
import EditIcon from '@mui/icons-material/Edit';
import RestoreFromTrashIcon from '@mui/icons-material/RestoreFromTrash';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import {
  deleteStation,
  listStations,
  permanentlyDeleteStation,
  restoreStation,
  duplicateStation,
} from '../../api/content';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { MultilingualString, Station, StationSortKey } from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import DataTable, { type DataTableColumn, type DataTableQuery } from '../../components/DataTable';
import { formatDate } from '../../utils/formatDate';

const StationLibraryList: React.FC = () => {
  const { t, i18n } = useTranslation('content');
  const { user } = useAuth();
  const navigate = useNavigate();

  const [stations, setStations] = useState<Station[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<Station | null>(null);
  const [deleting, setDeleting] = useState(false);
  // A short word after the archive of a station.
  const [notice, setNotice] = useState<string | null>(null);
  // Server mode: the table emits the query, the page keeps the last one and
  // loads with it.
  const [query, setQuery] = useState<DataTableQuery | null>(null);
  // Drops responses that arrive after a newer request was issued.
  const requestRef = useRef(0);
  // The archive view of the org_admin, with restore and permanent delete.
  const isOrgAdmin = user?.role === 'org_admin';
  const [showArchived, setShowArchived] = useState(false);
  const [toDeleteForever, setToDeleteForever] = useState<Station | null>(null);

  const orgSlug = user?.organization_slug;
  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';
  const titleOf = useCallback(
    (ml: MultilingualString) => localizedText(ml, activeLang),
    [activeLang],
  );
  // A two-digit year ("01.02.26") is ambiguous. The shared
  // helper prints the locale's medium date with a four-digit year.
  const fmtDate = useCallback((iso: string) => formatDate(iso, activeLang), [activeLang]);

  const load = useCallback(
    async (q: DataTableQuery) => {
      if (!orgSlug) return;
      const requestId = ++requestRef.current;
      setLoading(true);
      setError(null);
      try {
        const resp = await listStations(orgSlug, {
          skip: q.page * q.rowsPerPage,
          limit: q.rowsPerPage,
          q: q.search,
          sort: q.sort?.key as StationSortKey | undefined,
          order: q.sort?.dir,
          // The title order depends on the interface language.
          lang: activeLang,
          include_deleted: showArchived,
        });
        if (requestId !== requestRef.current) return;
        setStations(resp.items);
        setTotal(resp.total);
      } catch (err) {
        if (requestId !== requestRef.current) return;
        setError(err instanceof ApiError ? err.detail : String(err));
      } finally {
        if (requestId === requestRef.current) setLoading(false);
      }
    },
    [orgSlug, activeLang, showArchived],
  );

  const reload = useCallback(() => {
    if (query) void load(query);
  }, [query, load]);

  // The `columns` memo below does not re-run when `query` changes, so a
  // handler it captures must not close over `reload` directly — it would keep
  // replaying the initial no-op closure (`query === null`) forever. This ref
  // always holds the current `reload` instead. Declared, and kept current,
  // before the effect below reads it, so that effect always sees this
  // render's `reload`.
  const reloadRef = useRef(reload);
  useEffect(() => {
    reloadRef.current = reload;
  }, [reload]);

  // Not before the first query arrives, or the first page would load twice.
  // `load` changes identity when the language or archive switch changes, so
  // both reload with the current query. Routed through `reloadRef` (already
  // current for this render, from the effect above) instead of calling
  // `load` in the effect body directly, which would set state synchronously
  // inside the effect (react-hooks/set-state-in-effect).
  useEffect(() => {
    if (query) void reloadRef.current();
  }, [query, load]);

  const rallyeNames = useCallback(
    (s: Station) => (s.usage?.rallye_titles ?? []).map((rt) => titleOf(rt)).filter(Boolean),
    [titleOf],
  );

  const handleDelete = async () => {
    if (!orgSlug || !toDelete) return;
    setDeleting(true);
    try {
      await deleteStation(orgSlug, toDelete.id);
      setToDelete(null);
      setNotice(t('library.stationArchived'));
      // Reload with the current query so search, sort and page survive.
      // When "Show archived" is on, the just-archived item stays visible with
      // its archived state, matching what a reload would show.
      await reloadRef.current();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setDeleting(false);
    }
  };

  // The copy opens at once, so the creator adapts it right away.
  const handleDuplicate = useCallback(
    async (id: string) => {
      if (!orgSlug) return;
      try {
        const copy = await duplicateStation(orgSlug, id);
        navigate(`/stations/${copy.id}`);
      } catch {
        setError(t('library.duplicateFailed'));
      }
    },
    [orgSlug, navigate, t],
  );

  // Stable identity, always-current reload: captured by the `columns` memo,
  // which must never hold a stale `reload`. A restored row has to lose its
  // archived state in the displayed result.
  const handleRestore = useCallback(
    async (s: Station) => {
      if (!orgSlug) return;
      try {
        await restoreStation(orgSlug, s.id);
        await reloadRef.current();
      } catch (err) {
        setError(err instanceof ApiError ? err.detail : String(err));
      }
    },
    [orgSlug],
  );

  const handleDeleteForever = async () => {
    if (!orgSlug || !toDeleteForever) return;
    setDeleting(true);
    try {
      await permanentlyDeleteStation(orgSlug, toDeleteForever.id);
      setToDeleteForever(null);
      // Reload with the current query so search, sort and page survive.
      await reloadRef.current();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setDeleting(false);
    }
  };

  const columns = useMemo<DataTableColumn<Station>[]>(
    () => [
      {
        id: 'name',
        header: t('station.list.table.name'),
        sortKey: 'title',
        render: (s) => (
          <>
            <Box component="span" sx={{ fontWeight: 600 }}>
              {titleOf(s.title) || s.id}
              {s.is_deleted && (
                <Chip
                  label={t('archive.archivedChip')}
                  color="warning"
                  size="small"
                  variant="outlined"
                  sx={{ ml: 1 }}
                />
              )}
            </Box>
            {/* The search also matches the address, so a hit by address needs
                a visible reason. */}
            {s.address && (
              <Typography
                variant="body2"
                component="span"
                color="text.secondary"
                sx={{ display: 'block' }}
              >
                {s.address}
              </Typography>
            )}
          </>
        ),
      },
      {
        id: 'rallye',
        header: t('station.list.table.rallye'),
        hideBelow: 'sm',
        render: (s) => {
          const names = rallyeNames(s);
          return (
            <Box component="span" sx={{ color: 'text.secondary' }}>
              {names.length > 0 ? names.join(', ') : t('station.list.table.unused')}
            </Box>
          );
        },
      },
      {
        id: 'usage',
        header: t('station.list.table.usageCount'),
        align: 'right',
        sortKey: 'usage',
        render: (s) => (
          <Box component="span" sx={{ color: 'text.secondary' }}>
            {s.usage_count ?? 0}
          </Box>
        ),
      },
      {
        id: 'author',
        header: t('station.list.table.author'),
        hideBelow: 'md',
        sortKey: 'author',
        render: (s) => (
          <Box component="span" sx={{ color: 'text.secondary' }}>
            {s.created_by_display_name ?? '—'}
          </Box>
        ),
      },
      {
        id: 'date',
        header: t('station.list.table.date'),
        hideBelow: 'lg',
        sortKey: 'created_at',
        render: (s) => (
          <Box component="span" sx={{ color: 'text.secondary', whiteSpace: 'nowrap' }}>
            {fmtDate(s.created_at)}
          </Box>
        ),
      },
      {
        id: 'actions',
        header: t('station.list.table.actions'),
        align: 'right',
        actions: true,
        render: (s) =>
          s.is_deleted ? (
            <Box sx={{ whiteSpace: 'nowrap' }}>
              <Tooltip title={t('archive.restore')}>
                <IconButton
                  size="small"
                  color="primary"
                  aria-label={`${t('archive.restore')}: ${titleOf(s.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleRestore(s);
                  }}
                >
                  <RestoreFromTrashIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title={t('archive.deleteForever')}>
                <IconButton
                  size="small"
                  color="error"
                  aria-label={`${t('archive.deleteForever')}: ${titleOf(s.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setToDeleteForever(s);
                  }}
                >
                  <DeleteForeverIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
          ) : (
            <Box sx={{ whiteSpace: 'nowrap' }}>
              <Tooltip title={t('station.list.table.open')}>
                <IconButton
                  size="small"
                  aria-label={`${t('station.list.table.open')}: ${titleOf(s.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/stations/${s.id}`);
                  }}
                >
                  <EditIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              {/* A complete copy to adapt, opened at once. */}
              <Tooltip title={t('library.duplicate')}>
                <IconButton
                  size="small"
                  aria-label={`${t('library.duplicate')}: ${titleOf(s.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleDuplicate(s.id);
                  }}
                >
                  <ContentCopyIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title={t('archive.archive')}>
                <IconButton
                  size="small"
                  color="error"
                  aria-label={`${t('archive.archive')}: ${titleOf(s.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setToDelete(s);
                  }}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
          ),
      },
    ],
    [t, navigate, rallyeNames, handleRestore, handleDuplicate, titleOf, fmtDate],
  );

  const deleteRallyeNames = toDelete ? rallyeNames(toDelete) : [];

  return (
    <Box>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{
          justifyContent: 'space-between',
          alignItems: { xs: 'stretch', sm: 'center' },
          mb: 1,
        }}
      >
        <Typography variant="h2" component="h1">
          {t('station.list.title')}
        </Typography>
        <Button variant="contained" onClick={() => navigate('/stations/new')}>
          {t('station.list.createButton')}
        </Button>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        {t('station.list.subtitle')}
      </Typography>

      <DataTable<Station>
        rows={stations}
        columns={columns}
        getRowId={(s) => s.id}
        ariaLabel={t('station.list.title')}
        loading={loading}
        error={error}
        onRetry={reload}
        emptyMessage={
          <>
            <Typography variant="body1" color="text.secondary">
              {t('station.list.empty')}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t('station.list.emptyHint')}
            </Typography>
          </>
        }
        noMatchMessage={
          <Typography variant="body1" color="text.secondary">
            {t('station.list.noMatch')}
          </Typography>
        }
        searchable
        searchPlaceholder={t('station.list.search')}
        defaultSort={{ columnId: 'name', dir: 'asc' }}
        onRowClick={(s) => (s.is_deleted ? undefined : navigate(`/stations/${s.id}`))}
        server={{ total, onQueryChange: setQuery }}
        toolbarActions={
          isOrgAdmin ? (
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
          ) : undefined
        }
      />

      <ConfirmDialog
        open={!!toDelete}
        title={t('station.detail.archive')}
        message={
          deleteRallyeNames.length > 0
            ? t('station.detail.archiveInUseConfirm', {
                count: deleteRallyeNames.length,
                rallyes: deleteRallyeNames.join(', '),
              })
            : t('station.detail.archiveConfirm')
        }
        destructive
        loading={deleting}
        confirmLabel={t('archive.archive')}
        onConfirm={handleDelete}
        onCancel={() => setToDelete(null)}
      />

      <Snackbar
        open={notice !== null}
        autoHideDuration={4000}
        onClose={(_, reason) => {
          if (reason !== 'clickaway') setNotice(null);
        }}
        message={notice}
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

export default StationLibraryList;
