import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { localizedText } from '../../i18n/contentLocales';
import {
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  InputAdornment,
  Stack,
  Step,
  StepLabel,
  Stepper,
  TextField,
  Typography,
  useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import { listRallyes } from '../../api/content';
import { createRun } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { Rallye } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import { formatDateTime } from '../../utils/formatDate';
import { useAutoStartRuns } from '../../hooks/useAutoStartRuns';

const RunCreate: React.FC = () => {
  const { t, i18n } = useTranslation(['runs', 'common']);
  const { user } = useAuth();
  const navigate = useNavigate();

  const [activeStep, setActiveStep] = useState(0);

  // Step 1 state
  const [rallyes, setRallyes] = useState<Rallye[]>([]);
  const [rallyesLoading, setRallyesLoading] = useState(true);
  const [rallyesError, setRallyesError] = useState<string | null>(null);
  const [selectedRallyeId, setSelectedRallyeId] = useState<string | null>(null);

  // Step 2 state
  const [name, setName] = useState('');
  const [maxTeams, setMaxTeams] = useState('');
  const [scheduledFor, setScheduledFor] = useState('');
  const autoStart = useAutoStartRuns(user?.organization_slug);

  // Submit state
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';

  const rallyeTitle = (r: Rallye) => localizedText(r.title, activeLang) || r.id;

  const loadRallyes = useCallback(async () => {
    if (!user?.organization_slug) return;
    setRallyesLoading(true);
    setRallyesError(null);
    try {
      const data = await listRallyes(user.organization_slug);
      setRallyes(data.items.filter((r) => r.is_published));
    } catch (err) {
      setRallyesError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setRallyesLoading(false);
    }
  }, [user?.organization_slug]);

  useEffect(() => {
    void loadRallyes();
  }, [loadRallyes]);

  const selectedRallye = rallyes.find((r) => r.id === selectedRallyeId) ?? null;

  const canProceedStep1 = !!selectedRallyeId;
  const canProceedStep2 = !!name.trim();

  const handleNext = () => setActiveStep((s) => s + 1);
  const handleBack = () => setActiveStep((s) => s - 1);

  const handleSubmit = async () => {
    if (!user?.organization_slug || !selectedRallyeId || !name.trim()) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const body = {
        rallye_id: selectedRallyeId,
        name: name.trim(),
        max_teams: maxTeams ? parseInt(maxTeams, 10) : undefined,
        scheduled_for: scheduledFor ? new Date(scheduledFor).toISOString() : undefined,
      };
      const created = await createRun(user.organization_slug, body);
      navigate(`/runs/${created.id}`);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.detail : String(err));
      setSubmitting(false);
    }
  };

  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down('sm'));
  const steps = [t('create.step1'), t('create.step2'), t('create.step3')];

  return (
    <Box>
      <Typography variant="h2" component="h1" gutterBottom>
        {t('create.title')}
      </Typography>
      {/* Below sm the labels sit under the icons and wrap between words, so the
          three steps share the width. A single word never breaks: the label is the
          size of the overline token, smaller and the step gives it its whole width, so the longest word
          ("Zusammenfassung") fits at 320 px. */}
      <Stepper
        activeStep={activeStep}
        alternativeLabel={narrow}
        sx={{
          mb: 4,
          [theme.breakpoints.down('sm')]: {
            '& .MuiStep-root': { px: 0 },
            '& .MuiStepLabel-label': { fontSize: theme.typography.overline.fontSize },
          },
        }}
      >
        {steps.map((label) => (
          <Step key={label}>
            <StepLabel>{label}</StepLabel>
          </Step>
        ))}
      </Stepper>
      {/* Step 1: Select rallye */}
      {activeStep === 0 && (
        <Box>
          <Typography variant="h6" component="h2" gutterBottom>
            {t('create.selectRallye')}
          </Typography>
          {rallyesLoading && <LoadingSpinner />}
          {rallyesError && <ErrorAlert message={rallyesError} onRetry={loadRallyes} />}
          {!rallyesLoading && !rallyesError && (
            <Stack spacing={1}>
              {rallyes.map((r) => {
                const selected = selectedRallyeId === r.id;
                return (
                  <Card
                    key={r.id}
                    variant="outlined"
                    sx={{
                      borderColor: selected ? 'primary.main' : 'divider',
                      borderWidth: 2,
                      bgcolor: selected ? 'action.selected' : 'background.paper',
                      transition: (theme) =>
                        theme.transitions.create(['border-color', 'background-color']),
                    }}
                  >
                    <CardActionArea
                      onClick={() => setSelectedRallyeId(r.id)}
                      aria-pressed={selected}
                    >
                      <CardContent
                        sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}
                      >
                        {selected ? (
                          <CheckCircleIcon color="primary" />
                        ) : (
                          <RadioButtonUncheckedIcon sx={{ color: 'action.disabled' }} />
                        )}
                        <Typography
                          variant="subtitle1"
                          component="h3"
                          sx={{ fontWeight: selected ? 700 : 400 }}
                        >
                          {rallyeTitle(r)}
                        </Typography>
                      </CardContent>
                    </CardActionArea>
                  </Card>
                );
              })}
              {rallyes.length === 0 && (
                <Typography sx={{
                  color: "text.secondary"
                }}>{t('create.noPublishedRallyes')}</Typography>
              )}
            </Stack>
          )}
          <Stack
            direction="row"
            sx={{
              justifyContent: "flex-end",
              mt: 3
            }}>
            <Button
              variant="contained"
              onClick={handleNext}
              disabled={!canProceedStep1}
            >
              {t('common:actions.continue')}
            </Button>
          </Stack>
        </Box>
      )}
      {/* Step 2: Configure */}
      {activeStep === 1 && (
        <Box>
          <Stack spacing={3}>
            <TextField
              label={t('create.nameLabel')}
              helperText={t('create.nameHelper')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              fullWidth
            />
            <TextField
              label={t('create.maxTeams')}
              value={maxTeams}
              onChange={(e) => setMaxTeams(e.target.value)}
              type="number"
              fullWidth
              slotProps={{
                htmlInput: { min: 1 },
                input: {
                  startAdornment: <InputAdornment position="start">#</InputAdornment>,
                },
              }}
            />
            <TextField
              label={t('create.scheduledStart')}
              // The text follows the organization's switch.
              helperText={t(
                autoStart === false
                  ? 'create.scheduledStartHelperManual'
                  : 'create.scheduledStartHelperAuto',
              )}
              value={scheduledFor}
              onChange={(e) => setScheduledFor(e.target.value)}
              type="datetime-local"
              fullWidth
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Stack>
          <Stack
            direction="row"
            sx={{
              justifyContent: "space-between",
              mt: 3
            }}>
            <Button variant="outlined" onClick={handleBack}>
              {t('common:actions.back')}
            </Button>
            <Button variant="contained" onClick={handleNext} disabled={!canProceedStep2}>
              {t('common:actions.continue')}
            </Button>
          </Stack>
        </Box>
      )}
      {/* Step 3: Review */}
      {activeStep === 2 && (
        <Box>
          <Typography variant="h6" component="h2" gutterBottom>
            {t('create.review')}
          </Typography>

          {submitError && <ErrorAlert message={submitError} />}

          {/* Each row has a label of its own: the step names and the input labels
              are prompts ("Rallye auswählen"), not names for a value. */}
          <Stack spacing={1} sx={{ mt: 2 }} data-testid="summary-rows">
            <Typography>
              <strong>{t('create.summaryRallye')}:</strong>{' '}
              {selectedRallye ? rallyeTitle(selectedRallye) : '—'}
            </Typography>
            <Typography>
              <strong>{t('create.summaryName')}:</strong> {name}
            </Typography>
            {maxTeams && (
              <Typography>
                <strong>{t('create.summaryMaxTeams')}:</strong> {maxTeams}
              </Typography>
            )}
            {scheduledFor && (
              <Typography>
                <strong>{t('create.summaryScheduledStart')}:</strong>{' '}
                {formatDateTime(new Date(scheduledFor).toISOString(), i18n.language)}
              </Typography>
            )}
          </Stack>

          {/* What the click on "Lauf erstellen" does and when teams can join.
              The run page shows code and QR code before the start, and the
              server lets teams in from the start on. A planned run starts on
              its own unless the organization switched that off. */}
          <Typography sx={{ mt: 3 }} data-testid="after-create">
            {t(
              scheduledFor && autoStart !== false ? 'create.afterCreatePlanned' : 'create.afterCreate',
            )}
          </Typography>

          <Stack
            direction="row"
            sx={{
              justifyContent: "space-between",
              mt: 3
            }}>
            <Button variant="outlined" onClick={handleBack} disabled={submitting}>
              {t('common:actions.back')}
            </Button>
            <Button
              variant="contained"
              onClick={() => void handleSubmit()}
              disabled={submitting}
            >
              {t('create.submit')}
            </Button>
          </Stack>
        </Box>
      )}
    </Box>
  );
};

export default RunCreate;
