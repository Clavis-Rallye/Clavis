import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { localizedText } from '../i18n/contentLocales';
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Radio,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material';
import { listStations } from '../api/content';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/useAuth';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import type { MultilingualString, Station } from '../api/types';
import ErrorAlert from './ErrorAlert';
import LoadingSpinner from './LoadingSpinner';

interface ImportStationDialogProps {
  open: boolean;
  /** Station ids already linked to the rallye — shown as disabled. */
  linkedStationIds: string[];
  onCancel: () => void;
  onImport: (stationId: string) => Promise<void> | void;
}

// The native input of the radio is transparent, so the global focus ring has
// nothing to draw on. The ring goes on the wrapper MUI marks for keyboard focus.
const focusRingSx = {
  '&.Mui-focusVisible': {
    outline: '2px solid',
    outlineColor: 'primary.main',
    outlineOffset: -4,
  },
} as const;

const ImportStationDialog: React.FC<ImportStationDialogProps> = ({
  open,
  linkedStationIds,
  onCancel,
  onImport,
}) => {
  const { user } = useAuth();
  const { t, i18n } = useTranslation('content');
  const orgSlug = user?.organization_slug;

  const [stations, setStations] = useState<Station[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Drops responses that arrive after a newer request was issued.
  const requestRef = useRef(0);

  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';
  const titleOf = (ml: MultilingualString) => localizedText(ml, activeLang);

  // The API narrows by the same rule as the library page (titles in any
  // language plus the address). The rows it returns render as they arrive, in
  // the API's order.
  const debouncedSearch = useDebouncedValue(search, 300);

  const load = useCallback(
    async (q: string) => {
      if (!orgSlug) return;
      const requestId = ++requestRef.current;
      setLoading(true);
      setError(null);
      try {
        const resp = await listStations(orgSlug, { q, limit: 200 });
        if (requestId !== requestRef.current) return;
        setStations(resp.items);
      } catch (err) {
        if (requestId !== requestRef.current) return;
        setError(err instanceof ApiError ? err.detail : String(err));
      } finally {
        if (requestId === requestRef.current) setLoading(false);
      }
    },
    [orgSlug],
  );

  useEffect(() => {
    if (open) void load(debouncedSearch);
  }, [open, debouncedSearch, load]);

  // Reset on close, so reopening starts from an empty (and already settled)
  // search instead of re-issuing the previous query.
  useEffect(() => {
    if (!open) {
      setSearch('');
      setSelected(null);
    }
  }, [open]);

  const linked = useMemo(() => new Set(linkedStationIds), [linkedStationIds]);

  // The list is one radio group: its name is the dialog's title, the name of
  // each radio is the title of its row. The ids tie the three together.
  const baseId = useId();
  const titleId = `${baseId}-title`;

  const handleImport = async () => {
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    try {
      await onImport(selected);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setSubmitting(false);
      return;
    }
    setSubmitting(false);
  };

  return (
    <Dialog
      open={open}
      onClose={submitting ? undefined : onCancel}
      maxWidth="md"
      fullWidth
      aria-labelledby={titleId}
    >
      <DialogTitle id={titleId}>{t('station.import.title')}</DialogTitle>
      <DialogContent>
        <TextField
          label={t('station.import.search')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          size="small"
          fullWidth
          sx={{ mb: 2, mt: 1 }}
        />
        {error && <ErrorAlert message={error} />}
        {loading ? (
          <LoadingSpinner />
        ) : stations.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
            {debouncedSearch.trim()
              ? t('station.list.noMatch')
              : t('station.import.empty')}
          </Typography>
        ) : (
          <TableContainer role="radiogroup" aria-labelledby={titleId}>
            <Table size="small" aria-label={t('station.import.title')}>
              <TableHead>
                <TableRow>
                  {/* The radio column has no heading of its own: a plain cell,
                      not an empty column header. */}
                  <TableCell component="td" padding="checkbox" />
                  <TableCell scope="col">{t('station.list.table.name')}</TableCell>
                  <TableCell scope="col" sx={{ display: { xs: 'none', sm: 'table-cell' } }}>
                    {t('station.list.table.address')}
                  </TableCell>
                  <TableCell scope="col">{t('station.list.table.usage')}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {stations.map((s) => {
                  const isLinked = linked.has(s.id);
                  const rowTitleId = `${baseId}-row-${s.id}-title`;
                  const linkedNoteId = `${baseId}-row-${s.id}-linked`;
                  const rallyeTitles = (s.usage?.rallye_titles ?? [])
                    .map((rt) => titleOf(rt))
                    .filter(Boolean);
                  return (
                    <TableRow
                      key={s.id}
                      hover={!isLinked}
                      selected={selected === s.id}
                      onClick={() => !isLinked && setSelected(s.id)}
                      sx={{
                        cursor: isLinked ? 'default' : 'pointer',
                        opacity: isLinked ? 0.5 : 1,
                      }}
                      aria-disabled={isLinked}
                    >
                      <TableCell padding="checkbox">
                        {/* A native radio: Tab reaches the group, the arrow
                            keys move the choice, Space chooses. Enter chooses
                            too. A click lands on the row as well and sets the
                            same id. */}
                        <Radio
                          name={`${baseId}-choice`}
                          value={s.id}
                          checked={selected === s.id}
                          disabled={isLinked}
                          onChange={() => setSelected(s.id)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') setSelected(s.id);
                          }}
                          slotProps={{
                            input: {
                              'aria-labelledby': rowTitleId,
                              'aria-describedby': isLinked ? linkedNoteId : undefined,
                            },
                          }}
                          sx={focusRingSx}
                        />
                      </TableCell>
                      <TableCell sx={{ fontWeight: 600 }}>
                        <span id={rowTitleId}>{titleOf(s.title) || s.id}</span>
                        {isLinked && (
                          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                            (<span id={linkedNoteId}>{t('station.import.alreadyLinked')}</span>)
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell
                        sx={{ display: { xs: 'none', sm: 'table-cell' }, color: 'text.secondary' }}
                      >
                        {s.address ?? '—'}
                      </TableCell>
                      <TableCell sx={{ color: 'text.secondary' }}>
                        {rallyeTitles.length > 0
                          ? rallyeTitles.join(', ')
                          : t('station.list.table.unused')}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={submitting}>
          {t('actions.cancel', { ns: 'common' })}
        </Button>
        <Button
          variant="contained"
          onClick={handleImport}
          disabled={!selected || submitting}
        >
          {t('station.import.submit')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ImportStationDialog;
