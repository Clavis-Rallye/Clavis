import React from 'react';
import { useTranslation } from 'react-i18next';
import { Button, TextField } from '@mui/material';
import EditNoteIcon from '@mui/icons-material/EditNote';

/** The bound of a text answer, the same as on a text task (routers/submissions.py). */
const TEXT_FALLBACK_MAX_CHARS = 5000;

interface TextFallbackFieldProps {
  /** The medium the task asks for. It names what does not work. */
  kind: 'photo' | 'video' | 'audio';
  value: string;
  onChange: (text: string) => void;
  open: boolean;
  onOpen: () => void;
  disabled?: boolean;
}

/**
 * A text answer for a photo, video or audio task.
 *
 * The camera or the microphone fails sometimes: no permission, a full phone,
 * an upload that never ends. A text answer is then always possible. It sits
 * behind one tap, so the medium stays the first way. The team may send the
 * medium, the text, or both, and the coordinator sees both in the grading
 * view.
 */
const TextFallbackField: React.FC<TextFallbackFieldProps> = ({ kind, value, onChange, open, onOpen, disabled }) => {
  const { t } = useTranslation('runs');
  if (!open) {
    return (
      <Button
        variant="text"
        startIcon={<EditNoteIcon />}
        onClick={onOpen}
        disabled={disabled}
        sx={{ alignSelf: 'flex-start', minHeight: 44, textAlign: 'left' }}
      >
        {t(`participant.challenge.fallback.open.${kind}`)}
      </Button>
    );
  }
  return (
    <TextField
      label={t('participant.challenge.fallback.label')}
      multiline
      fullWidth
      minRows={3}
      autoFocus={!value}
      value={value}
      onChange={(e) => onChange(e.target.value.slice(0, TEXT_FALLBACK_MAX_CHARS))}
      helperText={t('participant.challenge.text.charCount', { count: value.length })}
      disabled={disabled}
    />
  );
};

export default TextFallbackField;
