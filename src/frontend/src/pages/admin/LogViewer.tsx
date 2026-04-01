import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Chip,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import DownloadIcon from '@mui/icons-material/Download';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import { listLogs, logsExportUrl } from '../../api/admin';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { LogCategory, LogEntry } from '../../api/types';
import DataTable, {
  type DataTableColumn,
  type DataTableFilter,
  type DataTableQuery,
} from '../../components/DataTable';

const ALL_CATEGORIES: LogCategory[] = ['audit', 'app', 'error', 'access'];

const CATEGORY_COLORS: Record<LogCategory, 'default' | 'primary' | 'error' | 'warning'> = {
  audit: 'primary',
  app: 'default',
  error: 'error',
  access: 'warning',
};

/**
 * The entry of a media purge also carries the kind of media and the number of
 * files. The API sends both for `org.media_purged` only.
 */
export type LogRow = LogEntry & { media_type?: string | null; purged?: number | null };

const LogViewer: React.FC = () => {
  const { t, i18n } = useTranslation('admin');
  const { t: tCommon } = useTranslation('common');
  const { user } = useAuth();
  const orgSlug = user?.organization_slug ?? '';

  const [entries, setEntries] = useState<LogRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  // Server-side date-range filters (bound volume); applied on demand.
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [appliedFromDate, setAppliedFromDate] = useState('');
  const [appliedToDate, setAppliedToDate] = useState('');

  // Server mode: the table emits search, category, page and sort, and the
  // server filters the whole period. The export reads the same query, so it
  // holds exactly what the view filters.
  const [query, setQuery] = useState<DataTableQuery | null>(null);
  // Drops responses that arrive after a newer request was issued.
  const requestRef = useRef(0);

  const [autoRefresh, setAutoRefresh] = useState(false);

  // The event labels exist only in the interface. A search for "Bereinig"
  // must find the media purge, so the server also gets the keys of
  // every event whose label in the current language contains the search.
  const eventKeysForSearch = useCallback(
    (search: string): string[] => {
      const needle = search.trim().toLowerCase();
      if (!needle) return [];
      const labels: unknown = t('dashboard.auditEvents', { returnObjects: true });
      const keys: string[] = [];
      const walk = (node: unknown, prefix: string) => {
        if (!node || typeof node !== 'object') return;
        for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
          if (typeof value === 'string') {
            if (value.toLowerCase().includes(needle)) keys.push(prefix + key);
          } else {
            walk(value, `${prefix}${key}.`);
          }
        }
      };
      walk(labels, '');
      return keys;
    },
    [t],
  );

  const viewFilters = useCallback(
    (q: DataTableQuery | null) => {
      const keys = eventKeysForSearch(q?.search ?? '');
      return {
        category: q?.filters.category ? [q.filters.category as LogCategory] : undefined,
        search: q?.search || undefined,
        search_event: keys.length > 0 ? keys : undefined,
        from_date: appliedFromDate || undefined,
        to_date: appliedToDate || undefined,
      };
    },
    [appliedFromDate, appliedToDate, eventKeysForSearch],
  );

  const load = useCallback(async () => {
    if (!orgSlug || !query) return;
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    try {
      const data = await listLogs(orgSlug, {
        ...viewFilters(query),
        skip: query.page * query.rowsPerPage,
        limit: query.rowsPerPage,
        order: query.sort?.dir,
      });
      if (requestId !== requestRef.current) return;
      setEntries(data.items);
      setTotal(data.total);
    } catch (err) {
      if (requestId !== requestRef.current) return;
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, [orgSlug, query, viewFilters]);

  useEffect(() => {
    void load();
  }, [load]);

  // Auto-refresh polling — setInterval with cleanup; loadRef avoids re-arming on every load change.
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      void loadRef.current();
    }, 10000);
    return () => clearInterval(id);
  }, [autoRefresh]);

  const handleApplyFilters = () => {
    setAppliedFromDate(fromDate);
    setAppliedToDate(toDate);
  };

  const handleResetFilters = () => {
    setFromDate('');
    setToDate('');
    setAppliedFromDate('');
    setAppliedToDate('');
  };

  // A plain link: the browser streams the file to disk, however long the period.
  const handleExportCsv = () => {
    const a = document.createElement('a');
    a.href = logsExportUrl(orgSlug, viewFilters(query));
    a.download = `audit-logs-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const formatTimestamp = (ts: string | null) => {
    if (!ts) return '—';
    return new Date(ts).toLocaleString(i18n.language);
  };

  const formatActor = (actor: Record<string, unknown> | null) => {
    if (!actor) return '—';
    const name = actor.display_name ?? actor.email;
    if (name) return String(name);
    const id = actor.user_id ?? actor.id;
    const role = actor.role;
    if (id && role) return `${String(role)} (${String(id).slice(0, 8)}…)`;
    if (id) return `${String(id).slice(0, 8)}…`;
    if (role) return String(role);
    return '—';
  };

  // "Station · 01a1f112…" in place of {"entity":"station","id":"01a1f112-…"}.
  const formatTarget = (target: Record<string, unknown> | null) => {
    if (!target) return '—';
    const kind = target.entity ?? target.type;
    const parts: string[] = [];
    if (kind) parts.push(String(kind));
    if (target.key) parts.push(String(target.key));
    if (target.id) parts.push(`${String(target.id).slice(0, 8)}…`);
    return parts.length > 0 ? parts.join(' · ') : JSON.stringify(target);
  };

  const eventLabel = (event: string) => t(`dashboard.auditEvents.${event}`, { defaultValue: event });

  // "Fotos: 12 Dateien gelöscht." for a media purge, in the words of the
  // purge itself (organization settings). Null for every other entry.
  const purgeDetail = (entry: LogRow): string | null => {
    if (entry.event !== 'org.media_purged') return null;
    const parts: string[] = [];
    if (entry.media_type) {
      parts.push(t(`org.media.type.${entry.media_type}`, { defaultValue: entry.media_type }));
    }
    if (typeof entry.purged === 'number') parts.push(t('org.media.success', { count: entry.purged }));
    return parts.length > 0 ? parts.join(': ') : null;
  };

  const rowId = (entry: LogRow) => entry.request_id ?? `${entry.timestamp ?? ''}-${entry.event ?? ''}`;

  const toggleExpanded = (id: string) =>
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const columns: DataTableColumn<LogRow>[] = [
    {
      id: 'timestamp',
      header: t('logs.table.timestamp'),
      render: (e) => {
        const id = rowId(e);
        const hasDetails = e.changes !== null || e.integrity_hash !== null;
        const expanded = expandedRows.has(id);
        return (
          <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', whiteSpace: 'nowrap' }}>
            {hasDetails && (
              <IconButton
                size="small"
                aria-label={expanded ? t('logs.collapse') : t('logs.expand')}
                aria-expanded={expanded}
                onClick={(ev) => {
                  ev.stopPropagation();
                  toggleExpanded(id);
                }}
              >
                {expanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
              </IconButton>
            )}
            <span>{formatTimestamp(e.timestamp)}</span>
          </Stack>
        );
      },
      sortValue: (e) => e.timestamp ?? '',
      sortKey: 'timestamp',
    },
    {
      id: 'category',
      header: t('logs.table.category'),
      render: (e) =>
        e.category ? (
          <Chip
            label={t(`logs.categories.${e.category}`)}
            color={CATEGORY_COLORS[e.category as LogCategory] ?? 'default'}
            size="small"
          />
        ) : null,
    },
    {
      id: 'event',
      header: t('logs.table.event'),
      // The label an admin can read, with the raw key under it: the key is
      // what support and the audit files speak, the label is what a search
      // for "Bereinig" must find. The server search gets the keys of the
      // matching labels, see viewFilters.
      render: (e) =>
        e.event ? (
          <Box>
            <Box component="span" sx={{ display: 'block' }}>{eventLabel(e.event)}</Box>
            {purgeDetail(e) && (
              <Box component="span" sx={{ display: 'block', fontSize: '0.8rem', color: 'text.secondary' }}>
                {purgeDetail(e)}
              </Box>
            )}
            {eventLabel(e.event) !== e.event && (
              <Box
                component="span"
                sx={{ display: 'block', fontFamily: 'monospace', fontSize: '0.7rem', color: 'text.secondary' }}
              >
                {e.event}
              </Box>
            )}
          </Box>
        ) : (
          '—'
        ),
    },
    {
      id: 'actor',
      header: t('logs.table.actor'),
      render: (e) => formatActor(e.actor),
    },
    {
      id: 'target',
      header: t('logs.table.target'),
      render: (e) => formatTarget(e.target),
    },
    {
      id: 'requestId',
      header: t('logs.table.requestId'),
      hideBelow: 'md',
      render: (e) => (
        <Box component="span" sx={{ fontFamily: 'monospace', fontSize: '0.75rem', opacity: 0.7 }}>
          {e.request_id ? e.request_id.slice(0, 8) + '…' : '—'}
        </Box>
      ),
    },
  ];

  const filters: DataTableFilter<LogRow>[] = [
    {
      id: 'category',
      label: t('logs.filters.category'),
      options: ALL_CATEGORIES.map((cat) => ({ value: cat, label: t(`logs.categories.${cat}`) })),
    },
  ];

  const extraFilters = (
    <>
      <Stack
        direction="row"
        spacing={1.5}
        useFlexGap
        sx={{ alignItems: 'center', flexWrap: 'wrap' }}
      >
        <TextField
          label={t('logs.filters.fromDate')}
          type="datetime-local"
          value={fromDate}
          onChange={(e) => setFromDate(e.target.value)}
          size="small"
          slotProps={{ inputLabel: { shrink: true } }}
          sx={{ minWidth: 200 }}
        />
        <TextField
          label={t('logs.filters.toDate')}
          type="datetime-local"
          value={toDate}
          onChange={(e) => setToDate(e.target.value)}
          size="small"
          slotProps={{ inputLabel: { shrink: true } }}
          sx={{ minWidth: 200 }}
        />
        <Button variant="contained" size="small" onClick={handleApplyFilters}>
          {t('logs.filters.apply')}
        </Button>
        <Button variant="outlined" size="small" onClick={handleResetFilters}>
          {t('logs.filters.reset')}
        </Button>
      </Stack>
      <FormControlLabel
        control={
          <Switch
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
            size="small"
          />
        }
        label={t('logs.autoRefresh')}
        sx={{ ml: 0 }}
      />
    </>
  );

  const toolbarActions = (
    <Tooltip title={t('logs.exportHint')} describeChild>
      <Button variant="outlined" startIcon={<DownloadIcon />} onClick={handleExportCsv} size="small">
        {t('logs.export')}
      </Button>
    </Tooltip>
  );

  const renderExpanded = (entry: LogRow) => (
    <Box
      sx={{
        p: 2,
        bgcolor: 'grey.50',
        borderBottom: 1,
        borderColor: 'divider',
      }}
    >
      {entry.changes !== null && (
        <Box sx={{ mb: 1 }}>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            Changes
          </Typography>
          <Box
            component="pre"
            sx={{
              fontFamily: 'monospace',
              fontSize: '0.75rem',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
              mt: 0.5,
            }}
          >
            {JSON.stringify(entry.changes, null, 2)}
          </Box>
        </Box>
      )}
      {entry.integrity_hash && (
        <Box>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {t('logs.table.integrityHash')}
          </Typography>
          <Box
            component="pre"
            sx={{
              fontFamily: 'monospace',
              fontSize: '0.75rem',
              wordBreak: 'break-all',
              mt: 0.5,
            }}
          >
            {entry.integrity_hash}
          </Box>
        </Box>
      )}
    </Box>
  );

  return (
    <Box>
      <Typography variant="h2" component="h1" sx={{ mb: 3 }}>
        {t('logs.title')}
      </Typography>
      <DataTable<LogRow>
        rows={entries}
        columns={columns}
        getRowId={rowId}
        loading={loading}
        error={error}
        onRetry={load}
        emptyMessage={t('logs.empty')}
        noMatchMessage={tCommon('table.noMatch')}
        searchable
        searchPlaceholder={t('logs.filters.searchPlaceholder')}
        filters={filters}
        extraFilters={extraFilters}
        toolbarActions={toolbarActions}
        defaultSort={{ columnId: 'timestamp', dir: 'desc' }}
        paginate
        server={{ total, onQueryChange: setQuery }}
        renderExpanded={renderExpanded}
        isRowExpanded={(e) => expandedRows.has(rowId(e))}
        ariaLabel={t('logs.tableLabel')}
      />
    </Box>
  );
};

export default LogViewer;
