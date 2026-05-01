import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useOutletContext, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Avatar,
  Box,
  Button,
  Card,
  CardContent,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import EmojiEventsIcon from '@mui/icons-material/EmojiEvents';
import { getMyResultLink, getScoreboard } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { Scoreboard, ScoreboardEntry, Team } from '../../api/types';
import type { PublicRunState } from '../../services/publicRuns';
import { formatRelativeTime } from '../../utils/relativeTime';
import { TEAM_DISSOLVED_EVENT } from '../../utils/windowEvents';
import { AnswerStateIcon } from '../../components/AnswerState';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import { visuallyHiddenSx } from '../../styles/visuallyHidden';

const AUTO_REFRESH_MS = 10_000;

/** Forwarded by TeamTab. `run` decides whether a standing banner is due. */
interface TeamTabOutletContext {
  run: PublicRunState | null;
  team: Team | null;
}

const ParticipantLeaderboard: React.FC = () => {
  const { t } = useTranslation('runs');
  const { user } = useAuth();
  const { runId } = useParams<{ runId: string }>();
  const { run, team } = useOutletContext<TeamTabOutletContext>();
  const orgSlug = user?.organization_slug ?? '';

  // The shell hands the team down live and switches it on `team_member_moved`.
  // A `sessionStorage` id read here would keep the highlight on the old team
  // after a move.
  const ownTeamId = team?.id ?? null;

  const [scoreboard, setScoreboard] = useState<Scoreboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // `Date.now()` cannot be called during render (purity rule of the React
  // Compiler). Snapshot it alongside each load, which already re-renders every
  // 10 s.
  const [now, setNow] = useState<number>(() => Date.now());

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // The published result link, only while it is live. It is read with every
  // refresh, so a withdrawal hides the button.
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const ended = run?.state === 'ended';

  const load = useCallback(async () => {
    if (!orgSlug || !runId) return;
    setError(null);
    try {
      const data = await getScoreboard(orgSlug, runId);
      setScoreboard(data);
      setNow(Date.now());
      if (ended) {
        // A failed link lookup only hides the button. The standing stays.
        try {
          const link = await getMyResultLink(orgSlug, runId);
          setResultUrl(link?.status === 'published' ? (link.url ?? null) : null);
        } catch {
          setResultUrl(null);
        }
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, runId, ended]);

  useEffect(() => {
    void load();
    intervalRef.current = setInterval(() => void load(), AUTO_REFRESH_MS);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [load]);

  // The shell hears on the run channel that another team was dissolved and
  // says so here. The ranks of the others changed, so the standing loads at
  // once and does not wait for the next refresh.
  useEffect(() => {
    const onTeamDissolved = () => void load();
    window.addEventListener(TEAM_DISSOLVED_EVENT, onTeamDissolved);
    return () => window.removeEventListener(TEAM_DISSOLVED_EVENT, onTeamDissolved);
  }, [load]);

  if (loading) return <LoadingSpinner />;

  const entries = scoreboard?.entries ?? [];

  // The "Rangliste" sub-tab above names this view. The one h1 of the page
  // carries that name visually hidden, so nothing on screen repeats the tab and
  // the page still has its main heading.
  return (
    <Box>
      <Typography variant="h1" sx={visuallyHiddenSx}>
        {t('participant.nav.leaderboard')}
      </Typography>
      {error && <ErrorAlert message={error} onRetry={load} />}

      {run?.state === 'ended' && scoreboard && (
        <Alert
          severity={scoreboard.is_final ? 'success' : 'info'}
          // The sentence names the standing, so the sign is decorative.
          icon={
            <AnswerStateIcon
              state={scoreboard.is_final ? 'scored' : 'submitted'}
              decorative
              fontSize="inherit"
            />
          }
          sx={{ mb: 2 }}
        >
          {scoreboard.is_final
            ? t('participant.leaderboard.final')
            : t('participant.leaderboard.interim')}
        </Alert>
      )}

      {ended && resultUrl && (
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 2 }}>
          <Button
            variant="contained"
            href={resultUrl}
            target="_blank"
            rel="noopener noreferrer"
            startIcon={<OpenInNewIcon />}
          >
            {t('participant.leaderboard.resultLink')}
          </Button>
          <Tooltip title={copied ? t('participant.leaderboard.resultLinkCopied') : t('participant.leaderboard.resultLinkCopy')}>
            <IconButton
              aria-label={t('participant.leaderboard.resultLinkCopy')}
              onClick={() => {
                void navigator.clipboard?.writeText(resultUrl).then(() => setCopied(true));
              }}
            >
              <ContentCopyIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      )}

      {entries.length === 0 ? (
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
              <EmojiEventsIcon fontSize="large" />
            </Avatar>
            <Typography variant="h5" component="h2" gutterBottom>
              {t('participant.leaderboard.empty.title')}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t('participant.leaderboard.empty.subtitle')}
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Stack spacing={1}>
          {entries.map((entry) => (
            <LeaderboardRow
              key={entry.team_id}
              entry={entry}
              isOwnTeam={entry.team_id === ownTeamId}
              now={now}
            />
          ))}
        </Stack>
      )}
    </Box>
  );
};

