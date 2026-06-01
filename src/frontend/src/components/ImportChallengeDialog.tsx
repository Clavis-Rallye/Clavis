import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { localizedText } from '../i18n/contentLocales';
import {
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Radio,
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
import { listChallenges } from '../api/content';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/useAuth';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import type { Challenge, ChallengeType, MultilingualString } from '../api/types';
import ErrorAlert from './ErrorAlert';
import LoadingSpinner from './LoadingSpinner';
// One order for every type list, so the filter and the create form agree.
import { CHALLENGE_TYPES } from '../utils/challengeTypes';

interface ImportChallengeDialogProps {
  open: boolean;
  /** Challenge ids already linked to the station — shown as disabled. */
  linkedChallengeIds: string[];
  /**
   * Library station the challenge will be matched to. Challenges already
   * used on this station in other rallyes sort first.
   */
  stationId?: string;
  onCancel: () => void;
  onImport: (challengeId: string) => Promise<void> | void;
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

const ImportChallengeDialog: React.FC<ImportChallengeDialogProps> = ({
  open,
  linkedChallengeIds,
  stationId,
  onCancel,
  onImport,
}) => {
  const { user } = useAuth();
  const { t, i18n } = useTranslation('content');
  const orgSlug = user?.organization_slug;

  const [challenges, setChallenges] = useState<Challenge[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<ChallengeType | 'all'>('all');
  const [selected, setSelected] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // Drops responses that arrive after a newer request was issued.
  const requestRef = useRef(0);

  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';
  const titleOf = (ml: MultilingualString) => localizedText(ml, activeLang);

  // The API narrows by the same rule as the library page (titles in any
  // language, plus the type). The station ranking below runs in the browser
  // over the rows the API returned.
  const debouncedSearch = useDebouncedValue(search, 300);

  const load = useCallback(
    async (q: string, type: ChallengeType | 'all') => {
      if (!orgSlug) return;
      const requestId = ++requestRef.current;
      setLoading(true);
      setError(null);
      try {
        const resp = await listChallenges(orgSlug, { q, type, limit: 200 });
        if (requestId !== requestRef.current) return;
        setChallenges(resp.items);
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
    if (open) void load(debouncedSearch, typeFilter);
  }, [open, debouncedSearch, typeFilter, load]);

  // Reset on close, so reopening starts from an empty (and already settled)
  // search and filter instead of re-issuing the previous query.
  useEffect(() => {
    if (!open) {
      setSearch('');
      setTypeFilter('all');
      setSelected(null);
    }
  }, [open]);

  const linked = useMemo(() => new Set(linkedChallengeIds), [linkedChallengeIds]);

  // The list is one radio group: its name is the dialog's title, the name of
  // each radio is the title of its row. The ids tie the three together.
  const baseId = useId();
  const titleId = `${baseId}-title`;

  // Station-relevant first (already matched to this library station in other
  // rallyes), then most recently updated.
  const isRelevant = (c: Challenge) =>
    !!stationId && (c.usage?.station_ids ?? []).includes(stationId);

  const visible = useMemo(
    () =>
      [...challenges].sort((a, b) => {
        const rel = Number(isRelevant(b)) - Number(isRelevant(a));
        if (rel !== 0) return rel;
        return b.updated_at.localeCompare(a.updated_at);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [challenges, stationId],
  );

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
      <DialogTitle id={titleId}>{t('challenge.import.title')}</DialogTitle>
      <DialogContent>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 2, mt: 1 }}>
          <TextField
            label={t('challenge.import.search')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            size="small"
            fullWidth
          />
          <TextField
            select
            label={t('challenge.import.typeFilter')}
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as ChallengeType | 'all')}
            size="small"
            sx={{ minWidth: 180 }}
          >
            <MenuItem value="all">{t('challenge.import.allTypes')}</MenuItem>
            {CHALLENGE_TYPES.map((ct) => (
              <MenuItem key={ct} value={ct}>
                {t(`challenge.types.${ct}`)}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
        {error && <ErrorAlert message={error} />}
        {loading ? (
          <LoadingSpinner />
        ) : visible.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ py: 3 }}>
            {/* A type filter without a hit is a filter that finds nothing, not
                an empty library. */}
            {debouncedSearch.trim() || typeFilter !== 'all'
              ? t('challenge.list.noMatch')
              : t('challenge.import.empty')}
          </Typography>
        ) : (
          <TableContainer role="radiogroup" aria-labelledby={titleId}>
            <Table size="small" aria-label={t('challenge.import.title')}>
              <TableHead>
                <TableRow>
                  {/* The radio column has no heading of its own: a plain cell,
                      not an empty column header. */}
                  <TableCell component="td" padding="checkbox" />
                  <TableCell scope="col">{t('challenge.list.table.name')}</TableCell>
                  <TableCell scope="col">{t('challenge.list.table.type')}</TableCell>
                  <TableCell scope="col">{t('challenge.list.table.usage')}</TableCell>
                  <TableCell scope="col" align="right">
                    {t('challenge.list.table.points')}
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {visible.map((c) => {
                  const isLinked = linked.has(c.id);
                  const showRelevant = !isLinked && isRelevant(c);
                  const rowTitleId = `${baseId}-row-${c.id}-title`;
                  // The note the row shows beside the title: why it cannot be
                  // chosen, or that it fits this station. Never both.
                  const rowNoteId = `${baseId}-row-${c.id}-note`;
                  const stationTitles = (c.usage?.station_titles ?? [])
                    .map((st) => titleOf(st))
                    .filter(Boolean);
                  const maxPoints =
                    typeof c.scoring_rules?.max_points === 'number'
                      ? c.scoring_rules.max_points
                      : 0;
                  return (
                    <TableRow
                      key={c.id}
                      hover={!isLinked}
                      selected={selected === c.id}
                      onClick={() => !isLinked && setSelected(c.id)}
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
                          value={c.id}
                          checked={selected === c.id}
                          disabled={isLinked}
                          onChange={() => setSelected(c.id)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') setSelected(c.id);
                          }}
                          slotProps={{
                            input: {
                              'aria-labelledby': rowTitleId,
                              'aria-describedby':
                                isLinked || showRelevant ? rowNoteId : undefined,
                            },
                          }}
                          sx={focusRingSx}
                        />
                      </TableCell>
                      <TableCell sx={{ fontWeight: 600 }}>
                        <span id={rowTitleId}>{titleOf(c.title) || c.id}</span>
                        {showRelevant && (
                          <Chip
                            id={rowNoteId}
                            label={t('challenge.import.relevant')}
                            size="small"
                            color="primary"
                            variant="outlined"
                            sx={{ ml: 1 }}
                          />
                        )}
                        {isLinked && (
                          <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                            (<span id={rowNoteId}>{t('challenge.import.alreadyLinked')}</span>)
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        <Chip label={t(`challenge.types.${c.challenge_type}`)} size="small" />
                      </TableCell>
                      <TableCell sx={{ color: 'text.secondary' }}>
                        {stationTitles.length > 0
                          ? stationTitles.join(', ')
                          : t('challenge.list.table.unused')}
                      </TableCell>
                      <TableCell align="right">{maxPoints}</TableCell>
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
        <Button variant="contained" onClick={handleImport} disabled={!selected || submitting}>
          {t('challenge.import.submit')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ImportChallengeDialog;
