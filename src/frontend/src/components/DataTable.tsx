import React, { useEffect, useMemo, useRef, useState } from 'react';
import { visuallyHiddenSx } from '../styles/visuallyHidden';
import { useTranslation } from 'react-i18next';
import {
  Box,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  TextField,
  Typography,
} from '@mui/material';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import ErrorAlert from './ErrorAlert';
import LoadingSpinner from './LoadingSpinner';
import { useDebouncedValue } from '../hooks/useDebouncedValue';

export type SortDir = 'asc' | 'desc';
type Breakpoint = 'sm' | 'md' | 'lg' | 'xl';

/** What the table asks the page to load. Emitted whenever it changes. */
export interface DataTableQuery {
  search: string;
  /** Active filters only — the "all" sentinel value is left out. */
  filters: Record<string, string>;
  /** `key` is the sorted column's `sortKey`. */
  sort: { key: string; dir: SortDir } | null;
  /** Zero-based. */
  page: number;
  rowsPerPage: number;
}

interface DataTableServerMode {
  /** Total from the API response — the table pages with it, not with `rows.length`. */
  total: number;
  onQueryChange: (query: DataTableQuery) => void;
  /** Default 300 ms. */
  searchDebounceMs?: number;
}

export interface DataTableColumn<T> {
  /** Stable column id (used for sort state + React keys). */
  id: string;
  /** Header label. */
  header: string;
  /** Cell renderer. */
  render: (row: T) => React.ReactNode;
  /** Provide to make the column sortable; returns the comparable value. */
  sortValue?: (row: T) => string | number | boolean | null | undefined;
  /** Server mode: the column is sortable if and only if this is set. */
  sortKey?: string;
  /** Provide to include this column's text in the global search. */
  searchValue?: (row: T) => string;
  align?: 'left' | 'right';
  /** Hide the column below this breakpoint (responsive). */
  hideBelow?: Breakpoint;
  /** Mark the actions column so its header label is visually hidden. */
  actions?: boolean;
  /** Keep the column at the right edge while the table scrolls sideways. */
  stickyEnd?: boolean;
  width?: number | string;
}

export interface DataTableFilter<T> {
  id: string;
  label: string;
  options: { value: string; label: string }[];
  /** Client-mode predicate. `value` is never the sentinel "all". Never called in server mode. */
  match?: (row: T, value: string) => boolean;
}

interface DataTableProps<T> {
  rows: T[];
  columns: DataTableColumn<T>[];
  getRowId: (row: T) => string;

  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  emptyMessage?: React.ReactNode;

  /** Toolbar. */
  searchable?: boolean;
  searchPlaceholder?: string;
  /** Declarative client-mode filters rendered as selects in the toolbar. */
  filters?: DataTableFilter<T>[];
  /** Extra toolbar controls (e.g. custom date-range filters), left side. */
  extraFilters?: React.ReactNode;
  /** Toolbar actions (create / export), right side. */
  toolbarActions?: React.ReactNode;

  defaultSort?: { columnId: string; dir: SortDir };
  paginate?: boolean;
  rowsPerPageOptions?: number[];
  defaultRowsPerPage?: number;

  /** Opt into server mode: the table owns the toolbar state, the page owns the data. */
  server?: DataTableServerMode;
  /** Server mode: shown when `server.total` is 0 while a search or filter is active. */
  noMatchMessage?: React.ReactNode;

  onRowClick?: (row: T) => void;
  /** Optional expandable detail row. */
  renderExpanded?: (row: T) => React.ReactNode;
  isRowExpanded?: (row: T) => boolean;

  ariaLabel: string;
  /** Hide the internal toolbar entirely (e.g. when the page renders its own). */
  hideToolbar?: boolean;
}

const cmp = (a: unknown, b: unknown): number => {
  if (a == null && b == null) return 0;
  if (a == null) return -1;
  if (b == null) return 1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
};

const hideSx = (bp?: Breakpoint) =>
  bp ? { display: { xs: 'none', [bp]: 'table-cell' } } : undefined;

/** A column that stays at the right edge of a table that scrolls sideways,
 *  so the row actions are reachable without scrolling to them. */
