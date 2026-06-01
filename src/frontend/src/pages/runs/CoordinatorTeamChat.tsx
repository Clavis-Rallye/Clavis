import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  IconButton,
  Stack,
  Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import CloseIcon from '@mui/icons-material/Close';
import LogoutIcon from '@mui/icons-material/Logout';
import { leaveCoordinatorChat } from '../../api/admin';
import { getTeam, listChatMessages, postChatMessage } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { ChatMessage, Team } from '../../api/types';
import { useWebSocket } from '../../hooks/useWebSocket';
import ChatMessageList from '../../components/chat/ChatMessageList';
import ChatComposer from '../../components/chat/ChatComposer';
import ConfirmDialog from '../../components/ConfirmDialog';
import ErrorAlert from '../../components/ErrorAlert';

/**
 * How often a closed assist window asks the server whether the team has
 * requested help again. The help request reaches coordinators only as a run
 * feed event (feed_staff:{run_id}); this page's team socket (chat:{team_id})
 * never receives it, so the page polls the team while the window is closed.
 */
const CLOSED_WINDOW_POLL_MS = 15_000;

/**
 * The coordinator's side of a team chat, reached from the run dashboard (the
 * chat icon of a team row, or the "Hilfe" chip of a requesting team). It shows
 * the team's full history, receives new messages live over the participants'
 * WebSocket topic, and sends the coordinator's replies.
 */
