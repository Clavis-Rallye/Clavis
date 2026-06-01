import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, IconButton, InputBase, Typography } from '@mui/material';
import SendIcon from '@mui/icons-material/Send';

const CHAT_MAX_CHARS = 2000;

interface ChatComposerProps {
  draft: string;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  /** Whether the send action is currently allowed (non-empty draft, not sending …). */
  canSend: boolean;
  /**
   * Why writing is closed altogether, if it is. Set this and the whole composer
   * is replaced by the sentence — the field stops accepting text instead of
   * letting someone type a message the server will only reject.
   */
  closedReason?: string;
}

/**
 * Message composer pill shared between the participant TeamChat and the
 * coordinator team-chat page. The owning page keeps the draft
 * state so it can preserve the text on delivery failure (retry banner).
 */
const ChatComposer: React.FC<ChatComposerProps> = ({
  draft,
  onDraftChange,
  onSend,
  canSend,
  closedReason,
}) => {
  const { t } = useTranslation('runs');

  const handleKeyDown = (e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (canSend) onSend();
    }
  };

  if (closedReason) {
    return (
      <Typography
        variant="body2"
        color="text.secondary"
        sx={{ mt: 1, px: 1.5, py: 1.5, textAlign: 'center' }}
      >
        {closedReason}
      </Typography>
    );
  }

  return (
    <>
      <Box
        sx={{
          mt: 1,
          display: 'flex',
          alignItems: 'flex-end',
          gap: 1,
          bgcolor: 'background.paper',
          border: 1,
          borderColor: 'divider',
          borderRadius: 4,
          px: 1.5,
          py: 0.75,
          minHeight: 44,
        }}
      >
        <InputBase
          value={draft}
          onChange={(e) => onDraftChange(e.target.value.slice(0, CHAT_MAX_CHARS))}
          onKeyDown={handleKeyDown}
          placeholder={t('participant.chat.placeholder')}
          inputProps={{
            'aria-label': t('participant.chat.messageLabel'),
            maxLength: CHAT_MAX_CHARS,
          }}
          multiline
          maxRows={4}
          sx={{
            flex: 1,
            minWidth: 0,
            fontSize: 14,
            // Make the textarea grow with content but stay anchored at the
            // bottom of the pill so the send button never floats above it.
            alignSelf: 'stretch',
            '& textarea': { resize: 'none' },
          }}
        />
        <IconButton
          color="primary"
          onClick={onSend}
          disabled={!canSend}
          aria-label={t('participant.chat.send')}
          size="small"
          sx={{
            flexShrink: 0,
            bgcolor: canSend ? 'primary.main' : 'action.disabledBackground',
            color: canSend ? 'primary.contrastText' : 'action.disabled',
            width: 36,
            height: 36,
            '&:hover': { bgcolor: canSend ? 'primary.dark' : 'action.disabledBackground' },
            '&.Mui-disabled': {
              bgcolor: 'action.disabledBackground',
              color: 'action.disabled',
            },
          }}
        >
          <SendIcon fontSize="small" />
        </IconButton>
      </Box>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ mt: 0.5, alignSelf: 'flex-end' }}
      >
        {t('participant.chat.charCount', { count: draft.length })}
      </Typography>
    </>
  );
};

export default ChatComposer;
