import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Typography } from '@mui/material';
import { getFileUrl } from '../api/files';
import type { Submission } from '../api/types';
import { AnswerStateIcon } from './AnswerState';

interface SubmittedAnswerProps {
  /** Optional: a submission with no content must not take the view down. */
  content: Submission['content'] | undefined;
  /**
   * The caption above the answer, "Eure Antwort" by default. null leaves it
   * out where the context names the content itself: the conflict view's
   * "Das wolltest du senden:" is not the team's answer.
   */
  caption?: string | null;
}

/**
 * A team's own answer, played back to it. The team reads what the coordinator
 * sees, without pressing "change answer" and entering the edit flow.
 *
 * It renders the content keys that the challenge types write today. A new
 * challenge type that writes a new content key must add it here.
 */
const SubmittedAnswer: React.FC<SubmittedAnswerProps> = ({ content, caption }) => {
  const { t } = useTranslation('runs');

  if (!content) return null;

  const text = typeof content.text === 'string' ? content.text : null;
  const photoUrl = typeof content.photo_url === 'string' ? content.photo_url : null;
  const audioUrl = typeof content.audio_url === 'string' ? content.audio_url : null;
  const videoUrl = typeof content.video_url === 'string' ? content.video_url : null;
  // QUIZ writes `selected_option`. QR writes `scanned_value`, the key the
  // backend validates and scores automatically.
  const option = typeof content.selected_option === 'string' ? content.selected_option : null;
  const scanned = typeof content.scanned_value === 'string' ? content.scanned_value : null;
  // Set by the org-admin media purge. The medium is deleted and the answer
  // itself stays, so the playback says so instead of loading it.
  const mediaRemoved = content.media_removed === true;

  if (!text && !photoUrl && !audioUrl && !videoUrl && !option && !scanned && !mediaRemoved) {
    return null;
  }

  return (
    <Box sx={{ mt: 1.5 }}>
      {caption !== null && (
        <Typography variant="caption" color="text.secondary" component="p" sx={{ mb: 0.5 }}>
          {caption ?? t('participant.challenge.yourAnswer')}
        </Typography>
      )}

      {text && (
        <Box sx={{ p: 1.5, bgcolor: 'action.hover', borderRadius: 1, whiteSpace: 'pre-wrap' }}>
          <Typography variant="body2">{text}</Typography>
        </Box>
      )}

      {option && <Typography variant="body2">{option}</Typography>}

      {scanned && (
        <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
          {scanned}
        </Typography>
      )}

      {mediaRemoved && (
        <Typography
          variant="body2"
          sx={{ color: 'text.secondary', display: 'flex', alignItems: 'center', gap: 0.75 }}
        >
          <AnswerStateIcon state="mediaRemoved" decorative fontSize="small" />
          {t('participant.challenge.mediaRemoved')}
        </Typography>
      )}

      {photoUrl && (
        <Box
          component="img"
          // The display variant, not the original: playback to the team must
          // not pull the full-resolution photo.
          src={getFileUrl(photoUrl, 'display')}
          alt={t('participant.challenge.yourAnswer')}
          sx={{ maxWidth: '100%', maxHeight: 260, borderRadius: 1, display: 'block' }}
        />
      )}

      {audioUrl && (
        <Box
          component="audio"
          controls
          aria-label={t('participant.challenge.audioAnswer')}
          src={getFileUrl(audioUrl)}
          sx={{ width: '100%', mt: 0.5 }}
        />
      )}

      {videoUrl && (
        <Box
          component="video"
          controls
          playsInline
          aria-label={t('participant.challenge.videoAnswer')}
          src={getFileUrl(videoUrl)}
          sx={{ maxWidth: '100%', maxHeight: 260, borderRadius: 1, display: 'block', mt: 0.5 }}
        />
      )}
    </Box>
  );
};

export default SubmittedAnswer;
