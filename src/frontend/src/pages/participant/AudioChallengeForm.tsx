import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  LinearProgress,
  Stack,
  Typography,
} from '@mui/material';
import { createSubmission } from '../../api/runs';
import { uploadFile } from '../../api/files';
import ErrorAlert from '../../components/ErrorAlert';
import type { ChallengeFormProps } from './TextChallengeForm';
import { SubmitAnswerButton, useAnswerSend } from './TextChallengeForm';
import TextFallbackField from './TextFallbackField';

const AudioChallengeForm: React.FC<ChallengeFormProps> = ({
  orgSlug,
  runId,
  challenge,
  alreadySubmitted,
  onSubmitted,
  submitAction,
  initialContent,
  onReload,
}) => {
  const { t } = useTranslation('runs');
  const [file, setFile] = useState<File | null>(null);
  // The text fallback. It is prefilled and open when the answer being replaced
  // carried a text.
  const initialText = typeof initialContent?.text === 'string' ? initialContent.text : '';
  const [text, setText] = useState(initialText);
  const [textOpen, setTextOpen] = useState(initialText !== '');
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const { submitting, begin, end, report } = useAnswerSend();
  const [error, setError] = useState<string | null>(null);
  const recordRef = useRef<HTMLInputElement | null>(null);
  const libraryRef = useRef<HTMLInputElement | null>(null);
  // Track current object URL for cleanup on unmount.
  const activeUrlRef = useRef<string | null>(null);

  // Revoke the object URL on unmount only. No setState here.
  useEffect(() => {
    return () => {
      if (activeUrlRef.current) URL.revokeObjectURL(activeUrlRef.current);
    };
  }, []);

  if (alreadySubmitted) {
    return (
      <Typography variant="body2" sx={{
        color: "text.secondary"
      }}>
        {t('participant.challenge.alreadySubmitted')}
      </Typography>
    );
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0] ?? null;
    // Revoke the previous object URL in the event handler (not during render).
    if (activeUrlRef.current) URL.revokeObjectURL(activeUrlRef.current);
    activeUrlRef.current = selected ? URL.createObjectURL(selected) : null;
    setPreviewUrl(activeUrlRef.current);
    setFile(selected);
    setError(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const fallbackText = text.trim();
    if (!file && !fallbackText) return;
    if (!begin(e.currentTarget as HTMLFormElement)) return;
    setError(null);
    if (file) setUploadProgress(0);
    // What this member tried to send. A teammate's earlier answer refuses it,
    // and the view shows it instead of dropping it.
    let attempted: ChallengeFormProps['initialContent'];
    try {
      const content: Record<string, string> = {};
      if (file) {
        const uploaded = await uploadFile(orgSlug, runId, file, setUploadProgress);
        setUploadProgress(100);
        content.audio_url = uploaded.object_key;
      }
      if (fallbackText) content.text = fallbackText;
      attempted = content;
      const result = submitAction
        ? await submitAction(content)
        : await createSubmission(orgSlug, runId, {
            run_challenge_id: challenge.id,
            content,
          });
      onSubmitted(result);
    } catch (err) {
      setUploadProgress(null);
      await report(err, attempted, onReload, setError);
      end();
    }
  };

  return (
    <Box component="form" onSubmit={handleSubmit}>
      <Stack spacing={2}>
        {error && <ErrorAlert message={error} />}

        {previewUrl && (
          <Box
            component="audio"
            controls
            src={previewUrl}
            aria-label={t('participant.challenge.audio.preview')}
            sx={{ width: '100%' }}
          />
        )}

        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            onClick={() => recordRef.current?.click()}
            disabled={submitting}
          >
            {t('participant.challenge.audio.record')}
          </Button>
          <Button
            variant="outlined"
            onClick={() => libraryRef.current?.click()}
            disabled={submitting}
          >
            {t('participant.challenge.audio.library')}
          </Button>
        </Stack>

        {/* Hidden file inputs. `capture` hands the job to the voice recorder of
            the phone. The camera-facing values mean nothing for audio. */}
        <input
          ref={recordRef}
          type="file"
          accept="audio/*"
          capture
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />
        <input
          ref={libraryRef}
          type="file"
          accept="audio/*"
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />

        <TextFallbackField
          kind="audio"
          value={text}
          onChange={setText}
          open={textOpen}
          onOpen={() => setTextOpen(true)}
          disabled={submitting}
        />

        {uploadProgress !== null && (
          <Box>
            <Typography variant="caption">
              {t('participant.challenge.audio.uploading')}
            </Typography>
            <LinearProgress variant="determinate" value={uploadProgress} />
          </Box>
        )}

        <SubmitAnswerButton submitting={submitting} disabled={!file && !text.trim()} />
      </Stack>
    </Box>
  );
};

export default AudioChallengeForm;