const CoordinatorTeamChat: React.FC = () => {
  const { runId, teamId } = useParams<{ runId: string; teamId: string }>();
  const { user } = useAuth();
  const { t } = useTranslation('runs');
  const navigate = useNavigate();

  const orgSlug = user?.organization_slug;

  const [team, setTeam] = useState<Team | null>(null);
  // Error *key* in the 'runs' namespace, translated at render. The translated
  // text would put t() into load's dependencies. t changes identity whenever
  // i18n emits (a namespace loaded late, a language event), which would re-fire
  // both requests and race the window state.
  const [teamErrorKey, setTeamErrorKey] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [deliveryFailed, setDeliveryFailed] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [leaveFailed, setLeaveFailed] = useState(false);
  const [endDialogOpen, setEndDialogOpen] = useState(false);
  // Whether the team's coordinator assist window is still open. Seeded from
  // the team fetch, then narrowed by a `[coordinator_left]` socket message or a
  // coded 403 on send, whichever comes first.
  const [windowOpen, setWindowOpen] = useState(true);
  // The socket may only (re)connect once the message history it must not miss
  // is in state. Every close clears it. The reopen effect below sets it again
  // after the history refetch has merged or failed.
  const [socketEnabled, setSocketEnabled] = useState(true);
  // After the socket closed the window, a team answer already in flight still
  // carries the state before the close. The close (team and message timestamp)
  // is kept so the seed ignores answers that are not newer. A newer answer (a
  // new help request bumped the team's updated_at) clears it and reopens the
  // window.
  const closedAtRef = useRef<{ teamId: string; at: string } | null>(null);

  const seedWindowOpen = useCallback((data: Team) => {
    const closed = closedAtRef.current;
    if (
      closed &&
      closed.teamId === data.id &&
      data.coordinator_chat_open &&
      Date.parse(data.updated_at) <= Date.parse(closed.at)
    ) {
      // The answer raced the [coordinator_left] message, so the window stays closed.
      return;
    }
    if (data.coordinator_chat_open) closedAtRef.current = null;
    setWindowOpen(data.coordinator_chat_open);
    if (!data.coordinator_chat_open) setSocketEnabled(false);
  }, []);

  const load = useCallback(async () => {
    if (!orgSlug || !runId || !teamId) return;
    try {
      const teamData = await getTeam(orgSlug, runId, teamId);
      setTeam(teamData);
      setTeamErrorKey(null);
      seedWindowOpen(teamData);
    } catch {
      setTeamErrorKey('teamChat.loadFailed');
    }
    try {
      const resp = await listChatMessages(orgSlug, runId, teamId);
      // The endpoint returns oldest first (created_at ASC), the render order.
      // Reversing it would put the newest message on top.
      setMessages(resp.items);
    } catch {
      // Initial load failure stays silent in the UI; the WS will retry.
    }
  }, [orgSlug, runId, teamId, seedWindowOpen]);

  useEffect(() => {
    void load();
  }, [load]);

  // While the window is closed, refetch the team at a fixed interval: a new
  // help request reopens the window on the server and bumps the team's
  // updated_at past the close, so seedWindowOpen lets that answer reopen it
  // here. Polling stops once the window is open again or the page unmounts.
  useEffect(() => {
    if (windowOpen || !orgSlug || !runId || !teamId) return;
    const timer = setInterval(() => {
      getTeam(orgSlug, runId, teamId)
        .then((teamData) => {
          setTeam(teamData);
          seedWindowOpen(teamData);
        })
        .catch(() => {
          // Silent: the next tick asks again.
        });
    }, CLOSED_WINDOW_POLL_MS);
    return () => clearInterval(timer);
  }, [windowOpen, orgSlug, runId, teamId, seedWindowOpen]);

  // A reopened window must resync. While the socket was down, the team's
  // question between its help request and the reconnection never reached this
  // page, because the socket has no replay. A page loaded while the window was
  // closed could not load its history at all (CHAT_WINDOW_CLOSED). So a real
  // closed→open transition refetches the history and merges it by message id,
  // and nothing shows twice. A change of the translation function alone changes
  // neither windowOpen nor the ids below, so it never lands here. The socket is
  // re-enabled only after the refetch has merged, because an earlier restart
  // could race a live message past the refetch. A failed refetch re-enables it
  // too, so a dead fetch cannot keep the chat dead.
  const wasClosedRef = useRef(false);
  useEffect(() => {
    if (!windowOpen) {
      wasClosedRef.current = true;
      return;
    }
    if (!wasClosedRef.current) return;
    wasClosedRef.current = false;
    if (!orgSlug || !runId || !teamId) return;
    listChatMessages(orgSlug, runId, teamId)
      .then((resp) => {
        setMessages((prev) => {
          const byId = new Map(prev.map((m) => [m.id, m]));
          for (const m of resp.items) byId.set(m.id, m);
          return [...byId.values()].sort(
            (a, b) => Date.parse(a.created_at) - Date.parse(b.created_at),
          );
        });
      })
      .catch(() => {
        // Silent: the live socket covers new messages, the next visit reloads.
      })
      .finally(() => {
        setSocketEnabled(true);
      });
  }, [windowOpen, orgSlug, runId, teamId]);

  // The topic the participant TeamChat subscribes to. ws.py admits coordinator+
  // only while the team's assist window is open and otherwise rejects the
  // socket with 4003, which useWebSocket treats as non-recoverable. So
  // `enabled` follows the window state. The dead socket is torn down while the
  // window is closed, and the reopen effect re-enables it with a fresh connect
  // and a new ws-token once the history refetch has merged.
  useWebSocket({
    path:
      orgSlug && runId && teamId
        ? `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}/ws`
        : '',
    enabled: !!(orgSlug && runId && teamId) && windowOpen && socketEnabled,
    handlers: {
      chat_message: (data) => {
        const msg = data as ChatMessage;
        setMessages((prev) => {
          if (prev.some((m) => m.id === msg.id)) return prev;
          return [...prev, msg];
        });
        if (msg.is_system_message && msg.content.startsWith('[coordinator_left]')) {
          // Remember the close so a team answer already in flight (older
          // than this message) cannot reseed the window open.
          closedAtRef.current = { teamId: msg.team_id, at: msg.created_at };
          setWindowOpen(false);
          setSocketEnabled(false);
        }
      },
    },
  });

  const canSend =
    windowOpen && !!draft.trim() && !sending && !!(orgSlug && runId && teamId);

  const handleSend = async () => {
    if (!canSend) return;
    setSending(true);
    setDeliveryFailed(false);
    try {
      await postChatMessage(orgSlug!, runId!, teamId!, { content: draft.trim() });
      setDraft('');
      // The message arrives over the socket. If it does not on a slow link,
      // the next visit loads it.
    } catch (err) {
      if (err instanceof ApiError && err.errorCode === 'CHAT_WINDOW_CLOSED') {
        // The team closed the window between render and send. The windowClosed
        // banner explains this, not the generic retry banner.
        setWindowOpen(false);
        setSocketEnabled(false);
      } else {
        // Keep the draft so the coordinator can retry without retyping.
        setDeliveryFailed(true);
      }
    } finally {
      setSending(false);
    }
  };

  // Ending assistance closes the team's coordinator assist window (a
  // coordinator may leave at any time). Access ends until the team requests
  // assistance again, so it asks for confirmation naming that consequence.
  const handleEndAssistance = async () => {
    if (!orgSlug || !runId || !teamId || leaving) return;
    setEndDialogOpen(false);
    setLeaving(true);
    setLeaveFailed(false);
    try {
      await leaveCoordinatorChat(orgSlug, runId, teamId);
      navigate(`/runs/${runId}`);
    } catch {
      setLeaveFailed(true);
    } finally {
      setLeaving(false);
    }
  };

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        height: 'calc(100dvh - 220px)',
      }}
    >
      <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 1 }}>
        <IconButton
          aria-label={t('teamChat.back')}
          onClick={() => navigate(`/runs/${runId}`)}
          size="small"
        >
          <ArrowBackIcon />
        </IconButton>
        <Typography variant="h2" component="h1" sx={{ flexGrow: 1 }}>
          {t('teamChat.title', { teamName: team?.name ?? '…' })}
        </Typography>
        <Button
          size="small"
          color="inherit"
          startIcon={<LogoutIcon fontSize="small" />}
          onClick={() => setEndDialogOpen(true)}
          disabled={leaving}
        >
          {t('teamChat.endAssistance')}
        </Button>
      </Stack>

      {leaveFailed && (
        <Box sx={{ mb: 1 }}>
          <ErrorAlert message={t('teamChat.leaveFailed')} />
        </Box>
      )}

      {teamErrorKey && (
        <Box sx={{ mb: 1 }}>
          <ErrorAlert message={t(teamErrorKey)} />
        </Box>
      )}

      {!windowOpen && (
        <Alert severity="info" sx={{ mb: 1 }}>
          {t('teamChat.windowClosed')}
        </Alert>
      )}

      {deliveryFailed && (
        <Alert
          severity="error"
          sx={{ mb: 1 }}
          action={
            <Stack direction="row" spacing={0.5}>
              <Button
                color="inherit"
                size="small"
                onClick={() => void handleSend()}
                disabled={sending}
              >
                {t('participant.chat.deliveryFailedRetry')}
              </Button>
              <IconButton
                color="inherit"
                size="small"
                onClick={() => {
                  setDeliveryFailed(false);
                  setDraft('');
                }}
                aria-label={t('participant.chat.deliveryFailedDismiss')}
              >
                <CloseIcon fontSize="small" />
              </IconButton>
            </Stack>
          }
        >
          {t('participant.chat.deliveryFailed')}
        </Alert>
      )}

      <ChatMessageList
        messages={messages}
        currentUserId={user?.id}
        emptySubtitle={t('teamChat.emptySubtitle')}
        label={t('teamChat.title', { teamName: team?.name ?? '…' })}
      />

      <ChatComposer
        draft={draft}
        onDraftChange={setDraft}
        onSend={() => void handleSend()}
        canSend={canSend}
      />

      <ConfirmDialog
        open={endDialogOpen}
        title={t('teamChat.endAssistance')}
        message={t('teamChat.endAssistanceConfirm')}
        confirmLabel={t('teamChat.endAssistance')}
        destructive
        loading={leaving}
        onConfirm={() => void handleEndAssistance()}
        onCancel={() => setEndDialogOpen(false)}
      />
    </Box>
  );
};

export default CoordinatorTeamChat;
