import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, CircularProgress, Stack, TextField, Typography } from '@mui/material';
import { createSubmission } from '../../api/runs';
import type { RunChallenge, Submission } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import { mapSubmissionError } from './submissionErrors';

const MAX_CHARS = 5000;

/** How long a form waits for the view's reload before it shows a refusal itself. */
const REFUSAL_RELOAD_WAIT_MS = 5_000;

export interface ChallengeFormProps {
  orgSlug: string;
  runId: string;
  challenge: RunChallenge;
  alreadySubmitted: boolean;
  onSubmitted: (submission: Submission) => void;
  /** Replace mode: the form calls this instead of createSubmission. */
  submitAction?: (content: Submission['content']) => Promise<Submission>;
  /** Existing submission content to prefill the form with (replace mode). */
  initialContent?: Submission['content'];
  /**
   * Called when a refusal means the server no longer matches the screen, and
   * the view reloads. After a teammate's earlier answer (TEAM_ALREADY_SUBMITTED)
   * it receives what this member tried to send, so the view can show it. Its
   * promise may then say whether the view now shows the team's answer.
   */
  onReload?: (attempted?: Submission['content']) => Promise<boolean> | void;
}

interface SubmitAnswerButtonProps {
  /** The answer is on its way to the server. */
  submitting: boolean;
  /** Nothing to send yet. While `submitting` the button is aria-disabled. */
  disabled?: boolean;
}

/**
 * The send state of every task form. `begin` refuses a second send while one
 * is on its way, by a ref set before the request and by the busy state of the
 * render the handler belongs to, so a press between the result and the next
 * render is refused too. It also keeps the focus on the send button. A field
 * that turns disabled with the request (Enter in a radio or in the code field
 * sent the form) would drop the focus to the page body.
 */
// The hook sits beside the button it serves; the six forms import both.
// eslint-disable-next-line react-refresh/only-export-components
export function useAnswerSend() {
  const [submitting, setSubmitting] = useState(false);
  const inFlight = useRef(false);
  const reloadTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // The wait for the reload ends with the form.
  useEffect(() => () => clearTimeout(reloadTimer.current), []);
  const begin = (form: HTMLFormElement): boolean => {
    if (inFlight.current || submitting) return false;
    inFlight.current = true;
    setSubmitting(true);
    const button = form.querySelector<HTMLElement>('button[type="submit"]');
    // Safari does not focus a button on tap, so the focus can already be on
    // the page body when the send starts.
    const active = document.activeElement;
    if (button && (form.contains(active) || !active || active === document.body)) button.focus();
    return true;
  };
  const end = () => {
    inFlight.current = false;
    setSubmitting(false);
  };
  /**
   * What a form does with a failed send. The error is its inline alert, the
   * one live announcement. The exception is a teammate's earlier answer: the
   * view then shows the team's answer with a notice that takes the focus, so
   * the form shows no alert while the view reloads. The wait is bounded,
   * because the API client has no timeout: on a stalled connection the refusal
   * would never be spoken and the button never free. After
   * REFUSAL_RELOAD_WAIT_MS, or when the reload fails or shows nothing, the form
   * shows the refusal as any other error. A reload that shows the team's answer
   * later still brings the notice.
   */
  const report = async (
    err: unknown,
    attempted: Submission['content'] | undefined,
    onReload: ChallengeFormProps['onReload'],
    setError: (message: string) => void,
  ): Promise<void> => {
    const { message, reload, teammate } = mapSubmissionError(err);
    if (reload && teammate && onReload) {
      const timedOut = Symbol('timeout');
      const shown = await Promise.race([
        Promise.resolve(onReload(attempted)),
        new Promise<typeof timedOut>((resolve) => {
          reloadTimer.current = setTimeout(() => resolve(timedOut), REFUSAL_RELOAD_WAIT_MS);
        }),
      ]);
      clearTimeout(reloadTimer.current);
      if (shown !== true) setError(message);
      return;
    }
    setError(message);
    if (reload) void onReload?.(undefined);
  };
  return { submitting, begin, end, report };
}

/**
 * The send button of every task form. From the tap until the server answers
 * it says that the answer is being sent and shows a spinner, because a button
 * that only turns grey reads as "nothing happened" on a slow connection. The
 * spinner sits beside the words as in the other pending buttons of the app,
 * and the words carry the state for a screen reader.
 */
export const SubmitAnswerButton: React.FC<SubmitAnswerButtonProps> = ({
  submitting,
  disabled = false,
}) => {
  const { t } = useTranslation('runs');
  return (
    <Button
      type="submit"
      size="large"
      variant="contained"
      color="primary"
      // While the request runs the button is only aria-disabled. A natively
      // disabled button drops the focus to the page body. Its form refuses
      // the second send. It still looks disabled.
      disabled={disabled}
      aria-disabled={submitting || undefined}
      aria-busy={submitting || undefined}
      sx={{
        '&[aria-disabled="true"], &[aria-disabled="true"]:hover': {
          color: 'action.disabled',
          bgcolor: 'action.disabledBackground',
          boxShadow: 'none',
          cursor: 'default',
        },
      }}
      startIcon={
        submitting ? <CircularProgress size={20} color="inherit" aria-hidden /> : undefined
      }
    >
      {t(submitting ? 'participant.challenge.submitting' : 'participant.challenge.submit')}
    </Button>
  );
};

const TextChallengeForm: React.FC<ChallengeFormProps> = ({
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
  const [text, setText] = useState(
    typeof initialContent?.text === 'string' ? initialContent.text : '',
  );
  const { submitting, begin, end, report } = useAnswerSend();
  const [error, setError] = useState<string | null>(null);

  if (alreadySubmitted) {
    return (
      <Typography variant="body2" sx={{
        color: "text.secondary"
      }}>
        {t('participant.challenge.alreadySubmitted')}
      </Typography>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    if (!begin(e.currentTarget as HTMLFormElement)) return;
    setError(null);
    // What this member tried to send. A teammate's earlier answer refuses it,
    // and the view shows it instead of dropping it.
    let attempted: ChallengeFormProps['initialContent'];
    try {
      const content = { text };
      attempted = content;
      const result = submitAction
        ? await submitAction(content)
        : await createSubmission(orgSlug, runId, {
            run_challenge_id: challenge.id,
            content,
          });
      onSubmitted(result);
    } catch (err) {
      await report(err, attempted, onReload, setError);
      end();
    }
  };

  return (
    <Box component="form" onSubmit={handleSubmit}>
      <Stack spacing={2}>
        {error && <ErrorAlert message={error} />}
        <TextField
          label={t('participant.challenge.text.label')}
          multiline
          fullWidth
          minRows={4}
          value={text}
          onChange={(e) => setText(e.target.value.slice(0, MAX_CHARS))}
          placeholder={t('participant.challenge.text.placeholder')}
          helperText={t('participant.challenge.text.charCount', { count: text.length })}
          disabled={submitting}
        />
        <SubmitAnswerButton submitting={submitting} disabled={!text.trim()} />
      </Stack>
    </Box>
  );
};

export default TextChallengeForm;
