import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Stack,
  Typography,
} from '@mui/material';
import CampaignIcon from '@mui/icons-material/Campaign';
import AccessTimeIcon from '@mui/icons-material/AccessTime';
import NotificationImportantIcon from '@mui/icons-material/NotificationImportant';
import EmojiEventsIcon from '@mui/icons-material/EmojiEvents';
import InfoIcon from '@mui/icons-material/Info';
import GroupIcon from '@mui/icons-material/Group';
import RateReviewIcon from '@mui/icons-material/RateReview';
import { listFeed } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { FeedChangedEvent, FeedEvent, FeedEventType } from '../../api/types';
import { feedMessage } from '../../utils/feedMessage';
import LinkifiedText from '../../components/LinkifiedText';
import { formatRelativeTime } from '../../utils/relativeTime';
import { useWebSocket } from '../../hooks/useWebSocket';
import { useUnreadNotices } from '../../hooks/useUnreadNotices';
import CountdownTimer from '../../components/CountdownTimer';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';

type ChipColor = 'default' | 'primary' | 'secondary' | 'tertiary' | 'success' | 'warning' | 'error' | 'info';

const TYPE_CONFIG: Record<
  FeedEventType,
  { icon: React.ReactNode; color: ChipColor; accentVar: string }
> = {
  // Chip colours: yellow announcements, indigo system, green milestones,
  // warning countdowns, red alerts.
  announcement: { icon: <CampaignIcon />, color: 'secondary', accentVar: 'secondary.main' },
  system: { icon: <InfoIcon />, color: 'primary', accentVar: 'primary.main' },
  milestone: { icon: <EmojiEventsIcon />, color: 'tertiary', accentVar: 'tertiary.main' },
  countdown: { icon: <AccessTimeIcon />, color: 'warning', accentVar: 'warning.main' },
  reminder: { icon: <CampaignIcon />, color: 'primary', accentVar: 'primary.main' },
  alert: { icon: <NotificationImportantIcon />, color: 'error', accentVar: 'error.main' },
  team_update: { icon: <GroupIcon />, color: 'primary', accentVar: 'primary.main' },
  results_available: { icon: <EmojiEventsIcon />, color: 'tertiary', accentVar: 'tertiary.main' },
  feedback_prompt: { icon: <RateReviewIcon />, color: 'secondary', accentVar: 'secondary.main' },
};

function targetTimeOf(ev: FeedEvent): string | undefined {
  const c = ev.content as { target_time?: string };
  return c.target_time;
}

// The kinds of notice the server takes away again. Publishing a result deletes
// the earlier result notice and the interim run-end notice. A withdrawal
// deletes the result notice. Both deletes select on this type
// (`delete_result_link_notices`, `delete_interim_result_notices`).
function canBeRemoved(ev: FeedEvent): boolean {
  return ev.event_type === 'results_available';
}

