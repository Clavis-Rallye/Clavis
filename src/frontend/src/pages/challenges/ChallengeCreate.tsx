import React, { useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { createChallenge, createChallengeOnRallyeStation } from '../../api/content';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { ChallengeType, MultilingualString, ScoringRules } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import MultilingualField, { type MultilingualFieldHandle } from '../../components/MultilingualField';
import ScoringRulesEditor from '../../components/ScoringRulesEditor';
import BackLink from '../../components/BackLink';
import { pruneScoringRules } from '../../utils/scoringRules';
import { CHALLENGE_TYPES } from '../../utils/challengeTypes';
import { isRichTextEmpty, optionalRichText } from '../../utils/richtext';
import SolutionField from '../../components/SolutionField';

const ChallengeCreate: React.FC = () => {
  const { user } = useAuth();
  const { t } = useTranslation('content');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // When launched from the rallye composition flow, the new challenge is
  // created AND matched to the station within the rallye in one call
  // (createChallengeOnRallyeStation).
  const rallyeId = searchParams.get('rallyeId');
  const rallyeStationId = searchParams.get('rallyeStationId');
  const composing = !!rallyeId && !!rallyeStationId;

  const [challengeType, setChallengeType] = useState<ChallengeType | null>(null);
  const [title, setTitle] = useState<MultilingualString>({});
  const [instructions, setInstructions] = useState<MultilingualString>({});
  const [scoringRules, setScoringRules] = useState<ScoringRules>({ max_points: 10 });
  const [duration, setDuration] = useState('');
  const [solution, setSolution] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Revealed together on an incomplete create click. The fields themselves
  // stay untouched until then.
  const [showErrors, setShowErrors] = useState(false);
  const titleRef = useRef<MultilingualFieldHandle>(null);
  const instructionsRef = useRef<MultilingualFieldHandle>(null);

  const orgSlug = user?.organization_slug;
  const titleMissing = !title.de?.trim();
  const instructionsMissing = isRichTextEmpty(instructions.de);
  const canSubmit =
    !!orgSlug &&
    challengeType !== null &&
    (scoringRules.max_points ?? 0) >= 1 &&
    !submitting;

  const backTo = composing ? `/rallyes/${rallyeId}` : '/challenges';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!orgSlug || !challengeType) return;
    if (titleMissing || instructionsMissing) {
      setShowErrors(true);
      (titleMissing ? titleRef : instructionsRef).current?.focus();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        challenge_type: challengeType,
        title,
        instructions,
        scoring_rules: scoringRules,
        time_estimate_minutes: duration ? parseInt(duration, 10) : null,
        suggested_solution: optionalRichText(solution),
      };
      if (composing && rallyeId && rallyeStationId) {
        await createChallengeOnRallyeStation(orgSlug, rallyeId, rallyeStationId, body);
        // rallyeStationId is the rallye-station link_id. RallyeDetail reads
        // ?open= to keep the station expanded after the round trip.
        navigate(`/rallyes/${rallyeId}?open=${rallyeStationId}`, {
          state: { challengeCreated: true },
        });
      } else {
        const challenge = await createChallenge(orgSlug, body);
        navigate(`/challenges/${challenge.id}`, { state: { created: true } });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setSubmitting(false);
    }
  };

  return (
    <Box component="form" onSubmit={handleSubmit} noValidate>
      {/* The label names where the link goes. */}
      <BackLink
        to={backTo}
        label={composing ? t('station.detail.backToRallye') : t('challenge.detail.backToList')}
      />
      <Typography variant="h2" component="h1" gutterBottom>
        {t('challenge.create.title')}
      </Typography>
      {error && <ErrorAlert message={error} />}
      <Stack spacing={3} sx={{ mt: 3 }}>
        <Box>
          <Typography variant="subtitle1" component="h2" gutterBottom>
            {t('challenge.create.selectType')}
          </Typography>
          <ToggleButtonGroup
            value={challengeType}
            exclusive
            onChange={(_, next: ChallengeType | null) => {
              setChallengeType(next);
              // Switching type silently drops the previous type's scoring keys.
              // Without this, configuring a quiz and then saving as text
              // carries auto_approve along, where it does nothing — the form
              // claims the task scores itself and a coordinator waits forever.
              setScoringRules((rules) => pruneScoringRules(next, rules));
            }}
            aria-label={t('challenge.create.selectType')}
            // The row of task kinds is wider than a phone: it scrolls inside itself and never
            // widens the page. The buttons keep their size, a focused one scrolls into view.
            sx={{ maxWidth: '100%', overflowX: 'auto', '& .MuiToggleButton-root': { flexShrink: 0 } }}
          >
            {CHALLENGE_TYPES.map((type) => (
              <ToggleButton key={type} value={type}>
                {t(`challenge.types.${type}`)}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>

        <MultilingualField
          ref={titleRef}
          value={title}
          onChange={setTitle}
          label={t('challenge.create.titleLabel')}
          required
          showErrors={showErrors}
        />

        <MultilingualField
          ref={instructionsRef}
          value={instructions}
          onChange={setInstructions}
          label={t('challenge.create.instructionsLabel')}
          required
          richText
          showErrors={showErrors}
        />

        <TextField
          label={t('challenge.create.durationLabel')}
          value={duration}
          onChange={(e) => setDuration(e.target.value)}
          type="number"
          slotProps={{ htmlInput: { min: 0 } }}
          sx={{ maxWidth: 240 }}
        />

        {challengeType !== null && (
          <ScoringRulesEditor
            value={scoringRules}
            onChange={setScoringRules}
            challengeType={challengeType}
          />
        )}

        <SolutionField value={solution} onChange={setSolution} disabled={submitting} />

        <Stack direction="row" spacing={2} sx={{ justifyContent: 'flex-end' }}>
          <Button variant="outlined" onClick={() => navigate(backTo)} disabled={submitting}>
            {t('actions.cancel', { ns: 'common' })}
          </Button>
          <Button type="submit" variant="contained" disabled={!canSubmit}>
            {t('challenge.create.submit')}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
};

export default ChallengeCreate;