const stickyEndSx = (sticky?: boolean) =>
  sticky
    ? ({ position: 'sticky', right: 0, zIndex: 1, bgcolor: 'background.paper' } as const)
    : undefined;


const sameFilters = (a: Record<string, string>, b: Record<string, string>) =>
  Object.keys(a).length === Object.keys(b).length &&
  Object.entries(a).every(([k, v]) => b[k] === v);

/**
 * Reusable, accessible data table with global search, column sorting, declarative
 * filters and pagination — all client-side over the supplied `rows`. Pages keep
 * loading their own data and pass it in; columns declare how to render / sort /
 * search each field. See StationLibraryList for a typical usage.
 *
 * With the `server` prop the table flips to server mode. It owns the toolbar
 * state but no longer filters, sorts or slices. It emits a `DataTableQuery`
 * on every change (search debounced) and pages with `server.total`.
 */
function DataTable<T>({
  rows,
  columns,
  getRowId,
  loading,
  error,
  onRetry,
  emptyMessage,
  searchable,
  searchPlaceholder,
  filters,
  extraFilters,
  toolbarActions,
  defaultSort,
  paginate = true,
  rowsPerPageOptions = [10, 25, 50, 100],
  defaultRowsPerPage = 25,
  server,
  noMatchMessage,
  onRowClick,
  renderExpanded,
  isRowExpanded,
  ariaLabel,
  hideToolbar,
}: DataTableProps<T>) {
  const { t } = useTranslation('common');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<{ columnId: string; dir: SortDir } | null>(defaultSort ?? null);
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(defaultRowsPerPage);

  const debouncedSearch = useDebouncedValue(search.trim(), server?.searchDebounceMs ?? 300);

  /** Active filters only — the "all" sentinel value is left out. */
  const activeFilters = useMemo(
    () => Object.fromEntries(Object.entries(filterValues).filter(([, v]) => v && v !== 'all')),
    [filterValues],
  );

  // Latest emission callback, held in a ref so a new identity from the page
  // (inline object or lambda) cannot re-trigger the emission effect below.
  const onQueryChangeRef = useRef(server?.onQueryChange);
  useEffect(() => {
    onQueryChangeRef.current = server?.onQueryChange;
  });
  const lastQueryRef = useRef<DataTableQuery | null>(null);

  useEffect(() => {
    if (!server) {
      lastQueryRef.current = null;
      return;
    }
    const sortCol = sort ? columns.find((c) => c.id === sort.columnId) : undefined;
    const querySort = sort && sortCol?.sortKey ? { key: sortCol.sortKey, dir: sort.dir } : null;
    const last = lastQueryRef.current;
    // A search or filter change resets the page in the same emission; a shrunk
    // total pulls an out-of-range page back onto the last page.
    const maxPage = Math.max(0, Math.ceil(server.total / rowsPerPage) - 1);
    const queryPage =
      last && (last.search !== debouncedSearch || !sameFilters(last.filters, activeFilters))
        ? 0
        : Math.min(page, maxPage);
    const query: DataTableQuery = {
      search: debouncedSearch,
      filters: activeFilters,
      sort: querySort,
      page: queryPage,
      rowsPerPage,
    };
    if (
      last &&
      last.search === query.search &&
      sameFilters(last.filters, query.filters) &&
      last.sort?.key === query.sort?.key &&
      last.sort?.dir === query.sort?.dir &&
      last.page === query.page &&
      last.rowsPerPage === query.rowsPerPage
    ) {
      return;
    }
    lastQueryRef.current = query;
    if (queryPage !== page) setPage(queryPage);
    onQueryChangeRef.current?.(query);
  }, [server, debouncedSearch, activeFilters, sort, page, rowsPerPage, columns]);

  // Server mode unmounts the whole table while the rows reload, so a sort
  // triggered by keyboard would drop focus to the document body. We remember
  // the column of a keyboard sort and give it its focus back once the reload
  // finished. A mouse sort sets nothing and stays unchanged.
  const sortControlRefs = useRef<Record<string, HTMLElement | null>>({});
  const keyboardSortRef = useRef<string | null>(null);
  const reloadStartedRef = useRef(false);

  useEffect(() => {
    if (!server) return;
    const columnId = keyboardSortRef.current;
    if (!columnId) return;
    if (loading) {
      reloadStartedRef.current = true;
      return;
    }
    // The reload has not begun yet — the control still holds focus itself.
    if (!reloadStartedRef.current) return;
    reloadStartedRef.current = false;
    keyboardSortRef.current = null;
    sortControlRefs.current[columnId]?.focus();
  }, [server, loading]);

  const searchableCols = useMemo(() => columns.filter((c) => c.searchValue), [columns]);
  const showSearch = server
    ? searchable !== false
    : (searchable ?? searchableCols.length > 0) && searchableCols.length > 0;

  // Sortable columns that are hidden below their breakpoint lose their header
  // sort control on narrow screens. Paired with the breakpoint so the toolbar
  // twin below can show exactly there.
  const hiddenSortCols = useMemo(
    () =>
      columns.flatMap((c) =>
        c.hideBelow && (server ? c.sortKey : c.sortValue)
          ? [{ col: c, below: c.hideBelow }]
          : [],
      ),
    [columns, server],
  );

  // Server mode never filters or sorts `rows` itself — the page already did
  // that server-side, and `match`/`sortValue` must not run over server rows.
  const filtered = useMemo(() => {
    if (server) return rows;
    let out = rows;
    const q = search.trim().toLowerCase();
    if (q && searchableCols.length > 0) {
      out = out.filter((row) =>
        searchableCols.some((c) => c.searchValue!(row).toLowerCase().includes(q)),
      );
    }
    for (const f of filters ?? []) {
      const v = filterValues[f.id];
      if (v && v !== 'all') out = out.filter((row) => f.match?.(row, v) ?? true);
    }
    return out;
  }, [server, rows, search, searchableCols, filters, filterValues]);

  const sorted = useMemo(() => {
    if (server) return filtered;
    if (!sort) return filtered;
    const col = columns.find((c) => c.id === sort.columnId);
    if (!col?.sortValue) return filtered;
    const dir = sort.dir === 'asc' ? 1 : -1;
    // copy before sort so we never mutate the parent's array
    return [...filtered].sort((a, b) => dir * cmp(col.sortValue!(a), col.sortValue!(b)));
  }, [server, filtered, sort, columns]);

  const total = server ? server.total : sorted.length;
  // Keep the page in range when the filtered set shrinks.
  const maxPage = Math.max(0, Math.ceil(total / rowsPerPage) - 1);
  const safePage = Math.min(page, maxPage);
  // In server mode the rows arrive already filtered, sorted and paged.
  const visibleRows = server
    ? rows
    : paginate
      ? sorted.slice(safePage * rowsPerPage, safePage * rowsPerPage + rowsPerPage)
      : sorted;

  const toggleSort = (col: DataTableColumn<T>) => {
    if (server ? !col.sortKey : !col.sortValue) return;
    setSort((prev) =>
      prev?.columnId === col.id
        ? { columnId: col.id, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { columnId: col.id, dir: 'asc' },
    );
  };

  // Client mode swaps the whole table for the spinner; server mode keeps the
  // toolbar mounted (focus and value of the search field survive the load).
  if (loading && !server) return <LoadingSpinner />;

  // Toolbar twins of the header sort controls, one per sortable column that is
  // hidden below its breakpoint. Each shows exactly below that breakpoint.
  // From the breakpoint up the column carries its own header control and the
  // twin is display:none, so the wide layout is unchanged. The twin
  // lives in the toolbar, which server mode keeps mounted across reloads, so
  // keyboard focus survives on its own (no ref restore needed). Its accessible
  // name states the sort the next activation will apply, since unlike a header
  // cell it has no aria-sort context around it.
  const narrowSortControls = hiddenSortCols.map(({ col, below }) => {
    const active = sort?.columnId === col.id;
    const nextDir: SortDir = !active || sort!.dir === 'desc' ? 'asc' : 'desc';
    return (
      <Box
        key={`narrow-sort-${col.id}`}
        sx={{ display: { xs: 'flex', [below]: 'none' }, alignItems: 'center' }}
      >
        <TableSortLabel
          active={active}
          direction={active ? sort!.dir : 'asc'}
          onClick={() => toggleSort(col)}
          aria-label={`${col.header}: ${
            nextDir === 'asc' ? t('table.sortAscending') : t('table.sortDescending')
          }`}
          sx={{
            border: 1,
            borderColor: 'divider',
            borderRadius: 1,
            px: 1.5,
            py: 0.25,
          }}
        >
          {col.header}
        </TableSortLabel>
      </Box>
    );
  });

  const toolbar = hideToolbar ? null : (
    <Stack
      direction={{ xs: 'column', md: 'row' }}
      spacing={1.5}
      useFlexGap
      sx={{ mb: 2, alignItems: { xs: 'stretch', md: 'center' }, flexWrap: 'wrap' }}
    >
      {showSearch && (
        <TextField
          label={t('table.search')}
          placeholder={searchPlaceholder}
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            // In server mode the page reset rides along with the debounced
            // emission — resetting here would emit the old search at once.
            if (!server) setPage(0);
          }}
          size="small"
          sx={{ minWidth: 220 }}
        />
      )}
      {(filters ?? []).map((f) => (
        <FormControl key={f.id} size="small" sx={{ minWidth: 160 }}>
          <InputLabel id={`dt-filter-${f.id}`}>{f.label}</InputLabel>
          <Select
            labelId={`dt-filter-${f.id}`}
            label={f.label}
            value={filterValues[f.id] ?? 'all'}
            onChange={(e) => {
              setFilterValues((prev) => ({ ...prev, [f.id]: e.target.value }));
              setPage(0);
            }}
          >
            <MenuItem value="all">{t('table.allFilter')}</MenuItem>
            {f.options.map((o) => (
              <MenuItem key={o.value} value={o.value}>
                {o.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      ))}
      {extraFilters}
      {narrowSortControls}
      {toolbarActions && <Box sx={{ flexGrow: 1 }} />}
      {toolbarActions}
    </Stack>
  );

  // With a search or filter active and nothing found, the no-match message
  // replaces the plain empty message.
  // In client mode a page can opt in the same way: when it passes
  // `noMatchMessage`, rows exist and the search or a filter left none, that
  // message shows. Without it the page's own empty message stays, which other
  // pages and their E2E tests rely on.
  const emptyStateMessage = server
    ? debouncedSearch !== '' || Object.keys(activeFilters).length > 0
      ? noMatchMessage
      : emptyMessage
    : noMatchMessage && rows.length > 0 && filtered.length === 0
      ? noMatchMessage
      : emptyMessage;

  return (
    <Box>
      {toolbar}
      {error && <ErrorAlert message={error} onRetry={onRetry} />}
      {server && !loading && (
        <Box component="span" role="status" aria-live="polite" sx={visuallyHiddenSx}>
          {t('table.resultCount', { count: total })}
        </Box>
      )}
      {!error && loading ? (
        <LoadingSpinner />
      ) : !error && total === 0 ? (
        <Paper variant="outlined" sx={{ py: 6, textAlign: 'center' }}>
          {typeof emptyStateMessage === 'string' || emptyStateMessage == null ? (
            <Typography variant="body1" color="text.secondary">
              {emptyStateMessage ?? t('table.noResults')}
            </Typography>
          ) : (
            emptyStateMessage
          )}
        </Paper>
      ) : (
        !error && (
          <TableContainer
            component={Paper}
            variant="outlined"
            // A sideways-scrolling region must be reachable by keyboard —
            // axe's scrollable-region-focusable — so it takes focus and a
            // name; the name is the table's own ariaLabel.
            role="region"
            tabIndex={0}
            aria-label={ariaLabel}
            sx={{
              overflowX: 'auto',
              // Makes this the containing block of the visually hidden header
              // of an actions column. Without it that absolutely positioned
              // span escaped the scroll container and widened the whole page
              // by the table's overflow: two sideways scrollbars, and the last
              // columns outside the window.
              position: 'relative',
            }}
          >
            <Table size="small" aria-label={ariaLabel}>
              <TableHead>
                <TableRow>
                  {columns.map((col) => {
                    const active = sort?.columnId === col.id;
                    return (
                      <TableCell
                        key={col.id}
                        scope="col"
                        align={col.align}
                        sortDirection={active ? sort!.dir : false}
                        sx={{ width: col.width, ...hideSx(col.hideBelow), ...stickyEndSx(col.stickyEnd) }}
                      >
                        {(server ? col.sortKey : col.sortValue) ? (
                          <TableSortLabel
                            active={active}
                            direction={active ? sort!.dir : 'asc'}
                            ref={(el: HTMLElement | null) => {
                              sortControlRefs.current[col.id] = el;
                            }}
                            onClick={() => toggleSort(col)}
                            onKeyDown={(e) => {
                              // MUI turns Enter / Space on the span into the
                              // click above; mark it so the focus comes back.
                              if (server && (e.key === 'Enter' || e.key === ' ')) {
                                keyboardSortRef.current = col.id;
                              }
                            }}
                          >
                            {col.actions ? (
                              <Box component="span" sx={{ position: 'absolute', overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
                                {col.header}
                              </Box>
                            ) : (
                              col.header
                            )}
                          </TableSortLabel>
                        ) : col.actions ? (
                          <Box component="span" sx={{ position: 'absolute', overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
                            {col.header}
                          </Box>
                        ) : (
                          col.header
                        )}
                      </TableCell>
                    );
                  })}
                </TableRow>
              </TableHead>
              <TableBody>
                {visibleRows.map((row) => {
                  const expanded = isRowExpanded?.(row) ?? false;
                  return (
                    <React.Fragment key={getRowId(row)}>
                      <TableRow
                        hover
                        onClick={onRowClick ? () => onRowClick(row) : undefined}
                        sx={onRowClick ? { cursor: 'pointer' } : undefined}
                      >
                        {columns.map((col) => (
                          <TableCell key={col.id} align={col.align} sx={{ ...hideSx(col.hideBelow), ...stickyEndSx(col.stickyEnd) }}>
                            {col.render(row)}
                          </TableCell>
                        ))}
                      </TableRow>
                      {renderExpanded && expanded && (
                        <TableRow>
                          <TableCell colSpan={columns.length} sx={{ py: 0 }}>
                            {renderExpanded(row)}
                          </TableCell>
                        </TableRow>
                      )}
                    </React.Fragment>
                  );
                })}
              </TableBody>
            </Table>
            {paginate && (
              <Stack
                direction="row"
                spacing={2}
                sx={{ alignItems: 'center', justifyContent: 'flex-end', px: 2, py: 1 }}
              >
                <Typography variant="caption" color="text.secondary">
                  {t('table.rowsPerPage')}
                </Typography>
                <Select
                  size="small"
                  variant="standard"
                  value={rowsPerPage}
                  onChange={(e) => {
                    setRowsPerPage(Number(e.target.value));
                    setPage(0);
                  }}
                  inputProps={{ 'aria-label': t('table.rowsPerPage') }}
                  sx={{ minWidth: 56 }}
                >
                  {rowsPerPageOptions.map((n) => (
                    <MenuItem key={n} value={n}>
                      {n}
                    </MenuItem>
                  ))}
                </Select>
                <Typography variant="caption" color="text.secondary">
                  {t('table.range', {
                    from: total === 0 ? 0 : safePage * rowsPerPage + 1,
                    to: Math.min((safePage + 1) * rowsPerPage, total),
                    total,
                  })}
                </Typography>
                <IconButton
                  size="small"
                  aria-label={t('pagination.previous')}
                  disabled={safePage <= 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  <ChevronLeftIcon fontSize="small" />
                </IconButton>
                <IconButton
                  size="small"
                  aria-label={t('pagination.next')}
                  disabled={safePage >= maxPage}
                  onClick={() => setPage((p) => Math.min(maxPage, p + 1))}
                >
                  <ChevronRightIcon fontSize="small" />
                </IconButton>
              </Stack>
            )}
          </TableContainer>
        )
      )}
    </Box>
  );
}

export default DataTable;
