import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Collapse,
  Divider,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Snackbar,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import SupportAgentIcon from '@mui/icons-material/SupportAgent';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutlined';
import GroupIcon from '@mui/icons-material/Group';
import ManageAccountsIcon from '@mui/icons-material/ManageAccounts';
import AssignmentIcon from '@mui/icons-material/Assignment';
import EmojiEventsIcon from '@mui/icons-material/EmojiEvents';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import PauseIcon from '@mui/icons-material/Pause';
import StopIcon from '@mui/icons-material/Stop';
import ScheduleIcon from '@mui/icons-material/Schedule';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import QrCode2Icon from '@mui/icons-material/QrCode2';
import { QRCodeSVG } from 'qrcode.react';
import { useAuth } from '../../auth/useAuth';
import { useRunState } from '../../hooks/useRunState';
import { useWebSocket } from '../../hooks/useWebSocket';
import {
  listTeams,
  listFeed,
  getFeedbackSummary,
  listFeedback,
  getTeamProgress,
  getScoreboard,
} from '../../api/runs';
import { ApiError } from '../../api/client';
import type {
  FeedEvent,
  FeedbackSummary,
  Feedback,
  RunState,
  Scoreboard,
  Team,
  TeamProgressEntry,
} from '../../api/types';
import { AnswerStateChip } from '../../components/AnswerState';
import ConfirmDialog from '../../components/ConfirmDialog';
import TeamMembersDialog from '../../components/TeamMembersDialog';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import FeedComposer from '../../components/FeedComposer';
import ResultLinkCard from './ResultLinkCard';
import JoinCodePresentation from './JoinCodePresentation';
import TeamMemberNames from './TeamMemberNames';
import { feedMessage } from '../../utils/feedMessage';
import LinkifiedText from '../../components/LinkifiedText';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { formatDateTime, formatTime } from '../../utils/formatDate';
import { useAutoStartRuns } from '../../hooks/useAutoStartRuns';
import { visuallyHiddenSx } from '../../styles/visuallyHidden';
import RunEditDialog from './RunEditDialog';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';

const STATE_COLORS: Record<
  RunState,
  'default' | 'primary' | 'success' | 'warning' | 'error'
> = {
  configured: 'default',
  scheduled: 'primary',
  active: 'success',
  paused: 'warning',
  ended: 'error',
};

// Detects a coordinator-help request in the content of a SYSTEM feed event. The
// backend stores a help request with `message_key: 'help_request'`, `team_id`
// and `team_name` beside the two sentences `de` and `en`. Help requests stored
// before the payload carried `team_id` have only the two sentences, so for them
// this parses the localized message ("Team X benötigt Hilfe." / "Team X needs
// help."). The parsing branch can go once no stored SYSTEM feed event of a help
// request lacks `team_id`.
function extractCoordinatorRequest(
  content: unknown,
): { teamId?: string; teamName?: string } | null {
  if (!content || typeof content !== 'object') return null;
  const c = content as Record<string, unknown>;
  if (typeof c.team_id === 'string' && c.team_id) {
    return {
      teamId: c.team_id,
      teamName: typeof c.team_name === 'string' && c.team_name ? c.team_name : undefined,
    };
  }
  for (const raw of [c.de, c.en]) {
    if (typeof raw !== 'string') continue;
    const m =
      raw.match(/^Team (.+?) ben[öo]tigt Hilfe\.?$/i) ??
      raw.match(/^Team (.+?) needs help\.?$/i);
    if (m) return { teamName: m[1].trim() };
  }
  return null;
}