const FeedView: React.FC = () => {
  const { runId } = useParams<{ runId: string }>();
  const { user } = useAuth();
  const { t, i18n } = useTranslation('runs');
  const navigate = useNavigate();

  const orgSlug = user?.organization_slug;

  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState<number>(() => Date.now());

  // The loaded list seeds the shared notice registry. A notice that arrived
  // while this participant was elsewhere or offline still badges the activity
  // tab. Opening this tab marks the notices as seen, but only once its own
  // list has loaded. Otherwise a notice on the still loading list would be
  // marked read before it was recorded.
  const { recordNotices, markSeen } = useUnreadNotices(runId);

  // Re-render once a minute so relative timestamps stay fresh.
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const formatRelative = useCallback(
    (iso: string): string => formatRelativeTime(iso, now, t),
    [now, t],
  );

  // The order of socket events and reads:
  // 1. Only the answer to the newest read is shown. It replaces the list, and
  //    the answer to an older read is dropped.
  // 2. An entry the socket delivered after the newest read began is kept
  //    beside that answer, once. Every earlier one was committed before the
  //    read began, so the answer holds it or the server has deleted it.
  // 3. An entry of a kind the server removes is never added from the socket.
  //    Like `feed_changed` it starts a read, so it is on the page exactly
  //    when the newest answer holds it.

  // Numbers the reads of the feed. Two can be under way at once, such as a
  // publish and at once its withdrawal. Their answers can arrive in either
  // order.
  const newestRead = useRef(0);
  // The entries the socket delivered since the newest read began. Its answer
  // is the feed as the server read it, which can be before these.
  const sinceNewestRead = useRef<FeedEvent[]>([]);

  // `quiet` reads the feed again behind the list that is on the page: no
  // spinner in its place, and the list stays if the read fails.
  const load = useCallback(async ({ quiet = false }: { quiet?: boolean } = {}) => {
    if (!orgSlug || !runId) return;
    const read = ++newestRead.current;
    sinceNewestRead.current = [];
    if (!quiet) {
      setLoading(true);
      setError(null);
    }
    try {
      const resp = await listFeed(orgSlug, runId);
      if (read !== newestRead.current) return;
      const answered = new Set(resp.items.map((e) => e.id));
      setEvents([...resp.items, ...sinceNewestRead.current.filter((e) => !answered.has(e.id))]);
      setError(null);
      recordNotices(resp.items);
      markSeen();
    } catch (err) {
      if (read !== newestRead.current) return;
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      // The spinner of a first load ends with the newest read, not with an
      // older one whose answer was dropped: that would show an empty feed.
      if (read === newestRead.current) setLoading(false);
    }
  }, [orgSlug, runId, recordNotices, markSeen]);

  useEffect(() => {
    void load();
  }, [load]);

  useWebSocket({
    path: orgSlug && runId ? `/api/orgs/${orgSlug}/runs/${runId}/ws` : '',
    enabled: !!(orgSlug && runId),
    handlers: {
      feed_event: (data) => {
        const ev = data as FeedEvent;
        // A notice goes through the database listener and `feed_changed`
        // goes out directly, so this event can arrive after its notice was
        // deleted and after the read that showed so. The feed the server
        // returns is the truth for these kinds: ask it.
        if (canBeRemoved(ev)) {
          void load({ quiet: true });
          return;
        }
        if (!sinceNewestRead.current.some((e) => e.id === ev.id)) {
          sinceNewestRead.current.push(ev);
        }
        setEvents((prev) => {
          if (prev.some((e) => e.id === ev.id)) return prev;
          // State stays in the API's order, oldest first. The render below
          // turns it around.
          return [...prev, ev];
        });
      },
      // A `feed_event` can only add an entry. When a result is published, its
      // earlier notice and the interim notice leave the feed. A withdrawal
      // takes the notice away and adds nothing. The backend then says the feed
      // changed, and the page reads it again and shows that list in place of
      // its own. It is the same list a participant gets who opens the page now.
      feed_changed: (data) => {
        if ((data as FeedChangedEvent).run_id !== runId) return;
        void load({ quiet: true });
      },
    },
  });

  if (loading) return <LoadingSpinner />;

  return (
    <Stack spacing={2} aria-live="polite" aria-atomic={false}>
      <Typography variant="h2" component="h1">
        {t('participant.feed.title')}
      </Typography>

      {error && <ErrorAlert message={error} onRetry={() => void load()} />}

      {events.length === 0 && !error && (
        <Card sx={{ textAlign: 'center', py: 5 }}>
          <CardContent>
            <Box
              sx={{
                width: 64,
                height: 64,
                borderRadius: '50%',
                bgcolor: 'primary.main',
                color: 'primary.contrastText',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                mb: 2,
              }}
            >
              <CampaignIcon fontSize="large" />
            </Box>
            <Typography variant="h5" component="p" gutterBottom>
              {t('participant.feed.emptyTitle')}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t('participant.feed.emptySubtitle')}
            </Typography>
          </CardContent>
        </Card>
      )}

      {/* Newest first, so a new alarm is not hidden below the fold. The API
          and the state keep their oldest-first order. */}
      {[...events].reverse().map((ev) => {
        const cfg = TYPE_CONFIG[ev.event_type] ?? TYPE_CONFIG.system;
        const msg = feedMessage(ev.content, i18n.language);
        const target = targetTimeOf(ev);

        return (
          <Card
            key={ev.id}
            variant="outlined"
            role={ev.event_type === 'alert' ? 'alert' : undefined}
            sx={{
              borderLeftWidth: 4,
              borderLeftColor: cfg.accentVar,
            }}
          >
            <CardContent>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1, flexWrap: 'wrap', rowGap: 0.5 }}>
                <Chip
                  icon={cfg.icon as React.ReactElement}
                  label={t(`feedComposer.types.${ev.event_type}`)}
                  size="small"
                  color={cfg.color}
                  sx={{ fontWeight: 600 }}
                />
                <Box sx={{ flex: 1 }} />
                <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', color: 'text.secondary' }}>
                  <AccessTimeIcon sx={{ fontSize: 14 }} />
                  <Typography variant="caption">
                    {formatRelative(ev.created_at)}
                  </Typography>
                </Stack>
              </Stack>

              {/* A notice is plain text. A web address in it is a link, so
                  the coordinator can send a page without a second channel. */}
              {msg && (
                <Typography
                  variant="body1"
                  sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', '& a': { color: 'primary.main' } }}
                >
                  <LinkifiedText text={msg} />
                </Typography>
              )}

              {ev.event_type === 'countdown' && target && (
                <Box sx={{ mt: 1 }}>
                  <CountdownTimer targetTime={target} />
                </Box>
              )}

              {/* Results are read in the "Rangliste" tab, which also holds
                  the published link while it lives. */}
              {ev.event_type === 'results_available' && (
                <Box sx={{ mt: 1.5 }}>
                  <Button
                    variant="outlined"
                    startIcon={<EmojiEventsIcon />}
                    onClick={() => navigate(`/runs/${runId}/participant/team/leaderboard`)}
                  >
                    {t('participant.feed.leaderboardCta')}
                  </Button>
                </Box>
              )}

              {/* The end-of-run FEEDBACK_PROMPT feed event is the entry point
                  into the feedback form, because the hard end-of-run redirect
                  is not active. Submission is anonymous on the backend, so it
                  works after the guest account was cleaned up at the run end. */}
              {ev.event_type === 'feedback_prompt' && (
                <Box sx={{ mt: 1.5 }}>
                  <Button
                    variant="contained"
                    color="secondary"
                    startIcon={<RateReviewIcon />}
                    onClick={() => navigate(`/runs/${runId}/participant/feedback`)}
                  >
                    {t('participant.feed.feedbackCta')}
                  </Button>
                </Box>
              )}
            </CardContent>
          </Card>
        );
      })}
    </Stack>
  );
};

export default FeedView;
