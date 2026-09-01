import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Card,
  CardContent,
  GlobalStyles,
  Stack,
  Typography,
} from '@mui/material';
import EmojiEventsIcon from '@mui/icons-material/EmojiEvents';
import FullscreenIcon from '@mui/icons-material/Fullscreen';
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit';
import PrintIcon from '@mui/icons-material/Print';
import { getRun, getScoreboard } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { Run, Scoreboard, ScoreboardEntry } from '../../api/types';
import { AnswerStateChip } from '../../components/AnswerState';
import BackLink from '../../components/BackLink';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatDate } from '../../utils/formatDate';

/**
 * Vollbild hides the AppShell navigation frame the same way the print
 * stylesheet's `print-hidden` hides it for paper: a utility class on <body>
 * plus display:none rules. It lives here (not in index.css) because only this
 * view needs it on screen. The Fullscreen API is used on top when available;
 * the body class alone is the fallback for browsers without it.
 */
const VOLLBILD_BODY_CLASS = 'clavis-results-vollbild';

const vollbildStyles = {
  [`body.${VOLLBILD_BODY_CLASS} .MuiAppBar-root, body.${VOLLBILD_BODY_CLASS} .MuiDrawer-root`]: {
    display: 'none !important',
  },
  [`body.${VOLLBILD_BODY_CLASS} #main-content`]: {
    width: '100% !important',
    padding: '0 !important',
  },
  [`body.${VOLLBILD_BODY_CLASS} #main-content > .MuiToolbar-root`]: {
    display: 'none !important',
  },
  [`body.${VOLLBILD_BODY_CLASS} .clavis-vollbild-hidden`]: {
    display: 'none !important',
  },
};