interface RowProps {
  entry: ScoreboardEntry;
  isOwnTeam: boolean;
  now: number;
}

const LeaderboardRow: React.FC<RowProps> = ({ entry, isOwnTeam, now }) => {
  const { t } = useTranslation('runs');
  // The API assigns shared ranks for ties (1, 1, 3). The row index would
  // re-number them wrongly.
  const rank = entry.rank;
  const isFirst = rank === 1;
  // Only a *final* first place is a winner; while the run is active or grading
  // continues, rank 1 keeps the trophy look but no winner label.
  const isWinner = entry.is_winner;

  return (
    <Card
      variant="outlined"
      sx={{
        borderColor: isOwnTeam ? 'secondary.main' : undefined,
        bgcolor: isOwnTeam ? 'secondary.light' : 'background.paper',
        borderWidth: isOwnTeam ? 2 : 1,
      }}
    >
      <CardContent
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 2,
          py: 1.5,
          '&:last-child': { pb: 1.5 },
        }}
      >
        <Avatar
          role="img"
          aria-label={t('participant.leaderboard.rankLabel', { rank })}
          sx={{
            width: 40,
            height: 40,
            bgcolor: isFirst ? 'secondary.main' : 'primary.main',
            color: isFirst ? 'text.primary' : 'primary.contrastText',
            fontWeight: 700,
          }}
        >
          {isFirst ? <EmojiEventsIcon /> : rank}
        </Avatar>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'baseline', flexWrap: 'wrap' }}>
            <Typography variant="subtitle1" component="span" noWrap>
              {entry.team_name}
            </Typography>
            {isWinner && (
              <Typography
                variant="caption"
                component="span"
                sx={{ color: 'text.primary', fontWeight: 600 }}
              >
                · {t('participant.leaderboard.winner')}
              </Typography>
            )}
            {isOwnTeam && (
              <Typography
                variant="caption"
                component="span"
                sx={{ color: 'text.primary', fontWeight: 600 }}
              >
                · {t('participant.leaderboard.yourTeam')}
              </Typography>
            )}
          </Stack>
          <Typography variant="caption" color="text.secondary">
            {entry.last_scored_at
              ? formatRelativeTime(entry.last_scored_at, now, t)
              : t('participant.leaderboard.neverScored')}
          </Typography>
        </Box>
        <Typography variant="subtitle1" component="span" sx={{ fontWeight: 700, ml: 'auto' }}>
          {t('participant.leaderboard.points', { points: entry.total_points })}
        </Typography>
      </CardContent>
    </Card>
  );
};

export default ParticipantLeaderboard;
