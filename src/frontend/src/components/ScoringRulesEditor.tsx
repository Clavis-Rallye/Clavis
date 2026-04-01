import React from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  CardHeader,
  Checkbox,
  FormControlLabel,
  IconButton,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import DeleteIcon from '@mui/icons-material/Delete';
import { useTranslation } from 'react-i18next';
import type { ChallengeType, ScoringRules } from '../api/types';

interface ScoringRulesEditorProps {
  value: ScoringRules;
  onChange: (next: ScoringRules) => void;
  challengeType: ChallengeType;
}

const ScoringRulesEditor: React.FC<ScoringRulesEditorProps> = ({
  value,
  onChange,
  challengeType,
}) => {
  const { t } = useTranslation('content');
  const isQuiz = challengeType === 'quiz';
  const isQrScan = challengeType === 'qr_scan';
  const autoApprove = value.auto_approve ?? false;
  const options = value.options ?? [];

  const updateOption = (idx: number, next: string) => {
    const nextOptions = [...options];
    nextOptions[idx] = next;
    const previous = options[idx];
    const nextRules: ScoringRules = { ...value, options: nextOptions };
    if (value.correct_option === previous) {
      nextRules.correct_option = next;
    }
    onChange(nextRules);
  };

  const addOption = () => {
    onChange({ ...value, options: [...options, ''] });
  };

  const removeOption = (idx: number) => {
    const removed = options[idx];
    const nextOptions = options.filter((_, i) => i !== idx);
    const nextRules: ScoringRules = { ...value, options: nextOptions };
    if (value.correct_option === removed) {
      delete nextRules.correct_option;
    }
    onChange(nextRules);
  };

  return (
    <Card variant="outlined">
      <CardHeader title={t('challenge.create.scoringTitle')} />
      <CardContent>
        <Stack spacing={2}>
          <TextField
            label={t('challenge.create.maxPoints')}
            type="number"
            required
            slotProps={{ htmlInput: { min: 1 } }}
            value={value.max_points ?? ''}
            onChange={(e) => {
              const raw = e.target.value;
              onChange({ ...value, max_points: raw === '' ? undefined : Number(raw) });
            }}
            error={value.max_points !== undefined && value.max_points < 1}
            fullWidth
          />

          {isQuiz && (
            <Box>
              <Typography variant="subtitle2" component="p" gutterBottom>
                {t('challenge.create.quizOptions')}
              </Typography>
              <Stack spacing={1}>
                {options.map((opt, idx) => (
                  <Stack key={idx} direction="row" spacing={1} sx={{
                    alignItems: "center"
                  }}>
                    <TextField
                      label={t('challenge.create.quizOptionLabel', { index: idx + 1 })}
                      value={opt}
                      onChange={(e) => updateOption(idx, e.target.value)}
                      fullWidth
                      size="small"
                    />
                    <IconButton
                      // One name per button: the visible wording plus the option's number.
                      aria-label={`${t('challenge.create.quizOptionRemove')}: ${t(
                        'challenge.create.quizOptionLabel',
                        { index: idx + 1 },
                      )}`}
                      onClick={() => removeOption(idx)}
                      size="small"
                    >
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </Stack>
                ))}
                <Button
                  startIcon={<AddIcon />}
                  onClick={addOption}
                  size="small"
                  variant="outlined"
                  sx={{ alignSelf: 'flex-start' }}
                >
                  {t('challenge.create.quizOptionAdd')}
                </Button>
              </Stack>
            </Box>
          )}

          {isQuiz && (
            <FormControlLabel
              control={
                <Checkbox
                  checked={autoApprove}
                  onChange={(e) => {
                    const next: ScoringRules = { ...value, auto_approve: e.target.checked };
                    if (!e.target.checked) delete next.correct_option;
                    onChange(next);
                  }}
                />
              }
              label={t('challenge.create.autoApprove')}
            />
          )}

          {isQuiz && autoApprove && (
            <TextField
              select
              label={t('challenge.create.correctOption')}
              value={value.correct_option ?? ''}
              onChange={(e) =>
                onChange({
                  ...value,
                  correct_option: e.target.value === '' ? undefined : e.target.value,
                })
              }
              disabled={options.filter((o) => o.trim()).length === 0}
              helperText={
                options.filter((o) => o.trim()).length === 0
                  ? t('challenge.create.correctOptionEmptyHint')
                  : undefined
              }
              fullWidth
            >
              {options.map((opt, idx) =>
                opt.trim() ? (
                  <MenuItem key={idx} value={opt}>
                    {idx + 1}. {opt}
                  </MenuItem>
                ) : null,
              )}
            </TextField>
          )}

          {isQrScan && (
            <>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={autoApprove}
                    onChange={(e) => {
                      const next: ScoringRules = { ...value, auto_approve: e.target.checked };
                      if (!e.target.checked) delete next.expected_value;
                      onChange(next);
                    }}
                  />
                }
                label={t('challenge.create.autoApprove')}
              />
              {autoApprove && (
                <TextField
                  label={t('challenge.create.expectedValue')}
                  value={value.expected_value ?? ''}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      expected_value: e.target.value === '' ? undefined : e.target.value,
                    })
                  }
                  helperText={t('challenge.create.expectedValueHint')}
                  fullWidth
                />
              )}
            </>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
};

export default ScoringRulesEditor;
