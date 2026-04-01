import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { localizedText } from '../../i18n/contentLocales';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Divider,
  FormControl,
  InputLabel,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Select,
  Snackbar,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import { deleteChallenge, duplicateChallenge, getChallenge, updateChallenge } from '../../api/content';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { Challenge, ChallengeType, MultilingualString, ScoringRules } from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import MultilingualField, { type MultilingualFieldHandle } from '../../components/MultilingualField';
import ScoringRulesEditor from '../../components/ScoringRulesEditor';
import BackLink from '../../components/BackLink';
import { CHALLENGE_TYPES } from '../../utils/challengeTypes';
import {
  discardedScoringKeys,
  pruneScoringRules,
  pruneScoringRulesForTypeChange,
} from '../../utils/scoringRules';
import { isRichTextEmpty, optionalRichText } from '../../utils/richtext';
import SolutionField from '../../components/SolutionField';

const ChallengeDetail: React.FC = () => {
  const { challengeId } = useParams<{ challengeId: string }>();
  const { user } = useAuth();
  const { t, i18n } = useTranslation('content');
  const navigate = useNavigate();
  // Opened from a rallye (its sharing chip or "Als Kopie bearbeiten"): the
  // way back leads to that rallye, as on the station page.
  const [searchParams] = useSearchParams();
  const rallyeId = searchParams.get('rallyeId');
  const [duplicating, setDuplicating] = useState(false);
  // Set by ChallengeCreate, so the page that follows says what happened.
  const [createdOpen, setCreatedOpen] = useState(
    (useLocation().state as { created?: boolean } | null)?.created === true,
  );

  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [challengeType, setChallengeType] = useState<ChallengeType>('photo');
  // The type the creator picked but has not confirmed yet. While it is set, the
  // confirmation is open and the Select still shows the old type, so cancelling
  // needs no restore step.
  const [pendingType, setPendingType] = useState<ChallengeType | null>(null);
  const [title, setTitle] = useState<MultilingualString>({});
  const [instructions, setInstructions] = useState<MultilingualString>({});
  const [scoringRules, setScoringRules] = useState<ScoringRules>({ max_points: 10 });
  const [duration, setDuration] = useState('');
  const [solution, setSolution] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Revealed together on an incomplete save click. The fields themselves
  // stay untouched until then.
  const [showErrors, setShowErrors] = useState(false);
  const titleRef = useRef<MultilingualFieldHandle>(null);
  const instructionsRef = useRef<MultilingualFieldHandle>(null);

  const orgSlug = user?.organization_slug;

  const handleDuplicate = async () => {
    if (!orgSlug || !challengeId) return;
    setDuplicating(true);
    try {
      const copy = await duplicateChallenge(orgSlug, challengeId);
      navigate(`/challenges/${copy.id}`);
    } catch {
      setError(t('library.duplicateFailed'));
    } finally {
      setDuplicating(false);
    }
  };
  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';
  const titleOf = (ml: MultilingualString) => localizedText(ml, activeLang);

  const load = useCallback(async () => {
    if (!orgSlug || !challengeId) return;
    setLoading(true);
    setError(null);
    try {
      const c = await getChallenge(orgSlug, challengeId);
      setChallenge(c);
      setChallengeType(c.challenge_type);
      setPendingType(null);
      setTitle(c.title);
      setInstructions(c.instructions);
      setScoringRules(c.scoring_rules);
      setDuration(c.time_estimate_minutes != null ? String(c.time_estimate_minutes) : '');
      setSolution(c.suggested_solution ?? '');
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, challengeId]);

  useEffect(() => {
    void load();
  }, [load]);

  const durationValue = duration ? parseInt(duration, 10) : null;
  const isDirty =
    !!challenge &&
    (challengeType !== challenge.challenge_type ||
      JSON.stringify(title) !== JSON.stringify(challenge.title) ||
      JSON.stringify(instructions) !== JSON.stringify(challenge.instructions) ||
      JSON.stringify(scoringRules) !== JSON.stringify(challenge.scoring_rules) ||
      durationValue !== challenge.time_estimate_minutes ||
      optionalRichText(solution) !== (challenge.suggested_solution ?? null));
  const deTitleMissing = !title.de?.trim();
  const deInstructionsMissing = isRichTextEmpty(instructions.de);

  const usageStations = (challenge?.usage?.station_titles ?? []).map((s) => titleOf(s)).filter(Boolean);
  const usageRallyes = (challenge?.usage?.rallye_titles ?? []).map((r) => titleOf(r)).filter(Boolean);
  const usageCount = challenge?.usage?.station_ids.length ?? 0;

  // Discarded per key that is actually configured, so the confirmation lists the
  // loss the creator would notice and nothing else.
  const discardedKeys = discardedScoringKeys(challengeType, pendingType, scoringRules);

  const handleTypeChange = (next: ChallengeType) => {
    if (next === challengeType) return;
    setPendingType(next);
  };

  const confirmTypeChange = () => {
    if (!pendingType) return;
    setScoringRules((rules) => pruneScoringRulesForTypeChange(challengeType, pendingType, rules));
    setChallengeType(pendingType);
    setPendingType(null);
  };

  const handleSave = async () => {
    if (!orgSlug || !challengeId || !challenge) return;
    if (deTitleMissing || deInstructionsMissing) {
      setShowErrors(true);
      (deTitleMissing ? titleRef : instructionsRef).current?.focus();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await updateChallenge(orgSlug, challengeId, {
        challenge_type: challengeType,
        title,
        instructions,
        // The keys were pruned when the type change was confirmed. Pruning again
        // against the type that is actually being saved keeps the payload honest
        // if the editor added a key in between.
        scoring_rules: pruneScoringRules(challengeType, scoringRules),
        time_estimate_minutes: durationValue,
        suggested_solution: optionalRichText(solution),
      });
      setChallenge(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!orgSlug || !challengeId) return;
    setDeleting(true);
    try {
      await deleteChallenge(orgSlug, challengeId);
      navigate('/challenges');
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setDeleting(false);
      setDeleteOpen(false);
    }
  };

  if (loading) return <LoadingSpinner />;
  if (!challenge) {
    return (
      <Box>
        <ErrorAlert message={error ?? 'Challenge not found'} onRetry={load} />
      </Box>
    );
  }

  return (
    <Box>
      <BackLink
        to={rallyeId ? `/rallyes/${rallyeId}` : '/challenges'}
        label={rallyeId ? t('station.detail.backToRallye') : t('challenge.detail.backToList')}
      />
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{ justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, mb: 3 }}
      >
        <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
          <Typography variant="h2" component="h1">
            {titleOf(challenge.title) || t('challenge.detail.title')}
          </Typography>
        </Stack>
        <Stack direction="row" spacing={1}>
        {/* A complete copy to adapt for another rallye. */}
        <Button
          startIcon={<ContentCopyIcon />}
          variant="outlined"
          disabled={duplicating}
          onClick={() => void handleDuplicate()}
        >
          {t('library.duplicate')}
        </Button>
        <Button
          startIcon={<DeleteIcon />}
          color="error"
          variant="outlined"
          onClick={() => setDeleteOpen(true)}
        >
          {t('challenge.detail.archive')}
        </Button>
        </Stack>
      </Stack>
      {error && <ErrorAlert message={error} />}
      <Card>
        <CardContent>
          <Typography variant="h5" component="h2" gutterBottom>
            {t('challenge.detail.editTitle')}
          </Typography>
          {usageCount > 0 && (
            <Alert severity="info" sx={{ mb: 2 }}>
              {t('challenge.detail.propagationNote')}
              {usageStations.length > 0 && ` ${t('challenge.detail.usedInStations', { stations: usageStations.join(', ') })}`}
              {usageRallyes.length > 0 && ` ${t('challenge.detail.usedInRallyes', { rallyes: usageRallyes.join(', ') })}`}
            </Alert>
          )}
          <Stack spacing={3}>
            <FormControl sx={{ maxWidth: 240 }}>
              <InputLabel id="challenge-type-label">{t('challenge.detail.typeLabel')}</InputLabel>
              <Select
                labelId="challenge-type-label"
                label={t('challenge.detail.typeLabel')}
                value={challengeType}
                onChange={(e) => handleTypeChange(e.target.value as ChallengeType)}
              >
                {CHALLENGE_TYPES.map((type) => (
                  <MenuItem key={type} value={type}>
                    {t(`challenge.types.${type}`)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
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
            <ScoringRulesEditor
              value={scoringRules}
              onChange={setScoringRules}
              challengeType={challengeType}
            />
            <SolutionField value={solution} onChange={setSolution} disabled={saving} />
            <Divider />
            <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button
                variant="contained"
                onClick={handleSave}
                disabled={!isDirty || saving}
              >
                {usageCount > 0
                  ? t('challenge.detail.saveAcrossAll')
                  : t('challenge.detail.save')}
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>
      <ConfirmDialog
        open={pendingType !== null}
        title={t('challenge.detail.typeChange.title')}
        message={t('challenge.detail.typeChange.intro', {
          from: t(`challenge.types.${challengeType}`),
          to: pendingType ? t(`challenge.types.${pendingType}`) : '',
        })}
        confirmLabel={t('challenge.detail.typeChange.confirm')}
        onConfirm={confirmTypeChange}
        onCancel={() => setPendingType(null)}
      >
        <Box sx={{ mt: 1 }}>
          <Typography variant="body2">
            {discardedKeys.length > 0
              ? t('challenge.detail.typeChange.discardedIntro')
              : t('challenge.detail.typeChange.nothingDiscarded')}
          </Typography>
          {discardedKeys.length > 0 && (
            <List dense disablePadding sx={{ listStyleType: 'disc', pl: 3 }}>
              {discardedKeys.map((key) => (
                <ListItem key={key} disableGutters sx={{ display: 'list-item', py: 0 }}>
                  <ListItemText
                    primary={t(`challenge.detail.typeChange.discarded.${key}`, {
                      defaultValue: key,
                    })}
                  />
                </ListItem>
              ))}
            </List>
          )}
          <Typography variant="body2" sx={{ mt: 1 }}>
            {t('challenge.detail.typeChange.runsNote')}
          </Typography>
        </Box>
      </ConfirmDialog>
      <ConfirmDialog
        open={deleteOpen}
        title={t('challenge.detail.archive')}
        message={
          usageStations.length > 0
            ? t('challenge.detail.archiveInUseConfirm', {
                count: usageStations.length,
                stations: usageStations.join(', '),
              })
            : t('challenge.detail.archiveConfirm')
        }
        destructive
        loading={deleting}
        confirmLabel={t('archive.archive')}
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />
      <Snackbar
        open={createdOpen}
        autoHideDuration={4000}
        onClose={(_, reason) => {
          if (reason !== 'clickaway') setCreatedOpen(false);
        }}
        message={t('challenge.create.success')}
      />
    </Box>
  );
};

export default ChallengeDetail;
