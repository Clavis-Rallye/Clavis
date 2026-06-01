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
  deleteChallenge,
  listChallenges,
  permanentlyDeleteChallenge,
  restoreChallenge,
  duplicateChallenge,
} from '../../api/content';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type {
  Challenge,
  ChallengeSortKey,
  ChallengeType,
  MultilingualString,
} from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import DataTable, { type DataTableColumn, type DataTableFilter, type DataTableQuery } from '../../components/DataTable';
import { formatDate } from '../../utils/formatDate';
// One order for every type list, shared by the filter and the create form.
import { CHALLENGE_TYPES } from '../../utils/challengeTypes';


const ChallengeLibraryList: React.FC = () => {
  const { t, i18n } = useTranslation('content');
  const { user } = useAuth();
  const navigate = useNavigate();

  const [challenges, setChallenges] = useState<Challenge[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<Challenge | null>(null);
  const [deleting, setDeleting] = useState(false);
  // Server mode: the table emits the query, the page keeps the last one and
  // loads with it.
  const [query, setQuery] = useState<DataTableQuery | null>(null);
  // Drops responses that arrive after a newer request was issued.
  const requestRef = useRef(0);
  // The archive view of the org_admin, with restore and permanent delete.
  const isOrgAdmin = user?.role === 'org_admin';
  const [showArchived, setShowArchived] = useState(false);
  const [toDeleteForever, setToDeleteForever] = useState<Challenge | null>(null);

  const orgSlug = user?.organization_slug;
  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';
  const titleOf = (ml: MultilingualString) => localizedText(ml, activeLang);
  // A two-digit year ("01.02.26") is ambiguous. The shared
  // helper prints the locale's medium date with a four-digit year.
  const fmtDate = (iso: string) => formatDate(iso, activeLang);

  const load = useCallback(
    async (q: DataTableQuery) => {
      if (!orgSlug) return;
      const requestId = ++requestRef.current;
      setLoading(true);
      setError(null);
      try {
        const resp = await listChallenges(orgSlug, {
          skip: q.page * q.rowsPerPage,
          limit: q.rowsPerPage,
          q: q.search,
          sort: q.sort?.key as ChallengeSortKey | undefined,
          order: q.sort?.dir,
          // The title order depends on the interface language.
          lang: activeLang,
          include_deleted: showArchived,
          // The table leaves the "all" value out of `filters`, so a present
          // `type` is always a real challenge type.
          type: q.filters.type as ChallengeType | undefined,
        });
        if (requestId !== requestRef.current) return;
        setChallenges(resp.items);
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

  // Not before the first query arrives, or the first page would load twice.
  // `load` changes identity when the language or archive switch changes, so
  // both reload with the current query.
  useEffect(() => {
    if (query) void load(query);
  }, [query, load]);

  const reload = useCallback(() => {
    if (query) void load(query);
  }, [query, load]);

  // The `columns` memo below does not re-run when `query` changes, so a
  // handler it captures must not close over `reload` directly. It would keep
  // replaying the initial no-op closure (`query === null`) forever. This ref
  // always holds the current `reload`.
  const reloadRef = useRef(reload);
  useEffect(() => {
    reloadRef.current = reload;
  }, [reload]);

  const stationNames = useCallback(
    (c: Challenge) => (c.usage?.station_titles ?? []).map((st) => titleOf(st)).filter(Boolean),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeLang],
  );
  const rallyeNames = useCallback(
    (c: Challenge) => (c.usage?.rallye_titles ?? []).map((rt) => titleOf(rt)).filter(Boolean),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeLang],
  );

  const handleDelete = async () => {
    if (!orgSlug || !toDelete) return;
    setDeleting(true);
    try {
      await deleteChallenge(orgSlug, toDelete.id);
      setToDelete(null);
      // Reload with the current query so search, filter, sort and page survive.
      // When "Show archived" is on, the just-archived item stays visible
      // with its archived state, matching what a reload would show.
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
        const copy = await duplicateChallenge(orgSlug, id);
        navigate(`/challenges/${copy.id}`);
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
    async (c: Challenge) => {
      if (!orgSlug) return;
      try {
        await restoreChallenge(orgSlug, c.id);
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
      await permanentlyDeleteChallenge(orgSlug, toDeleteForever.id);
      setToDeleteForever(null);
      // Reload with the current query so search, filter, sort and page survive.
      await reloadRef.current();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setDeleting(false);
    }
  };

  const columns = useMemo<DataTableColumn<Challenge>[]>(
    () => [
      {
        id: 'name',
        header: t('challenge.list.table.name'),
        sortKey: 'title',
        render: (c) => (
          <Box component="span" sx={{ fontWeight: 600 }}>
            {titleOf(c.title) || c.id}
            {c.is_deleted && (
              <Chip
                label={t('archive.archivedChip')}
                color="warning"
                size="small"
                variant="outlined"
                sx={{ ml: 1 }}
              />
            )}
          </Box>
        ),
      },
      {
        id: 'station',
        header: t('challenge.list.table.station'),
        hideBelow: 'sm',
        render: (c) => {
          const names = stationNames(c);
          return (
            <Box component="span" sx={{ color: 'text.secondary' }}>
              {names.length > 0 ? names.join(', ') : t('challenge.list.table.unused')}
            </Box>
          );
        },
      },
      {
        id: 'rallye',
        header: t('challenge.list.table.rallye'),
        hideBelow: 'md',
        render: (c) => {
          const names = rallyeNames(c);
          return (
            <Box component="span" sx={{ color: 'text.secondary' }}>
              {names.length > 0 ? names.join(', ') : t('challenge.list.table.unused')}
            </Box>
          );
        },
      },
      {
        id: 'usage',
        header: t('challenge.list.table.usageCount'),
        align: 'right',
        sortKey: 'usage',
        render: (c) => (
          <Box component="span" sx={{ color: 'text.secondary' }}>
            {c.usage_count ?? 0}
          </Box>
        ),
      },
      {
        id: 'points',
        header: t('challenge.list.table.points'),
        align: 'right',
        sortKey: 'points',
        render: (c) =>
          typeof c.scoring_rules?.max_points === 'number' ? c.scoring_rules.max_points : 0,
      },
      {
        id: 'type',
        header: t('challenge.list.table.type'),
        sortKey: 'type',
        render: (c) => <Chip label={t(`challenge.types.${c.challenge_type}`)} size="small" />,
      },
      {
        id: 'duration',
        header: t('challenge.list.table.duration'),
        align: 'right',
        hideBelow: 'sm',
        sortKey: 'duration',
        render: (c) => c.time_estimate_minutes ?? 0,
      },
      {
        id: 'author',
        header: t('challenge.list.table.author'),
        hideBelow: 'md',
        sortKey: 'author',
        render: (c) => (
          <Box component="span" sx={{ color: 'text.secondary' }}>
            {c.created_by_display_name ?? '—'}
          </Box>
        ),
      },
      {
        id: 'date',
        header: t('challenge.list.table.date'),
        hideBelow: 'lg',
        sortKey: 'created_at',
        render: (c) => (
          <Box component="span" sx={{ color: 'text.secondary', whiteSpace: 'nowrap' }}>
            {fmtDate(c.created_at)}
          </Box>
        ),
      },
      {
        id: 'actions',
        header: t('challenge.list.table.actions'),
        align: 'right',
        actions: true,
        render: (c) =>
          c.is_deleted ? (
            <Box sx={{ whiteSpace: 'nowrap' }}>
              <Tooltip title={t('archive.restore')}>
                <IconButton
                  size="small"
                  color="primary"
                  aria-label={`${t('archive.restore')}: ${titleOf(c.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleRestore(c);
                  }}
                >
                  <RestoreFromTrashIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title={t('archive.deleteForever')}>
                <IconButton
                  size="small"
                  color="error"
                  aria-label={`${t('archive.deleteForever')}: ${titleOf(c.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setToDeleteForever(c);
                  }}
                >
                  <DeleteForeverIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
          ) : (
            <Box sx={{ whiteSpace: 'nowrap' }}>
              <Tooltip title={t('challenge.list.table.open')}>
                <IconButton
                  size="small"
                  aria-label={`${t('challenge.list.table.open')}: ${titleOf(c.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/challenges/${c.id}`);
                  }}
                >
                  <EditIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              {/* A complete copy to adapt, opened at once. */}
              <Tooltip title={t('library.duplicate')}>
                <IconButton
                  size="small"
                  aria-label={`${t('library.duplicate')}: ${titleOf(c.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleDuplicate(c.id);
                  }}
                >
                  <ContentCopyIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title={t('archive.archive')}>
                <IconButton
                  size="small"
                  color="error"
                  aria-label={`${t('archive.archive')}: ${titleOf(c.title)}`}
                  onClick={(e) => {
                    e.stopPropagation();
                    setToDelete(c);
                  }}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            </Box>
          ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, activeLang, navigate, stationNames, rallyeNames, handleRestore, handleDuplicate],
  );

  const filters = useMemo<DataTableFilter<Challenge>[]>(
    () => [
      {
        id: 'type',
        label: t('challenge.list.filter.type'),
        options: CHALLENGE_TYPES.map((ct) => ({ value: ct, label: t(`challenge.types.${ct}`) })),
      },
    ],
    [t],
  );

  const deleteStationNames = toDelete ? stationNames(toDelete) : [];

  return (
    <Box>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{ justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, mb: 1 }}
      >
        <Typography variant="h2" component="h1">
          {t('challenge.list.title')}
        </Typography>
        <Button variant="contained" onClick={() => navigate('/challenges/new')}>
          {t('challenge.list.createButton')}
        </Button>
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        {t('challenge.list.subtitle')}
      </Typography>

      <DataTable<Challenge>
        rows={challenges}
        columns={columns}
        getRowId={(c) => c.id}
        ariaLabel={t('challenge.list.title')}
        loading={loading}
        error={error}
        onRetry={reload}
        emptyMessage={
          <>
            <Typography variant="body1" color="text.secondary">
              {t('challenge.list.empty')}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t('challenge.list.emptyHint')}
            </Typography>
          </>
        }
        noMatchMessage={
          <Typography variant="body1" color="text.secondary">
            {t('challenge.list.noMatch')}
          </Typography>
        }
        searchable
        searchPlaceholder={t('challenge.list.search')}
        filters={filters}
        defaultSort={{ columnId: 'name', dir: 'asc' }}
        onRowClick={(c) => (c.is_deleted ? undefined : navigate(`/challenges/${c.id}`))}
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
        title={t('challenge.detail.archive')}
        message={
          deleteStationNames.length > 0
            ? t('challenge.detail.archiveInUseConfirm', {
                count: deleteStationNames.length,
                stations: deleteStationNames.join(', '),
              })
            : t('challenge.detail.archiveConfirm')
        }
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

export default ChallengeLibraryList;