function formatDuration(ms: number, hoursUnit: string, minutesUnit: string): string {
  const totalMinutes = Math.max(0, Math.floor(ms / 60_000));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours} ${hoursUnit} ${minutes} ${minutesUnit}`;
}

const RunDashboard: React.FC = () => {
  const { t, i18n } = useTranslation('runs');
  const { t: tAdmin } = useTranslation('admin');
  const { user } = useAuth();
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();

  const orgSlug = user?.organization_slug ?? '';

  const { run, state, wsStatus, loading, error, start, pause, resume, end, schedule, refresh } =
    useRunState({ orgSlug, runId });
  const autoStart = useAutoStartRuns(orgSlug);
  const [editOpen, setEditOpen] = useState(false);
  const blockedInfo = run?.settings?.auto_start_blocked as { code?: string } | undefined;
  const autoStartBlocked = blockedInfo?.code ?? null;

  // Teams
  const [teams, setTeams] = useState<Team[]>([]);
  const [teamsLoading, setTeamsLoading] = useState(true);
  const [teamProgress, setTeamProgress] = useState<TeamProgressEntry[]>([]);
  // The reads of the teams and of their progress are numbered. An answer that
  // arrives after the answer of a later read is dropped: an older list would
  // put a removed team back on the page.
  const loadSeq = useRef(0);
  const teamsApplied = useRef(0);
  const progressApplied = useRef(0);

  // Feed
  const [feedEvents, setFeedEvents] = useState<FeedEvent[]>([]);
  // The notice that arrived last while the page is open. Only this one is
  // announced: the list itself is no live region, or a screen reader reads
  // every sent notice when the page opens.
  const [arrivedNotice, setArrivedNotice] = useState<FeedEvent | null>(null);

  // Joins refused before the start or during a pause: the live `join_blocked`
  // events since the page opened. The event carries no personal data, so only
  // the count is kept.
  const [blockedJoinCount, setBlockedJoinCount] = useState(0);

  // Teams that have requested coordinator assistance (highlighted in the team list).
  const [requestingTeamIds, setRequestingTeamIds] = useState<Set<string>>(new Set());
  // Team whose member list is open in the manage-members dialog
  const [membersTeam, setMembersTeam] = useState<{ id: string; name: string } | null>(null);
  // Counts the live reports of a member change. The open members dialog
  // reloads on it, so it never shows an older list than the rows behind it.
  const [membersChanged, setMembersChanged] = useState(0);
  const [coordinatorRequestToast, setCoordinatorRequestToast] = useState<string | null>(null);
  const [copyToast, setCopyToast] = useState<string | null>(null);

  // Feedback summary (ended runs only)
  const [feedbackSummary, setFeedbackSummary] = useState<FeedbackSummary | null>(null);
  const [feedbackItems, setFeedbackItems] = useState<Feedback[]>([]);

  // Scoreboard totals (ended runs only): grading progress for the results
  // card.
  const [scoreboard, setScoreboard] = useState<Scoreboard | null>(null);

  // End confirm dialog
  const [endConfirmOpen, setEndConfirmOpen] = useState(false);
  const [endLoading, setEndLoading] = useState(false);

  // Schedule picker
  const [schedulePickerOpen, setSchedulePickerOpen] = useState(false);
  const [scheduleDateTime, setScheduleDateTime] = useState('');
  const [scheduleLoading, setScheduleLoading] = useState(false);

  const [actionError, setActionError] = useState<string | null>(null);

  // Live "since start" clock, ticked once a minute.
  const [now, setNow] = useState<number>(() => Date.now());
  // The join card is open while the run is fresh, because the join code
  // matters mostly at the beginning. It folds 15 min after the start, and the
  // user can still toggle it either way.
  const [joinOpen, setJoinOpen] = useState(true);
  const joinAutoCollapsed = useRef(false);
  // The large join code for a projector. A mode of this page, not a route, so
  // the page underneath keeps its live connection.
  const [presenting, setPresenting] = useState(false);

  const loadTeams = useCallback(async ({ quiet = false }: { quiet?: boolean } = {}) => {
    if (!orgSlug || !runId) return;
    if (!quiet) setTeamsLoading(true);
    const seq = ++loadSeq.current;
    try {
      const data = await listTeams(orgSlug, runId);
      if (seq >= teamsApplied.current) {
        teamsApplied.current = seq;
        setTeams(data.items);
      }
    } catch {
      // non-fatal
    } finally {
      setTeamsLoading(false);
    }
    // Team progress loads in parallel. A failure is silent, and the row falls
    // back to the basic "{n} members" caption.
    void getTeamProgress(orgSlug, runId)
      .then((p) => {
        if (seq < progressApplied.current) return;
        progressApplied.current = seq;
        setTeamProgress(p.teams);
        // Seed the help highlight from progress, so a team that asked for help
        // before the dashboard opened shows at once, not only on a later live
        // event.
        setRequestingTeamIds((prev) => {
          const next = new Set(prev);
          p.teams.forEach((tp) => {
            if (tp.requested_help) next.add(tp.team_id);
          });
          return next;
        });
      })
      .catch(() => {
        if (seq < progressApplied.current) return;
        progressApplied.current = seq;
        setTeamProgress([]);
      });
  }, [orgSlug, runId]);

  const loadFeed = useCallback(async () => {
    if (!orgSlug || !runId) return;
    try {
      const data = await listFeed(orgSlug, runId);
      setFeedEvents(data.items);
    } catch {
      // non-fatal
    }
  }, [orgSlug, runId]);

  const loadFeedback = useCallback(async () => {
    if (!orgSlug || !runId) return;
    try {
      const [summary, items] = await Promise.all([
        getFeedbackSummary(orgSlug, runId),
        listFeedback(orgSlug, runId),
      ]);
      setFeedbackSummary(summary);
      setFeedbackItems(items.items);
    } catch {
      // non-fatal
    }
  }, [orgSlug, runId]);

  const loadScoreboard = useCallback(async () => {
    if (!orgSlug || !runId) return;
    try {
      setScoreboard(await getScoreboard(orgSlug, runId));
    } catch {
      // non-fatal: the results card falls back to "grading in progress"
    }
  }, [orgSlug, runId]);

  useEffect(() => {
    void loadTeams();
    void loadFeed();
  }, [loadTeams, loadFeed]);

  // Teams form and grow while this dashboard sits open. Without a refresh, a
  // coordinator watching teams join would see none of it.
  useAutoRefresh(() => loadTeams({ quiet: true }));

  // The name as the team list has it now: a team can be renamed while its
  // dialog is open, and the confirmation names the team.
  const membersTeamName =
    teams.find((team) => team.id === membersTeam?.id)?.name ?? membersTeam?.name ?? '';

  useEffect(() => {
    if (state === 'ended') {
      void loadFeedback();
      void loadScoreboard();
    }
  }, [state, loadFeedback, loadScoreboard]);

  // The refused-join counter resets whenever the run becomes active, start and
  // resume alike, because from then on joins are open again and the refused
  // attempts are moot.
  useEffect(() => {
    if (state === 'active') setBlockedJoinCount(0);
  }, [state]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  // The card folds once, 15 min after the start, also while the page stays
  // open: the minute tick above re-checks. Deciding only when the page opens
  // would never fold it for a coordinator at the meeting point. A card opened
  // again by hand stays open.
  useEffect(() => {
    if (joinAutoCollapsed.current || !run?.started_at) return;
    if (now - Date.parse(run.started_at) >= 15 * 60_000) {
      joinAutoCollapsed.current = true;
      setJoinOpen(false);
    }
  }, [run?.started_at, now]);

  // A notice reaches the page twice, as the answer to the post and over the
  // socket. The second arrival changes neither the list nor the announcement.
  const addFeedEvent = (event: FeedEvent) => {
    setFeedEvents((prev) => (prev.some((e) => e.id === event.id) ? prev : [event, ...prev]));
    if (event.event_type !== 'system') {
      setArrivedNotice((prev) => (prev?.id === event.id ? prev : event));
    }
  };

  // Subscribe to WS for live feed events (separate from useRunState's WS)
  useWebSocket({
    path: orgSlug && runId ? `/api/orgs/${orgSlug}/runs/${runId}/ws` : '',
    enabled: !!orgSlug && !!runId,
    handlers: {
      // Broadcast on run_state:{run_id} whenever a join or team creation is
      // refused because the run has not started or is paused. Only the arrival
      // matters. The payload has no personal data.
      join_blocked: () => setBlockedJoinCount((c) => c + 1),
      // A join, leave or removal, a move between teams and a new team name
      // each reload the team list at once. The rows name the members, and
      // without this a new name would wait for the next poll.
      team_members_changed: () => {
        void loadTeams({ quiet: true });
        setMembersChanged((n) => n + 1);
      },
      team_member_moved: () => {
        void loadTeams({ quiet: true });
        setMembersChanged((n) => n + 1);
      },
      // A team is gone, removed as a whole or with its last member.
      // The list loads as on a member change, and a members dialog that is
      // open for that team closes, because nothing is left to manage.
      team_dissolved: (data) => {
        void loadTeams({ quiet: true });
        const dissolvedId = (data as { team_id?: string } | null)?.team_id;
        setMembersTeam((prev) => (prev && prev.id === dissolvedId ? null : prev));
      },
      'team.name_updated': () => void loadTeams({ quiet: true }),
      feed_event: (data) => {
        const event = data as FeedEvent;
        addFeedEvent(event);
        if (event.event_type === 'system') {
          const req = extractCoordinatorRequest(event.content);
          if (req) {
            setTeams((prevTeams) => {
              const match = req.teamId
                ? prevTeams.find((t) => t.id === req.teamId)
                : prevTeams.find((t) => t.name === req.teamName);
              const highlightId = req.teamId ?? match?.id;
              if (highlightId) {
                setRequestingTeamIds((prev) => {
                  const next = new Set(prev);
                  next.add(highlightId);
                  return next;
                });
              }
              const toastName = match?.name ?? req.teamName;
              if (toastName) {
                setCoordinatorRequestToast(
                  tAdmin('coordinatorRequest.teamRequestedHelp', { teamName: toastName }),
                );
              }
              // Refresh the team list: a request opens the team's coordinator
              // assist window, so coordinator_chat_open must be refetched, and
              // a not-yet-listed team needs its row rendered.
              void loadTeams();
              return prevTeams;
            });
          }
        }
      },
    },
  });

  // Opens the coordinator chat of a team and clears its local "needs help"
  // highlight. The backend has no "resolved" state for help requests, so
  // opening the chat counts as the coordinator's acknowledgment. A new live
  // request adds the highlight again.
  const handleOpenTeamChat = (teamId: string) => {
    setRequestingTeamIds((prev) => {
      if (!prev.has(teamId)) return prev;
      const next = new Set(prev);
      next.delete(teamId);
      return next;
    });
    navigate(`/runs/${runId}/teams/${teamId}/chat`);
  };

  const handleCopyCode = () => {
    if (run?.join_code) {
      void navigator.clipboard
        .writeText(run.join_code)
        .then(() => setCopyToast(t('dashboard.copyCodeSuccess')))
        .catch(() => setCopyToast(t('dashboard.copyCodeFailure')));
    }
  };

  const handleCopyJoinUrl = () => {
    if (run?.join_code) {
      const url = `${window.location.origin}/join/${run.join_code}`;
      void navigator.clipboard
        .writeText(url)
        .then(() => setCopyToast(t('dashboard.copyJoinUrlSuccess')))
        .catch(() => setCopyToast(t('dashboard.copyJoinUrlFailure')));
    }
  };

  const handleEnd = async () => {
    setEndLoading(true);
    setActionError(null);
    try {
      await end();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setEndLoading(false);
      setEndConfirmOpen(false);
    }
  };

  const handleSchedule = async () => {
    if (!scheduleDateTime) return;
    setScheduleLoading(true);
    setActionError(null);
    try {
      await schedule(new Date(scheduleDateTime).toISOString());
      setSchedulePickerOpen(false);
      setScheduleDateTime('');
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setScheduleLoading(false);
    }
  };

  const handleStart = async () => {
    setActionError(null);
    try {
      await start();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  const handlePause = async () => {
    setActionError(null);
    try {
      await pause();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  const handleResume = async () => {
    setActionError(null);
    try {
      await resume();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  if (loading && !run) return <LoadingSpinner />;
  if (error && !run) return <ErrorAlert message={error} />;
  if (!run) return null;

  const joinUrl = `${window.location.origin}/join/${run.join_code}`;
  const maxDist = feedbackSummary
    ? Math.max(...Object.values(feedbackSummary.distribution), 1)
    : 1;

  const startedMs = run.started_at ? Date.parse(run.started_at) : null;
  const endedMs = run.ended_at ? Date.parse(run.ended_at) : null;
  const elapsed =
    // `now` ticks once a minute, so right after "Starten" it still lies
    // before the start. That would show "Seit Start —" for up to a minute.
    // A started run always has a duration, at first zero.
    startedMs
      ? formatDuration(
          (endedMs ?? now) - startedMs,
          t('dashboard.metrics.hoursUnit'),
          t('dashboard.metrics.minutesUnit'),
        )
      : t('dashboard.metrics.notStarted');
  // "Vergangene Mitteilungen" lists what the coordinators sent. System lines
  // ("Quiz automatisch bewertet", "Team X benötigt Hilfe.") are not notices
  // of theirs, and the help request already shows as a marked team row and a
  // toast.
  const pastNotices = feedEvents.filter((ev) => ev.event_type !== 'system');
  const submittedCount = teamProgress.reduce((n, p) => n + p.challenges_submitted, 0);
  const scoredCount = teamProgress.reduce((n, p) => n + p.challenges_scored, 0);
  const newCount = Math.max(0, submittedCount - scoredCount);
  // When teams can join. The page shows code and QR code before the
  // start, but the server refuses every join until the run is active, and
  // again during a pause. The sentence says so next to the button that opens
  // joining. A planned run starts on its own, unless the
  // organization switched that off or the automatic start failed.
  const plannedAutoStart = !!run.scheduled_for && autoStart !== false && !autoStartBlocked;
  const joinWindowKey =
    state === 'configured' || state === 'scheduled'
      ? plannedAutoStart
        ? 'dashboard.joinWindow.plannedStart'
        : 'dashboard.joinWindow.manualStart'
      : state === 'paused'
        ? 'dashboard.joinWindow.paused'
        : null;

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" spacing={1.5} sx={{ alignItems: 'center', mb: 2 }}>
        <Typography variant="h2" component="h1">
          {run.name}
        </Typography>
        {wsStatus !== 'open' && (
          <Tooltip title={`ws: ${wsStatus}`}>
            <Box
              sx={{
                width: 10,
                height: 10,
                borderRadius: '50%',
                bgcolor: wsStatus === 'connecting' ? 'warning.main' : 'error.main',
              }}
            />
          </Tooltip>
        )}
      </Stack>

      {(error || actionError) && (
        <Box sx={{ mb: 2 }}>
          <ErrorAlert message={error ?? actionError ?? ''} />
        </Box>
      )}

      {/* Status: state + elapsed + run controls, with the key metrics */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Stack
            direction={{ xs: 'column', sm: 'row' }}
            spacing={2}
            sx={{ justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' } }}
          >
            <Stack direction="row" spacing={1.5} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
              {state && (
                <Chip
                  label={t(`dashboard.state.${state}`)}
                  color={STATE_COLORS[state]}
                  size="small"
                />
              )}
              {/* The planned start, and whether it happens on its own. */}
              {(state === 'configured' || state === 'scheduled') && run.scheduled_for && (
                <Typography variant="body2" color="text.secondary" data-testid="planned-start">
                  {t(autoStart === false ? 'dashboard.plannedManual' : 'dashboard.plannedAuto', {
                    datetime: formatDateTime(run.scheduled_for, i18n.language),
                  })}
                </Typography>
              )}
              {startedMs && (
                <Typography variant="body2" color="text.secondary">
                  {t('dashboard.metrics.timeSinceStart')}{' '}
                  <Box component="span" sx={{ fontWeight: 700, color: 'text.primary' }}>
                    {elapsed}
                  </Box>
                </Typography>
              )}
            </Stack>
            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
              {/* Refused joins since the page opened, next to the action that
                  ends the waiting. Hidden at 0: the signal only matters once
                  someone actually tried. */}
              {blockedJoinCount > 0 &&
                (state === 'configured' || state === 'scheduled' || state === 'paused') && (
                  <Chip
                    size="small"
                    color="warning"
                    sx={{ alignSelf: 'center', fontWeight: 700 }}
                    label={t(
                      state === 'paused'
                        ? 'dashboard.blockedJoins.whilePaused'
                        : 'dashboard.blockedJoins.beforeStart',
                      { count: blockedJoinCount },
                    )}
                  />
                )}
              {(state === 'configured' || state === 'scheduled') && (
                <Tooltip title={t('dashboard.edit.button')}>
                  <IconButton aria-label={t('dashboard.edit.button')} onClick={() => setEditOpen(true)}>
                    <EditOutlinedIcon />
                  </IconButton>
                </Tooltip>
              )}
              {(state === 'configured' || state === 'scheduled') && (
                <Button
                  variant="contained"
                  color="success"
                  startIcon={<PlayArrowIcon />}
                  onClick={() => void handleStart()}
                >
                  {t('dashboard.actions.start')}
                </Button>
              )}
              {state === 'configured' && (
                <Button
                  variant="outlined"
                  startIcon={<ScheduleIcon />}
                  onClick={() => setSchedulePickerOpen((v) => !v)}
                >
                  {t('dashboard.actions.schedule')}
                </Button>
              )}
              {state === 'active' && (
                <Button
                  variant="outlined"
                  startIcon={<PauseIcon />}
                  onClick={() => void handlePause()}
                >
                  {t('dashboard.actions.pause')}
                </Button>
              )}
              {state === 'paused' && (
                <Button
                  variant="contained"
                  startIcon={<PlayArrowIcon />}
                  onClick={() => void handleResume()}
                >
                  {t('dashboard.actions.resume')}
                </Button>
              )}
              {(state === 'active' || state === 'paused') && (
                <Button
                  variant="outlined"
                  color="error"
                  startIcon={<StopIcon />}
                  onClick={() => setEndConfirmOpen(true)}
                >
                  {t('dashboard.actions.end')}
                </Button>
              )}
            </Stack>
          </Stack>

          {joinWindowKey && (
            <Typography variant="body2" sx={{ mt: 1.5 }} data-testid="join-window">
              {t(joinWindowKey)}
            </Typography>
          )}

          {/* Why a planned start did not happen. */}
          {state === 'scheduled' && autoStartBlocked && (
            <Alert severity="warning" sx={{ mt: 2 }}>
              {t(`dashboard.autoStartBlocked.${autoStartBlocked}`, {
                defaultValue: t('dashboard.autoStartBlocked.SNAPSHOT_FAILED'),
              })}
            </Alert>
          )}

          {schedulePickerOpen && (
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mt: 2, flexWrap: 'wrap' }}>
              <TextField
                label={t('dashboard.actions.scheduleDateLabel')}
                type="datetime-local"
                value={scheduleDateTime}
                onChange={(e) => setScheduleDateTime(e.target.value)}
                size="small"
                slotProps={{ inputLabel: { shrink: true } }}
              />
              <Button
                variant="contained"
                size="small"
                onClick={() => void handleSchedule()}
                disabled={!scheduleDateTime || scheduleLoading}
              >
                OK
              </Button>
              <Button size="small" onClick={() => setSchedulePickerOpen(false)}>
                {t('actions.cancel', { ns: 'common' })}
              </Button>
            </Stack>
          )}

          <Divider sx={{ my: 2 }} />

          <Stack direction="row" spacing={3} sx={{ flexWrap: 'wrap', rowGap: 2, alignItems: 'center' }}>
            {/* Teams */}
            <Stack direction="row" spacing={1.25} sx={{ alignItems: 'center' }}>
              <GroupIcon fontSize="small" sx={{ color: 'text.secondary' }} />
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                  {t('dashboard.metrics.teams')}
                </Typography>
                <Typography variant="h6" component="p" sx={{ fontWeight: 700, lineHeight: 1 }}>
                  {teams.length}
                </Typography>
              </Box>
            </Stack>

            {/* Submissions: a stat, plus its own "Öffnen" button. The stat names
                both halves, because a bare number such as "12 bewertet" with no
                total says nothing about how much is left. The navigation is a
                real button that the keyboard reaches by itself, not a clickable
                tile. */}
            <Stack direction="row" spacing={1.25} sx={{ alignItems: 'center' }}>
              <AssignmentIcon fontSize="small" sx={{ color: 'text.secondary' }} />
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                  {t('submissions.title')}
                </Typography>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                  <Typography variant="body1" component="p" sx={{ fontWeight: 700, lineHeight: 1.2 }}>
                    {t('dashboard.metrics.scoredOfTotal', {
                      scored: scoredCount,
                      total: submittedCount,
                    })}
                  </Typography>
                  {newCount > 0 && (
                    <AnswerStateChip
                      state="submitted"
                      label={t('dashboard.metrics.unscoredCount', { count: newCount })}
                      sx={{ fontWeight: 700 }}
                    />
                  )}
                  {/* Two "Öffnen" buttons sit next to each other in this row,
                      so each carries an aria-label naming its target. The
                      visible word stays inside the accessible name (WCAG 2.5.3). */}
                  <Button
                    size="small"
                    aria-label={t('dashboard.metrics.openSubmissions')}
                    onClick={() => navigate(`/runs/${runId}/submissions`)}
                    sx={{ fontWeight: 700 }}
                  >
                    {t('dashboard.metrics.open')}
                  </Button>
                </Stack>
              </Box>
            </Stack>

            {/* Scoreboard — same shape as the submissions tile above. */}
            <Stack direction="row" spacing={1.25} sx={{ alignItems: 'center' }}>
              <EmojiEventsIcon fontSize="small" sx={{ color: 'text.secondary' }} />
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                  {t('scoreboard.title')}
                </Typography>
                <Button
                  size="small"
                  aria-label={t('dashboard.metrics.openScoreboard')}
                  onClick={() => navigate(`/runs/${runId}/scoreboard`)}
                  sx={{ fontWeight: 700 }}
                >
                  {t('dashboard.metrics.open')}
                </Button>
              </Box>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      {/* Ended runs: feedback summary next to the results card (grading
          status, public link and the closing-view link). After the end this is
          the card that matters, so it stands above the join code, the teams
          and the notices, not below them. */}
      {state === 'ended' && (
        <Box
          sx={{
            mb: 3,
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
            gap: 3,
            alignItems: 'start',
          }}
        >
          {feedbackSummary && (
            <Card>
              <CardContent>
                <Typography variant="h6" component="h2" gutterBottom>
                  {t('participant.feedback.title')}
                </Typography>
                {/* An average of nothing is no grade: "Bewertung: 0.0 / 5 (0)"
                    reads as the worst possible rating. */}
                {feedbackSummary.total_count === 0 ? (
                  <Typography variant="body1" color="text.secondary">
                    {t('dashboard.feedbackEmpty')}
                  </Typography>
                ) : (
                <>
                <Typography variant="body1" gutterBottom>
                  {t('participant.feedback.rating')}: {feedbackSummary.average_rating.toFixed(1)} / 5
                  &nbsp;({feedbackSummary.total_count})
                </Typography>
                <Stack spacing={0.5} sx={{ mb: 2 }}>
                  {[5, 4, 3, 2, 1].map((star) => (
                    <Stack key={star} direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                      <Typography variant="caption" sx={{ width: 16 }}>
                        {star}
                      </Typography>
                      <Box sx={{ flexGrow: 1, bgcolor: 'grey.200', height: 12, borderRadius: 1 }}>
                        <Box
                          sx={{
                            width: `${((feedbackSummary.distribution[star] ?? 0) / maxDist) * 100}%`,
                            bgcolor: 'primary.main',
                            height: '100%',
                            borderRadius: 1,
                          }}
                        />
                      </Box>
                      <Typography variant="caption" sx={{ width: 24 }}>
                        {feedbackSummary.distribution[star] ?? 0}
                      </Typography>
                    </Stack>
                  ))}
                </Stack>
                <Stack spacing={0.5}>
                  {feedbackItems
                    .filter((f) => f.comment)
                    .map((f) => (
                      <Typography key={f.id} variant="body2" sx={{ fontStyle: 'italic' }}>
                        "{f.comment}"
                      </Typography>
                    ))}
                </Stack>
                </>
                )}
              </CardContent>
            </Card>
          )}
          <ResultLinkCard
            orgSlug={orgSlug}
            runId={runId ?? ''}
            scored={scoreboard?.scored_count ?? 0}
            total={scoreboard?.submission_count ?? 0}
            gradingComplete={scoreboard?.is_final ?? false}
          />
        </Box>
      )}

      {/* Join info, collapsible (open while fresh, auto-collapsed >= 15 min after start).
          Absent after the end: nobody can join an ended run. */}
      {state !== 'ended' && (
      <Card sx={{ mb: 3 }}>
        <CardContent>
          {/* Wraps on a narrow card: folded, the header holds the title, the
              code, "Groß anzeigen" and the fold button, which is wider than
              a 390 px window. */}
          <Stack
            direction="row"
            spacing={1}
            useFlexGap
            onClick={() => setJoinOpen((v) => !v)}
            sx={{ alignItems: 'center', flexWrap: 'wrap', cursor: 'pointer', userSelect: 'none' }}
          >
            <Typography variant="h6" component="h2">
              {t('dashboard.joinCode')}
            </Typography>
            {!joinOpen && (
              <Typography
                variant="body2"
                sx={{ fontFamily: 'monospace', letterSpacing: 2, color: 'text.secondary' }}
              >
                {run.join_code}
              </Typography>
            )}
            <Box sx={{ flexGrow: 1 }} />
            {/* In the header, so it stays in reach when the card has folded
                15 min after the start and a late group arrives. The click
                does not fold the card. */}
            <Button
              size="small"
              variant="outlined"
              startIcon={<QrCode2Icon />}
              aria-label={t('dashboard.presentation.openLabel')}
              aria-haspopup="dialog"
              onClick={(e) => {
                e.stopPropagation();
                setPresenting(true);
              }}
              sx={{ whiteSpace: 'nowrap', flex: 'none' }}
            >
              {t('dashboard.presentation.open')}
            </Button>
            {/* The state is announced with the button. */}
            <IconButton
              size="small"
              aria-label={t('dashboard.joinCode')}
              aria-expanded={joinOpen}
              aria-controls="run-join-card-details"
            >
              {joinOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            </IconButton>
          </Stack>
          <Collapse in={joinOpen} id="run-join-card-details">
            <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap', alignItems: 'flex-start', mt: 2 }}>
              <Stack spacing={1} sx={{ minWidth: 240 }}>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                  <Typography variant="body2" color="text.secondary" sx={{ minWidth: 96 }}>
                    {t('dashboard.joinCode')}
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{ fontFamily: 'monospace', fontWeight: 700, letterSpacing: 1 }}
                  >
                    {run.join_code}
                  </Typography>
                  <IconButton size="small" onClick={handleCopyCode} aria-label={t('dashboard.copyCode')}>
                    <ContentCopyIcon fontSize="small" />
                  </IconButton>
                </Stack>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                  <Typography variant="body2" color="text.secondary" sx={{ minWidth: 96 }}>
                    {t('dashboard.joinLink')}
                  </Typography>
                  <Typography
                    variant="body2"
                    component="a"
                    href={joinUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    sx={{ color: 'primary.main', wordBreak: 'break-all' }}
                  >
                    {joinUrl}
                  </Typography>
                  <IconButton
                    size="small"
                    onClick={handleCopyJoinUrl}
                    aria-label={t('dashboard.copyJoinUrl')}
                  >
                    <ContentCopyIcon fontSize="small" />
                  </IconButton>
                </Stack>
              </Stack>
              <QRCodeSVG
                value={joinUrl}
                size={128}
                title={t('dashboard.qrTitle', { code: run.join_code })}
              />
            </Box>
          </Collapse>
        </CardContent>
      </Card>
      )}

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
          gap: 3,
        }}
      >
        {/* Teams */}
        <Card>
          <CardContent>
            <Typography variant="h6" component="h2" gutterBottom>
              {t('dashboard.teamsTitle')} ({teams.length})
            </Typography>
            {teamsLoading ? (
              <LoadingSpinner minHeight={60} />
            ) : teams.length === 0 ? (
              /* The empty state names the one thing that makes teams appear. A
                 bare dash would tell the coordinator nothing. */
              <Typography color="text.secondary" variant="body2">
                {t('dashboard.teamsEmpty', { code: run.join_code })}
              </Typography>
            ) : (
              <List dense disablePadding>
                {teams.map((team) => {
                  const requesting = requestingTeamIds.has(team.id);
                  const chatOpen = team.coordinator_chat_open || requesting;
                  // The name starts with the visible "Chat öffnen", so voice
                  // control finds the button by what it shows (WCAG 2.5.3).
                  // A team without a name gets its own sentence, not a gap.
                  const chatName = team.name
                    ? t('teamChat.openChatNamed', { teamName: team.name })
                    : t('teamChat.openChatUnnamed');
                  const chatLockedId = `team-chat-locked-${team.id}`;
                  return (
                    <ListItem
                      key={team.id}
                      disablePadding
                      // Text and actions share one line while it fits, and the
                      // actions move below the text on a narrow card. As an
                      // absolute secondaryAction they would lie over the progress
                      // line at 390 px, and the names make the row taller.
                      sx={{
                        flexWrap: 'wrap',
                        columnGap: 1,
                        rowGap: 0.5,
                        py: 0.5,
                        ...(requesting && { bgcolor: 'secondary.main', borderRadius: 1, px: 1 }),
                      }}
                    >
                      <Box sx={{ flex: '1 1 220px', minWidth: 0, py: requesting ? 0.5 : 0 }}>
                      {/* The help chip sits on its own line above the name. In
                          the action corner it would lie over the team name and
                          the progress line: the card is about 445 px wide, and
                          the chip does not fit beside the name. */}
                      {requesting && (
                            <Chip
                              icon={<SupportAgentIcon />}
                              label={tAdmin('coordinatorRequest.teamRequestedHelp', { teamName: team.name })}
                              size="small"
                              onClick={() => handleOpenTeamChat(team.id)}
                              sx={{
                                bgcolor: 'text.primary',
                                color: 'background.paper',
                                '&:hover': { bgcolor: 'text.primary' },
                                '& .MuiChip-icon': { color: 'background.paper' },
                                maxWidth: '100%',
                                mb: 0.5,
                              }}
                            />
                      )}
                      <ListItemText
                        // A team whose members have not chosen a name yet would be
                        // an empty line in the list.
                        primary={team.name || t('dashboard.teamWithoutName')}
                        // The names stand above the progress line. A div,
                        // because the names line is a flex box and a <p> may
                        // not hold one.
                        slotProps={{ secondary: { component: 'div' } }}
                        secondary={(() => {
                          const progress = teamProgress.find(
                            (p) => p.team_id === team.id,
                          );
                          const summary = !progress
                            ? t('dashboard.memberCount', { count: team.member_count })
                            : t('dashboard.teamProgressSummary', {
                                members: progress.member_count,
                                stationsDone: progress.stations_completed,
                                stationsTotal: progress.stations_total,
                                challengesDone: progress.challenges_submitted,
                                challengesTotal: progress.challenges_total,
                                points: progress.total_points,
                              });
                          return (
                            <>
                              {team.members && team.members.length > 0 && (
                                <TeamMemberNames members={team.members} />
                              )}
                              <Box component="span" sx={{ display: 'block' }}>
                                {summary}
                              </Box>
                            </>
                          );
                        })()}
                      />
                      </Box>
                      <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', flex: 'none', ml: 'auto' }}>
                        <Tooltip title={t('teamMembers.manage', { teamName: team.name })}>
                          <IconButton
                            size="small"
                            aria-label={t('teamMembers.manage', { teamName: team.name })}
                            onClick={() => setMembersTeam({ id: team.id, name: team.name })}
                          >
                            <ManageAccountsIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        <Tooltip
                          describeChild
                          title={chatOpen ? chatName : t('teamChat.chatLocked')}
                        >
                          {/* MUI disabled buttons swallow pointer events, so the
                              Tooltip needs a wrapping span to receive hover.
                              Otherwise a disabled icon gives no explanation. */}
                          <span>
                            {/* Labelled, not icon-only: this is the primary
                                action on a team row, and an unlabelled speech
                                bubble does not say whether it opens a chat,
                                writes a message, or shows a history. */}
                            <Button
                              size="small"
                              variant="outlined"
                              startIcon={<ChatBubbleOutlineIcon fontSize="small" />}
                              // The coordinator chat opens on the team's request only.
                              // A live help request ("requesting") opens the window before
                              // the team list refreshes, so it also enables the button.
                              disabled={!chatOpen}
                              aria-label={chatName}
                              // Why it is locked is a description, not the name.
                              aria-describedby={chatOpen ? undefined : chatLockedId}
                              onClick={() => handleOpenTeamChat(team.id)}
                              sx={{ whiteSpace: 'nowrap' }}
                            >
                              {t('teamChat.openChatShort')}
                            </Button>
                            {!chatOpen && (
                              <Box component="span" id={chatLockedId} sx={visuallyHiddenSx}>
                                {t('teamChat.chatLocked')}
                              </Box>
                            )}
                          </span>
                        </Tooltip>
                      </Stack>
                    </ListItem>
                  );
                })}
              </List>
            )}
          </CardContent>
        </Card>

        {/* Live feed */}
        <Card>
          <CardContent>
            <Typography variant="h6" component="h2" gutterBottom>
              {t('dashboard.feedTitle')}
            </Typography>
            <FeedComposer
              orgSlug={orgSlug}
              runId={run.id}
              teams={teams}
              onPosted={addFeedEvent}
            />
            <Divider sx={{ my: 1 }} />
            {/* Without a heading the list below reads as part of the composer,
                as a preview of what you are about to send rather than what was
                already sent. */}
            {/* component="p": MUI renders subtitle2 as <h6>, which would jump
                the heading order inside the card (axe heading-order). This is a
                list label, not a document heading. */}
            <Typography variant="subtitle2" component="p" color="text.secondary" sx={{ mb: 0.5 }}>
              {t('feedComposer.pastEvents')}
            </Typography>
            {/* No live region, for the reason given at `arrivedNotice`: a screen
                reader would read every sent notice on opening the page. The
                list is a list, and kind, time and text of an entry are apart
                for a screen reader. */}
            {pastNotices.length === 0 ? (
              <Typography color="text.secondary" variant="body2">
                {t('participant.feed.empty')}
              </Typography>
            ) : (
              <List
                disablePadding
                aria-label={t('feedComposer.pastEvents')}
                sx={{ maxHeight: 300, overflowY: 'auto' }}
              >
                {pastNotices.map((ev) => (
                  <ListItem
                    key={ev.id}
                    disableGutters
                    sx={{ display: 'block', py: 0.5 }}
                  >
                    <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
                      {/* The localized type label, not the enum value. */}
                      <Typography variant="caption" color="text.secondary">
                        {t(`feedComposer.types.${ev.event_type}`)}
                      </Typography>
                      <Box component="span" sx={visuallyHiddenSx}>
                        {', '}
                      </Box>
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        component="time"
                        dateTime={ev.created_at}
                      >
                        {/* The UI language decides, not the browser locale. */}
                        {formatTime(ev.created_at, i18n.language)}
                      </Typography>
                    </Stack>
                    {/* A web address in a notice is a link, as on the
                        Mitteilungen page of the teams. */}
                    <Typography
                      variant="body2"
                      sx={{ wordBreak: 'break-word', '& a': { color: 'primary.main' } }}
                    >
                      <LinkifiedText text={feedMessage(ev.content, i18n.language)} />
                    </Typography>
                  </ListItem>
                ))}
              </List>
            )}
            {/* Announces a notice that arrives while the page is open, and
                nothing else. Empty on load. */}
            <Box role="status" aria-live="polite" sx={visuallyHiddenSx} data-testid="notice-announcement">
              {arrivedNotice &&
                `${t(`feedComposer.types.${arrivedNotice.event_type}`)}: ${feedMessage(arrivedNotice.content, i18n.language)}`}
            </Box>
          </CardContent>
        </Card>
      </Box>

      <JoinCodePresentation
        open={presenting}
        onClose={() => setPresenting(false)}
        runName={run.name}
        joinCode={run.join_code}
        joinUrl={joinUrl}
      />

      {/* Team member management, keyed by team so each team gets a fresh
          dialog state. A reset inside an effect would trip the lint rule
          react-hooks/set-state-in-effect. */}
      {runId && (
        <TeamMembersDialog
          key={membersTeam?.id ?? 'closed'}
          open={membersTeam !== null}
          onClose={() => setMembersTeam(null)}
          orgSlug={orgSlug}
          runId={runId}
          teamId={membersTeam?.id ?? null}
          teamName={membersTeamName}
          rallyeCode={run?.join_code ?? ''}
          onMemberRemoved={() => void loadTeams()}
          onTeamRemoved={() => {
            setMembersTeam(null);
            void loadTeams();
          }}
          reloadSignal={membersChanged}
        />
      )}

      {/* End confirm dialog */}
      {editOpen && run && (
        <RunEditDialog
          open={editOpen}
          orgSlug={orgSlug}
          run={run}
          onSaved={() => {
            setEditOpen(false);
            void refresh();
          }}
          onClose={() => setEditOpen(false)}
        />
      )}

      <ConfirmDialog
        open={endConfirmOpen}
        title={t('dashboard.actions.end')}
        message={t('dashboard.endConfirm')}
        confirmLabel={t('dashboard.actions.end')}
        destructive
        loading={endLoading}
        onConfirm={() => void handleEnd()}
        onCancel={() => setEndConfirmOpen(false)}
      />

      {/* A help request names a team. While the large join code is on a
          projector, the toast waits until the view closes. */}
      <Snackbar
        open={!!coordinatorRequestToast && !presenting}
        autoHideDuration={6000}
        onClose={() => setCoordinatorRequestToast(null)}
        anchorOrigin={{ vertical: 'top', horizontal: 'center' }}
      >
        <Alert
          severity="warning"
          icon={<SupportAgentIcon />}
          onClose={() => setCoordinatorRequestToast(null)}
        >
          {coordinatorRequestToast}
        </Alert>
      </Snackbar>

      <Snackbar
        open={!!copyToast && !presenting}
        autoHideDuration={3000}
        onClose={() => setCopyToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity="success" onClose={() => setCopyToast(null)}>
          {copyToast}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default RunDashboard;
