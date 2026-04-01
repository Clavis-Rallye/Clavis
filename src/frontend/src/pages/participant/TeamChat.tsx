import React, { useEffect, useRef, useState } from 'react';
import { useOutletContext, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  IconButton,
  Snackbar,
  Stack,
  Typography,
} from '@mui/material';
import SupportAgentIcon from '@mui/icons-material/SupportAgent';
import CloseIcon from '@mui/icons-material/Close';
import { listChatMessages, postChatMessage } from '../../api/runs';
import { requestCoordinator } from '../../api/admin';
import { useAuth } from '../../auth/useAuth';
import type { ChatMessage, Team } from '../../api/types';
import type { PublicRunState } from '../../services/publicRuns';
import { useWebSocket } from '../../hooks/useWebSocket';
import LoadingSpinner from '../../components/LoadingSpinner';
import ErrorAlert from '../../components/ErrorAlert';
import ConfirmDialog from '../../components/ConfirmDialog';
import ChatMessageList from '../../components/chat/ChatMessageList';
import ChatComposer from '../../components/chat/ChatComposer';

interface TeamTabOutletContext {
  team: Team | null;
  run: PublicRunState | null;
  /** What the shell knows of the caller's membership in this run. */
  membership?: 'unknown' | 'present' | 'none' | 'error';
  /** Asks the server for the membership again after a failed lookup. */
  retryMembership?: () => void;
}