const RunResults: React.FC = () => {
  const { t, i18n } = useTranslation('runs');
  const { user } = useAuth();
  const { runId } = useParams<{ runId: string }>();
  const orgSlug = user?.organization_slug ?? '';

  const [run, setRun] = useState<Run | null>(null);
  const [scoreboard, setScoreboard] = useState<Scoreboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [vollbild, setVollbild] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!orgSlug || !runId) return;
    setError(null);
    try {
      const [runData, scoreboardData] = await Promise.all([
        getRun(orgSlug, runId),
        getScoreboard(orgSlug, runId),
      ]);
      setRun(runData);
      setScoreboard(scoreboardData);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Esc leaves browser fullscreen without any button being clicked — follow
  // the element's state so the toggle and the body class stay in sync.
  useEffect(() => {
    const onChange = () => {
      if (!document.fullscreenElement) setVollbild(false);
    };
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  useEffect(() => {
    document.body.classList.toggle(VOLLBILD_BODY_CLASS, vollbild);
    return () => document.body.classList.remove(VOLLBILD_BODY_CLASS);
  }, [vollbild]);

  const toggleVollbild = () => {
    const next = !vollbild;
    setVollbild(next);
    const el = containerRef.current;
    if (next && el && typeof el.requestFullscreen === 'function') {
      void el.requestFullscreen().catch(() => {
        /* the body class alone already removed the navigation frame */
      });
    } else if (
      !next &&
      document.fullscreenElement &&
      typeof document.exitFullscreen === 'function'
    ) {
      void document.exitFullscreen().catch(() => {});
    }
  };

  if (loading && !run) return <LoadingSpinner />;

  const entries = scoreboard?.entries ?? [];
  const winners = entries.filter((e: ScoreboardEntry) => e.is_winner);
  const isFinal = scoreboard?.is_final ?? false;

  return (
    <Box>
      <GlobalStyles styles={vollbildStyles} />
      <Box className="clavis-vollbild-hidden print-hidden">
        <BackLink to={`/runs/${runId}`} label={t('scoreboard.backToRun')} />
      </Box>
      <Box
        ref={containerRef}
        sx={vollbild ? { minHeight: '100vh', p: { xs: 2, md: 4 } } : undefined}
      >
        {error && <ErrorAlert message={error} onRetry={load} />}

        {run && (
          <Card className="print-card">
            <CardContent sx={{ p: { xs: 2, sm: 3, md: 4 } }}>
              <Stack
                direction={{ xs: 'column', sm: 'row' }}
                spacing={{ xs: 1, sm: 2 }}
                sx={{ alignItems: { sm: 'center' }, flexWrap: 'wrap', mb: 1 }}
              >
                <Typography variant="h2" component="h1" sx={{ fontWeight: 700 }}>
                  {run.name}
                </Typography>
                <AnswerStateChip
                  state={isFinal ? 'scored' : 'submitted'}
                  label={isFinal ? t('results.labelFinal') : t('results.labelInterim')}
                  size="medium"
                />
              </Stack>
              {run.ended_at && (
                <Typography variant="h5" component="p" color="text.secondary" sx={{ mb: 3 }}>
                  {t('results.endedOn', {
                    date: formatDate(run.ended_at, i18n.language),
                  })}
                </Typography>
              )}

              {winners.length > 0 && (
                <Stack
                  direction="row"
                  spacing={{ xs: 2, md: 3 }}
                  sx={{ alignItems: 'center', flexWrap: 'wrap', mb: 4 }}
                >
                  <EmojiEventsIcon color="warning" sx={{ fontSize: { xs: 48, md: 72 } }} />
                  <Stack spacing={0.5}>
                    <Typography variant="overline" component="p">
                      {t('results.winnerLabel', { count: winners.length })}
                    </Typography>
                    <Stack direction="row" spacing={{ xs: 2, md: 4 }} sx={{ flexWrap: 'wrap' }}>
                      {winners.map((w) => (
                        <Box key={w.team_id}>
                          <Typography variant="h3" component="p">
                            {w.team_name}
                          </Typography>
                          <Typography variant="h5" component="p" color="text.secondary">
                            {t('participant.leaderboard.points', { points: w.total_points })}
                          </Typography>
                        </Box>
                      ))}
                    </Stack>
                  </Stack>
                </Stack>
              )}

              <Stack>
                {entries.map((entry) => (
                  <Stack
                    key={entry.team_id}
                    className="print-card"
                    direction="row"
                    spacing={{ xs: 1, md: 2 }}
                    sx={{
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      py: { xs: 0.75, md: 1.25 },
                      borderBottom: '1px solid',
                      borderColor: 'divider',
                      gap: 1,
                    }}
                  >
                    <Stack direction="row" spacing={{ xs: 1, md: 2 }} sx={{ alignItems: 'center', minWidth: 0 }}>
                      <Typography
                        variant="h4"
                        component="p"
                        sx={{ minWidth: { xs: 36, md: 56 }, fontWeight: 700, color: 'text.secondary' }}
                      >
                        {entry.rank}.
                      </Typography>
                      <Typography variant="h4" component="p">
                        {entry.team_name}
                      </Typography>
                      {entry.is_winner && (
                        <EmojiEventsIcon color="warning" sx={{ fontSize: { xs: 28, md: 36 } }} />
                      )}
                    </Stack>
                    <Typography variant="h5" component="p" sx={{ fontWeight: 700, whiteSpace: 'nowrap' }}>
                      {t('participant.leaderboard.points', { points: entry.total_points })}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            </CardContent>
          </Card>
        )}

        <Stack className="print-hidden" direction="row" spacing={1} sx={{ mt: 2, flexWrap: 'wrap' }}>
          <Button
            variant="outlined"
            startIcon={vollbild ? <FullscreenExitIcon /> : <FullscreenIcon />}
            onClick={toggleVollbild}
            aria-pressed={vollbild}
          >
            {vollbild ? t('results.exitVollbild') : t('results.vollbild')}
          </Button>
          <Button variant="outlined" startIcon={<PrintIcon />} onClick={() => window.print()}>
            {t('results.print')}
          </Button>
        </Stack>
      </Box>
    </Box>
  );
};

export default RunResults;
