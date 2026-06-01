import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Card, CardContent, Chip, Divider, Typography } from '@mui/material';
import ChatBubbleOutlineIcon from '@mui/icons-material/ChatBubbleOutlined';
import SupportAgentIcon from '@mui/icons-material/SupportAgent';
import type { ChatMessage } from '../../api/types';
import { formatTime } from '../../utils/formatDate';

/** Roles whose messages render as "coordinator" bubbles with a role chip. */
const COORDINATOR_ROLES = ['coordinator', 'creator', 'org_admin'];

interface ChatMessageListProps {
  /** Messages in oldest-first order. */
  messages: ChatMessage[];
  /** Messages from this user render right-aligned as "own" bubbles. */
  currentUserId?: string;
  /** Override for the empty-state subtitle (defaults to the participant copy). */
  emptySubtitle?: string;
  /** Accessible name of the list. The list scrolls, so it takes the keyboard and needs one. */
  label?: string;
}

/**
 * Scrollable chat history shared between the participant TeamChat and the
 * coordinator team-chat page.
 *
 * Renders system messages as divider rows, the current user's messages
 * right-aligned, and coordinator+ messages from other users with a
 * distinct bubble colour plus a role chip next to the sender label.
 */
const ChatMessageList: React.FC<ChatMessageListProps> = ({
  messages,
  currentUserId,
  emptySubtitle,
  label,
}) => {
  const { t, i18n } = useTranslation('runs');
  const listRef = useRef<HTMLDivElement | null>(null);

  // Autoscroll to newest message whenever the list grows.
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <Box
      ref={listRef}
      role="log"
      tabIndex={0}
      aria-label={label}
      aria-live="polite"
      aria-atomic={false}
      sx={{
        flex: 1,
        overflowY: 'auto',
        bgcolor: 'sectionBackground',
        borderRadius: 2,
        px: 1.5,
        py: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        gap: 0.5,
        '&:focus-visible': { outlineOffset: -2 },
      }}
    >
      {messages.length === 0 ? (
        <Card variant="outlined" sx={{ textAlign: 'center', m: 'auto', py: 3, maxWidth: 360 }}>
          <CardContent>
            <Box
              sx={{
                width: 56,
                height: 56,
                borderRadius: '50%',
                bgcolor: 'primary.main',
                color: 'primary.contrastText',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                mb: 1.5,
              }}
            >
              <ChatBubbleOutlineIcon />
            </Box>
            <Typography variant="subtitle1" component="p" gutterBottom>
              {t('participant.chat.emptyTitle')}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {emptySubtitle ?? t('participant.chat.emptySubtitle')}
            </Typography>
          </CardContent>
        </Card>
      ) : (
        messages.map((m, idx) => {
          const prev = messages[idx - 1];
          const isMine = !!currentUserId && m.author_id === currentUserId;
          const isCoordinator =
            !isMine && !!m.author_role && COORDINATOR_ROLES.includes(m.author_role);
          const sameAuthorAsPrev =
            prev && prev.author_id === m.author_id && prev.is_system_message === m.is_system_message;
          const time = formatTime(m.created_at, i18n.language);

          if (m.is_system_message) {
            // Marker-prefixed system messages are localized client-side
            // (the backend stores a neutral English fallback).
            const systemText = m.content.startsWith('[coordinator_request]')
              ? t('teamChat.coordinatorRequestedUnnamed')
              : m.content.startsWith('[coordinator_left]')
                ? t('teamChat.coordinatorLeft')
                : m.content.startsWith('[coordinator_joined]')
                  ? t('teamChat.coordinatorJoined')
                  : m.content;
            return (
              <Box
                key={m.id}
                sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', my: 0.75 }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                  <Divider sx={{ flex: 1 }} />
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ fontStyle: 'italic', whiteSpace: 'pre-wrap' }}
                  >
                    {systemText}
                  </Typography>
                  <Divider sx={{ flex: 1 }} />
                </Box>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{
                    mt: 0.25,
                    mx: 1,
                    fontSize: 11,
                  }}
                >
                  {time}
                </Typography>
              </Box>
            );
          }

          // Prefer the server-resolved display name; fall back to
          // a neutral label for anonymized/deleted authors (author_id present
          // but no name), and to an em dash in the unexpected case where even
          // author_id is missing.
          const senderLabel = isMine
            ? t('participant.chat.you')
            : (m.author_display_name ?? (m.author_id ? t('participant.chat.anonymousSender') : '—'));

          return (
            <Box
              key={m.id}
              sx={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: isMine ? 'flex-end' : 'flex-start',
                mt: sameAuthorAsPrev ? 0.25 : 1,
              }}
            >
              {!sameAuthorAsPrev && !isMine && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.25, ml: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    {senderLabel}
                  </Typography>
                  {isCoordinator && (
                    <Chip
                      icon={<SupportAgentIcon />}
                      label={t('teamChat.coordinatorRole')}
                      size="small"
                      color="secondary"
                      sx={{ height: 18, fontSize: 11 }}
                    />
                  )}
                </Box>
              )}
              <Box
                sx={{
                  maxWidth: '78%',
                  bgcolor: isMine
                    ? 'primary.main'
                    : isCoordinator
                      ? 'secondary.light'
                      : 'background.paper',
                  color: isMine ? 'primary.contrastText' : 'text.primary',
                  border: isMine ? 'none' : 1,
                  borderColor: isCoordinator ? 'secondary.main' : 'divider',
                  px: 1.5,
                  py: 1,
                  borderRadius: 3,
                  borderTopLeftRadius: !isMine && !sameAuthorAsPrev ? 4 : undefined,
                  borderTopRightRadius: isMine && !sameAuthorAsPrev ? 4 : undefined,
                }}
              >
                <Typography
                  variant="body2"
                  sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                >
                  {m.content}
                </Typography>
              </Box>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{
                  mt: 0.25,
                  mx: 1,
                  fontSize: 11,
                }}
              >
                {time}
              </Typography>
            </Box>
          );
        })
      )}
    </Box>
  );
};

export default ChatMessageList;