const TeamChat: React.FC = () => {
  const { runId } = useParams<{ runId: string }>();
  const { user } = useAuth();
  const { t } = useTranslation('runs');
  const { t: tAdmin } = useTranslation('admin');
  // Optional: TeamChat is normally nested under the Outlet of TeamTab, which
  // supplies `team`. Tests and other embeddings must not crash without it, and
  // `useOutletContext()` is undefined outside a matching Outlet.
  const outlet = useOutletContext<TeamTabOutletContext>();
  const team = outlet?.team ?? null;
  const run = outlet?.run ?? null;

  const orgSlug = user?.organization_slug;
  // The outlet team is the source of truth. The shell switches it live on
  // `team_member_moved`. The `sessionStorage` id only serves as a fallback
  // while the shell has no team to hand down (mount, failed fetch, embeddings
  // without an outlet). After a move the shell re-points the stored id before
  // the team changes, so the fallback never wins.
  const teamId =
    team?.id ??
    (runId ? sessionStorage.getItem(`clavis_team_for_run_${runId}`) : null);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [deliveryFailed, setDeliveryFailed] = useState(false);
  const [confirmRequestOpen, setConfirmRequestOpen] = useState(false);
  const [requestingCoordinator, setRequestingCoordinator] = useState(false);
  const [requestSuccess, setRequestSuccess] = useState(false);
  // Whether the coordinator assist window is open. It is seeded from the team
  // fetch (RunShell, TeamTab, here) so it survives reloads and does not live in
  // the request state alone. Otherwise the button would revert to "Request
  // help" once the success toast dismissed, while a request was still live.
  const [coordinatorChatOpen, setCoordinatorChatOpen] = useState(
    () => team?.coordinator_chat_open ?? false,
  );
  useEffect(() => {
    if (team) setCoordinatorChatOpen(team.coordinator_chat_open);
  }, [team]);
  // While a request is in flight or live the button does nothing, but it stays
  // focusable (`aria-disabled`, never `disabled`). A disabled button cannot
  // hold the focus. The closing dialog would hand the focus back to a button
  // that had just been disabled, and it would fall to the root of the page.
  const requestButtonRef = useRef<HTMLButtonElement>(null);
  const requestInert = requestingCoordinator || coordinatorChatOpen;
  // The dialog hands the focus back to the element that opened it. Where a
  // click does not focus a button (Safari), that element is the page's root.
  // So the focus is put on the button once, in the commit that closes the
  // dialog: this effect runs after the dialog's own focus restore. It runs at
  // "Bestätigen" and not when the answer arrives, so it never pulls the focus
  // from a place the person has moved on to, like the message field.
  useEffect(() => {
    if (requestingCoordinator) requestButtonRef.current?.focus();
  }, [requestingCoordinator]);

  // This view can be reached without a remount between teams, such as a plain
  // outlet-context rerender in a test or an embedding that skips the key
  // remount of RunShell. A request for the old team can then still be in
  // flight when `teamId` changes. The cleanup below marks it stale, so its
  // response never overwrites the messages of the new team.
  useEffect(() => {
    if (!orgSlug || !runId || !teamId) return;
    let stale = false;
    listChatMessages(orgSlug, runId, teamId)
      .then((resp) => {
        // The endpoint orders by created_at ASC, oldest first, which is
        // already the render order. Reversing it would put the newest message
        // at the top.
        if (!stale) setMessages(resp.items);
      })
      .catch(() => {
        // Initial load failure stays silent in the UI; the WS will retry.
      });
    return () => {
      stale = true;
    };
  }, [orgSlug, runId, teamId]);

  useWebSocket({
    path:
      orgSlug && runId && teamId
        ? `/api/orgs/${orgSlug}/runs/${runId}/teams/${teamId}/ws`
        : '',
    enabled: !!(orgSlug && runId && teamId),
    handlers: {
      chat_message: (data) => {
        const msg = data as ChatMessage;
        setMessages((prev) => {
          if (prev.some((m) => m.id === msg.id)) return prev;
          return [...prev, msg];
        });
        if (msg.is_system_message && msg.content.startsWith('[coordinator_left]')) {
          setCoordinatorChatOpen(false);
        }
      },
    },
  });

  // The backend only accepts chat while the run is active. Say so in the UI
  // instead of letting someone write a message that comes back rejected, with
  // an English server string in an otherwise German app. While the state is
  // still loading, `run` is null. Stay open then, rather than flashing a
  // "closed" notice at someone who can in fact write.
  const closedReason =
    run && run.state !== 'active'
      ? t(
          run.state === 'ended'
            ? 'participant.chat.closedEnded'
            : run.state === 'paused'
              ? 'participant.chat.closedPaused'
              : 'participant.chat.closedNotStarted',
        )
      : undefined;

  const canSend =
    !closedReason && !!draft.trim() && !sending && !!(orgSlug && runId && teamId);

  const handleSend = async () => {
    if (!canSend) return;
    setSending(true);
    setDeliveryFailed(false);
    try {
      await postChatMessage(orgSlug!, runId!, teamId!, { content: draft.trim() });
      setDraft('');
      // The message arrives via WS. If it does not (slow link), the next
      // visit picks it up on reload.
    } catch {
      // Show a friendly banner and keep the draft intact, so the user can
      // retry without retyping.
      setDeliveryFailed(true);
    } finally {
      setSending(false);
    }
  };

  const handleRequestCoordinator = async () => {
    if (!orgSlug || !runId || !teamId) return;
    setConfirmRequestOpen(false);
    setRequestingCoordinator(true);
    try {
      await requestCoordinator(orgSlug, runId, teamId);
      setRequestSuccess(true);
      // The backend opens the assist window synchronously on request. Reflect
      // that at once instead of waiting for a refetch.
      setCoordinatorChatOpen(true);
    } catch {
      // The request failed, for example on the network. The user can retry.
    } finally {
      setRequestingCoordinator(false);
    }
  };

  if (!teamId) {
    // A run page opened without the stored key. The shell is still asking the
    // server, and "no team" is not the answer yet.
    if (outlet?.membership === 'unknown') return <LoadingSpinner />;
    if (outlet?.membership === 'error') {
      return <ErrorAlert message={t('team.info.lookupFailed')} onRetry={outlet.retryMembership} />;
    }
    return (
      <Box sx={{ py: 2 }}>
        <Typography variant="h2" component="h1" gutterBottom>
          {t('participant.chat.title')}
        </Typography>
        <Typography variant="body1" color="text.secondary">
          {t('participant.chat.noTeam')}
        </Typography>
      </Box>
    );
  }

  return (
    // No height of its own. The chat is the last link of the flex column that
    // the run shell and the team tab build from the header to the bottom
    // navigation. It takes what they leave and may shrink, so the message list
    // scrolls inside it and the input row stays at its bottom edge. A fixed
    // subtracted number would put the input below the screen as soon as the
    // header, the code line and the tabs outgrew it.
    <Box
      sx={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        minHeight: 0,
      }}
    >
      <Stack
        direction="row"
        sx={{
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 1,
          mb: 1,
          flexShrink: 0,
        }}
      >
        {/* Below 360 px the heading and the help button do not fit side by
            side at the full heading size, and the heading would run under the
            button at 320 px. It steps down there and keeps its size everywhere
            else. */}
        <Typography
          variant="h2"
          component="h1"
          sx={{ '@media (max-width: 359.95px)': { fontSize: '1.5rem' } }}
        >
          {t('participant.chat.title')}
        </Typography>
        {/* Calling coordination is not destructive, so the label is `text.primary`
            at the theme button size, not `warning.main` at 13 px — MUI's
            `warning.main` misses 4.5:1 on this surface. */}
        <Button
          ref={requestButtonRef}
          variant="outlined"
          color="inherit"
          size="medium"
          sx={{
            // The `:not()` guard keeps the greyed-out look for the inert
            // state, which this button reaches while a request is in flight
            // or live. The second rule draws that look as MUI draws `disabled`.
            '&:not([aria-disabled="true"])': { color: 'text.primary', borderColor: 'text.secondary' },
            '&[aria-disabled="true"]': {
              color: 'action.disabled',
              borderColor: 'action.disabledBackground',
              pointerEvents: 'none',
              cursor: 'default',
            },
            minHeight: 44,
          }}
          startIcon={<SupportAgentIcon />}
          onClick={() => {
            // Enter and Space still reach an `aria-disabled` button.
            if (!requestInert) setConfirmRequestOpen(true);
          }}
          aria-disabled={requestInert || undefined}
          disableRipple={requestInert}
        >
          {coordinatorChatOpen
            ? tAdmin('coordinatorRequest.pendingLabel')
            : tAdmin('coordinatorRequest.buttonLabel')}
        </Button>
      </Stack>

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

      <ChatMessageList messages={messages} currentUserId={user?.id} label={t('participant.chat.title')} />

      <ChatComposer
        draft={draft}
        onDraftChange={setDraft}
        onSend={() => void handleSend()}
        canSend={canSend}
        closedReason={closedReason}
      />

      <ConfirmDialog
        open={confirmRequestOpen}
        title={tAdmin('coordinatorRequest.buttonLabel')}
        message={tAdmin('coordinatorRequest.confirmMessage')}
        onConfirm={() => void handleRequestCoordinator()}
        onCancel={() => setConfirmRequestOpen(false)}
      />

      <Snackbar
        open={requestSuccess}
        autoHideDuration={4000}
        onClose={() => setRequestSuccess(false)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity="success" onClose={() => setRequestSuccess(false)}>
          {tAdmin('coordinatorRequest.success')}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default TeamChat;
