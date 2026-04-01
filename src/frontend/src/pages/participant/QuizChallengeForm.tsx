import React, { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  FormControl,
  FormControlLabel,
  FormLabel,
  Radio,
  RadioGroup,
  Stack,
  Typography,
} from '@mui/material';
import { createSubmission } from '../../api/runs';
import ErrorAlert from '../../components/ErrorAlert';
import type { ChallengeFormProps } from './TextChallengeForm';
import { SubmitAnswerButton, useAnswerSend } from './TextChallengeForm';

const QuizChallengeForm: React.FC<ChallengeFormProps> = ({
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
  const legendId = useId();
  const [selected, setSelected] = useState<string | null>(
    typeof initialContent?.selected_option === 'string' ? initialContent.selected_option : null,
  );
  const { submitting, begin, end, report } = useAnswerSend();
  const [error, setError] = useState<string | null>(null);

  const options = (challenge.scoring_rules.options as string[] | undefined) ?? [];

  if (alreadySubmitted) {
    return (
      <Typography variant="body2" sx={{
        color: "text.secondary"
      }}>
        {t('participant.challenge.alreadySubmitted')}
      </Typography>
    );
  }

  if (options.length === 0) {
    return <ErrorAlert message={t('participant.challenge.quiz.noOptions')} />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selected) return;
    if (!begin(e.currentTarget as HTMLFormElement)) return;
    setError(null);
    // What this member tried to send. A teammate's earlier answer refuses it,
    // and the view shows it instead of dropping it.
    let attempted: ChallengeFormProps['initialContent'];
    try {
      const content = { selected_option: selected };
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
        <FormControl component="fieldset" disabled={submitting}>
          {/* The authored instructions sit above the form in ChallengeView. The
              option group names what the control itself collects, so the legend
              reads "Deine Antwort" and not "Anleitung" a second time. */}
          <FormLabel component="legend" id={legendId} sx={{ mb: 1 }}>
            {t('participant.challenge.yourAnswer')}
          </FormLabel>
          {/* The fieldset has its legend. The radiogroup inside it needs a name
              of its own for a screen reader. */}
          <RadioGroup
            aria-labelledby={legendId}
            value={selected ?? ''}
            onChange={(e) => setSelected(e.target.value)}
          >
            {options.map((opt, idx) => (
              <FormControlLabel
                key={`${idx}-${opt}`}
                value={opt}
                control={<Radio />}
                label={opt}
              />
            ))}
          </RadioGroup>
        </FormControl>
        {/* The button has an explicit primary tone. Its enabled state must read
            as the one call to action of the page, never as a lighter secondary
            button. */}
        <SubmitAnswerButton submitting={submitting} disabled={!selected} />
      </Stack>
    </Box>
  );
};

export default QuizChallengeForm;
