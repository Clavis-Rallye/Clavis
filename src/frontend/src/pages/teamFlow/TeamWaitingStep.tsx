/**
 * Wait screen of a joiner (step 5/6) while the team is not named yet. Listens on
 * the run's websocket: `team.stage_changed` to naming moves on to the pending
 * step, `team.name_updated` moves on to the run view.
 */
import { Box, Button, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

import ClavisCircularProgress from '../../components/ClavisCircularProgress';
import OnboardingShell from '../../components/OnboardingShell';
import ProgressIndicator from '../../components/ProgressIndicator';
import heroWaiting from '../../assets/onboarding/hero-team-waiting.svg';
import { useWebSocket } from '../../hooks/useWebSocket';
import { useWizardState } from '../../wizard/useWizardState';
import { getStepInfo } from '../../wizard/wizardSteps';

const TeamWaitingStep: React.FC = () => {
  const { t } = useTranslation('onboarding');
  const navigate = useNavigate();
  const { code: rallyeCode } = useParams<{ code: string }>();
  const wizard = useWizardState();
  const tf = wizard.teamFlow;

  const step = getStepInfo('team-waiting', 'with-code');
  const canSubscribe = !!wizard.organizationSlug && !!tf.runId;

  useWebSocket({
    path:
      canSubscribe
        ? `/api/orgs/${wizard.organizationSlug}/runs/${tf.runId}/ws`
        : '',
    enabled: canSubscribe,
    handlers: {
      'team.stage_changed': (data) => {
        const payload = data as { team_id?: string; stage?: string } | undefined;
        if (!payload || payload.team_id !== tf.teamId) return;
        if (payload.stage === 'naming') {
          navigate(`/join/${rallyeCode}/team/pending`);
        }
      },
      'team.name_updated': (data) => {
        const payload = data as { team_id?: string; name?: string | null } | undefined;
        if (!payload || payload.team_id !== tf.teamId) return;
        if (payload.name && tf.runId) {
          if (tf.teamId) {
            try {
              sessionStorage.setItem(`clavis_team_for_run_${tf.runId}`, tf.teamId);
            } catch { /* sessionStorage disabled */ }
          }
          // `replace`: the wizard ends here, so a back from
          // the run view must not land on the waiting screen again.
          navigate(`/runs/${tf.runId}/participant/stations`, { replace: true });
        }
      },
    },
  });

  return (
    <OnboardingShell
      progress={
        <ProgressIndicator
          current={step.current}
          total={step.total}
        />
      }
    >
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          paddingBlockStart: '52px',
          gap: '24px',
        }}
      >
        <Box
          component="img"
          src={heroWaiting}
          alt=""
          sx={{
            width: '107px',
            height: '98px',
            display: 'block',
          }}
        />
        <Typography
          component="h1"
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '28px',
            fontWeight: 600,
            lineHeight: '36px',
            color: '#322F35',
            textAlign: 'center',
            margin: 0,
            whiteSpace: 'pre-line',
          }}
        >
          {t('waiting.title')}
        </Typography>
        <Box
          role="status"
          aria-live="polite"
          aria-label={t('waiting.subtitle')}
          sx={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '16px',
          }}
        >
          <ClavisCircularProgress indeterminate ariaLabel={t('waiting.subtitle')} />
          <Typography
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '16px',
              fontWeight: 400,
              lineHeight: '24px',
              letterSpacing: '0.5px',
              color: '#322F35',
              textAlign: 'center',
              // The body/large line box has 3 px of leading above the glyph. The
              // design's text box has none. Pull the element up so the glyph lands at
              // the design's position.
              marginBlockStart: '-3px',
            }}
          >
            {t('waiting.subtitle')}
          </Typography>
        </Box>
        <Button
          variant="outlined"
          onClick={() => navigate(`/join/${rallyeCode}/team/find`)}
          sx={{
            // Cascade compensation: the -3 px half-leading of the body shrank the inner box
            // by 3 px and shifted this button up. +3 px restores the design's position.
            marginBlockStart: '3px',
            minWidth: 0,
            // The visible pill of the outline button is 40 px tall. The project's
            // theme.ts MuiButton.styleOverrides.root sets minHeight: 48,
            // which silently wins over sx.height because CSS computes
            // effective-height = max(height, min-height). Explicit minHeight
            // here defeats that.
            minHeight: '40px',
            height: '40px',
            paddingInline: '16px',
            borderRadius: '100px',
            borderColor: '#CAC4D0',
            color: '#49454F',
            fontFamily: "'Roboto', sans-serif",
            fontSize: '14px',
            fontWeight: 500,
            lineHeight: '20px',
            letterSpacing: '0.1px',
            textTransform: 'none',
            '&:hover': {
              borderColor: '#49454F',
              backgroundColor: 'rgba(73, 69, 79, 0.04)',
            },
          }}
        >
          {t('waiting.back')}
        </Button>
      </Box>
    </OnboardingShell>
  );
};

export default TeamWaitingStep;
