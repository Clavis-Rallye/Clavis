import React, { useEffect, useState } from 'react';
import { Link as RouterLink, useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Avatar,
  Box,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  Container,
  Button,
  IconButton,
  Stack,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import EmojiEventsIcon from '@mui/icons-material/EmojiEvents';
import HistoryIcon from '@mui/icons-material/History';
import { getMyRunHistory } from '../../api/me';
import { ApiError } from '../../api/client';
import type { RunHistoryEntry } from '../../api/types';
import { useInstanceConfig } from '../../config/instanceConfig';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import ParticipantBottomNav from '../../components/ParticipantBottomNav';
import { BOTTOM_NAV_HEIGHT, getLastRunId } from '../../components/participantNav';
import { formatDate } from '../../utils/formatDate';
import logoSmall from '../../assets/logos/logo-small.svg';

/**
 * Participant Verlauf / Run History page.
 *
 * Reachable via the overflow menu of the run shell (item "Verlauf").
 * Any signed-in user, guests included, can open it. `/api/me/run-history` is
 * gated only by `require_role("guest")` and scopes by user id.
 */
const RunHistory: React.FC = () => {
  const { t, i18n } = useTranslation('runs');
  const { t: tCommon } = useTranslation('common');
  const navigate = useNavigate();
  const { brand_name: brand } = useInstanceConfig();
  const [items, setItems] = useState<RunHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      setError(null);
      try {
        const r = await getMyRunHistory();
        if (!cancelled) setItems(r.items);
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.detail : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const lang = i18n.language.startsWith('de') ? 'de' : 'en';

  return (
    <Box
      sx={{
        bgcolor: 'background.default',
        minHeight: '100dvh',
        pt: 3,
        pb: `${BOTTOM_NAV_HEIGHT + 32}px`,
      }}
    >
      <a href="#main-content" className="skip-link">{tCommon('aria.skipToMain')}</a>
      <Container maxWidth="md" component="main" id="main-content">
        <Stack
          direction="row"
          spacing={1}
          component={RouterLink}
          to="/"
          sx={{ alignItems: 'center', mb: 2, textDecoration: 'none', color: 'inherit' }}
        >
          <Box component="img" src={logoSmall} alt={brand} sx={{ width: 28, height: 24 }} />
          <Typography variant="subtitle1" component="span" sx={{ fontWeight: 600 }}>
            {brand}
          </Typography>
        </Stack>

        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 2 }}>
          <IconButton edge="start" onClick={() => navigate(-1)} aria-label={t('participant.history.back')}>
            <ArrowBackIcon />
          </IconButton>
          <Typography variant="h2" component="h1">
            {t('participant.history.title')}
          </Typography>
        </Stack>

        {/* Someone whose runs have ended lands here and may want to join the
            next run. This is the way to the code field. */}
        <Button
          variant="outlined"
          onClick={() => navigate('/no-runs')}
          sx={{ alignSelf: 'flex-start', mb: 2, minHeight: 44 }}
        >
          {t('participant.history.joinWithCode')}
        </Button>

        {loading ? (
          <LoadingSpinner />
        ) : error ? (
          <ErrorAlert message={error} />
        ) : items.length === 0 ? (
          <Card sx={{ textAlign: 'center', py: 6 }}>
            <CardContent>
              <Avatar
                sx={{
                  bgcolor: 'secondary.light',
                  color: 'text.primary',
                  width: 64,
                  height: 64,
                  mx: 'auto',
                  mb: 2,
                }}
              >
                <HistoryIcon fontSize="large" />
              </Avatar>
              <Typography variant="h5" component="p" gutterBottom>
                {t('participant.history.empty.title')}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {t('participant.history.empty.subtitle')}
              </Typography>
            </CardContent>
          </Card>
        ) : (
          <Stack spacing={1.5}>
            {items.map((entry) => {
              const title =
                entry.rallye_title?.[lang] ??
                entry.rallye_title?.de ??
                entry.rallye_title?.en ??
                entry.run_name;
              const subtitle = entry.team_name;
              // Medium date with a four-digit year.
              const dateStr = entry.started_at ? formatDate(entry.started_at, lang) : '—';
              return (
                <Card key={entry.run_id} variant="outlined" sx={{ borderRadius: 2 }}>
                  <CardActionArea
                    onClick={() =>
                      navigate(`/runs/${entry.run_id}/participant/feed`)
                    }
                  >
                    <CardContent>
                      <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
                        <Avatar
                          sx={{
                            bgcolor:
                              entry.placement === 1
                                ? 'secondary.main'
                                : 'primary.main',
                            color:
                              entry.placement === 1
                                ? 'text.primary'
                                : 'primary.contrastText',
                            fontWeight: 700,
                          }}
                        >
                          {/* The trophy is for a winner with points, as on the
                              scoreboard. Without a final place the circle
                              holds a dash, not a "?". */}
                          {entry.placement === 1 && entry.total_points > 0 ? (
                            <EmojiEventsIcon />
                          ) : (
                            (entry.placement ?? '–')
                          )}
                        </Avatar>
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Typography variant="subtitle1" component="div" noWrap>
                            {title}
                          </Typography>
                          <Typography variant="body2" color="text.secondary" noWrap>
                            {subtitle} · {dateStr}
                          </Typography>
                        </Box>
                        <Stack spacing={0.5} sx={{ alignItems: 'flex-end' }}>
                          {entry.placement && entry.total_teams ? (
                            <Chip
                              size="small"
                              label={t('participant.history.placement', {
                                place: entry.placement,
                                total: entry.total_teams,
                              })}
                              color={entry.placement === 1 ? 'secondary' : 'primary'}
                              variant={entry.placement === 1 ? 'filled' : 'outlined'}
                            />
                          ) : (
                            <Chip
                              size="small"
                              label={t(`participant.history.state.${entry.state}`, {
                                defaultValue: entry.state,
                              })}
                              variant="outlined"
                            />
                          )}
                          <Typography variant="caption" color="text.secondary">
                            {t('participant.leaderboard.points', {
                              points: entry.total_points,
                            })}
                          </Typography>
                        </Stack>
                      </Stack>
                    </CardContent>
                  </CardActionArea>
                </Card>
              );
            })}
          </Stack>
        )}
      </Container>
      <ParticipantBottomNav runId={getLastRunId()} active={false} />
    </Box>
  );
};

export default RunHistory;
