/**
 * Team selection step: create a team or join one. Handles the deep-link
 * preselect via ?team=, the redirect from useResolveJoinDestination, path-kind
 * normalization and the branch Create to Invite, Join to Join.
 */
import { Box, Typography } from '@mui/material';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router';

import OnboardingShell from '../../components/OnboardingShell';
import ProgressIndicator from '../../components/ProgressIndicator';
import ClavisCircularProgress from '../../components/ClavisCircularProgress';
import heroBalloon from '../../assets/onboarding/hero-team-balloon.svg';
import addTeamIcon from '../../assets/onboarding/add_team.svg';
import joinTeamIcon from '../../assets/onboarding/join_team.svg';
import checkCircleIcon from '../../assets/onboarding/check_circle.svg';
import {
  teamPendingPath,
  useResolveJoinDestination,
} from '../../routes/useResolveJoinDestination';
import { useAdoptResolvedTeam } from '../../wizard/WizardStateContext';
import { useWizardState } from '../../wizard/useWizardState';
import { getStepInfo } from '../../wizard/wizardSteps';
import { pillButtonFocusRing } from '../../styles/onboardingButtons';
import { useDelayedFlag } from '../../hooks/useDelayedFlag';

const FindTeamStep: React.FC = () => {
  const { t } = useTranslation('onboarding');
  const { t: tc } = useTranslation('common');
  const navigate = useNavigate();
  const { code } = useParams<{ code: string }>();
  const [searchParams] = useSearchParams();
  const wizard = useWizardState();
  const tf = wizard.teamFlow;

  const destination = useResolveJoinDestination(code);
  useAdoptResolvedTeam(destination);
  const showLoadingIndicator = useDelayedFlag(destination.kind === 'loading');

  useEffect(() => {
    const presetTeamCode = searchParams.get('team');
    if (presetTeamCode && tf.mode === null) {
      wizard.patchTeamFlow({
        mode: 'join',
        enteredCode: presetTeamCode.toUpperCase(),
      });
    }
    if (wizard.pathKind !== 'with-code') wizard.setPathKind('with-code');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const step = getStepInfo('team-find', 'with-code');

  if (destination.kind === 'team-ready') {
    return <Navigate to={`/runs/${destination.runId}/participant/stations`} replace />;
  }
  // A member of a still-unnamed team may not see the
  // "create or join" choice: creating would give them a second team, joining
  // would offer the team they are already in. Send them to their own step,
  // and render nothing while my-team is still answering, so the choice does
  // not flash up before the redirect.
  if (code && destination.kind === 'team-pending') {
    return <Navigate to={teamPendingPath(code, destination.isCreator)} replace />;
  }
  if (destination.kind === 'loading') {
    return showLoadingIndicator ? (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          minHeight: '100vh',
        }}
      >
        {/* The spinner says what loads, so a screen
            reader hears more than a bare "progress bar". */}
        <ClavisCircularProgress
          indeterminate
          ariaLabel={tc('aria.loadingTeamDestination')}
        />
      </Box>
    ) : null;
  }

  const onContinue = () => {
    if (tf.mode === 'create') {
      navigate(`/join/${code}/team/invite`);
    } else if (tf.mode === 'join') {
      navigate(`/join/${code}/team/join`);
    }
  };

  const renderChip = (
    mode: 'create' | 'join',
    label: string,
    iconSrc: string,
  ) => {
    const selected = tf.mode === mode;
    return (
      <Box
        component="button"
        type="button"
        onClick={() => wizard.patchTeamFlow({ mode })}
        aria-pressed={selected}
        sx={{
          all: 'unset',
          boxSizing: 'border-box',
          cursor: 'pointer',
          alignSelf: 'flex-start',
          minHeight: '32px',
          paddingBlock: '6px',
          paddingLeft: '8px',
          paddingRight: '12px',
          borderRadius: '8px',
          border: 'none',
          boxShadow: selected ? 'none' : 'inset 0 0 0 1px #CAC5CD',
          backgroundColor: selected ? '#D3E9D5' : '#FFFFFF',
          fontFamily: "'Roboto', sans-serif",
          fontSize: '14px',
          fontWeight: 500,
          lineHeight: '20px',
          letterSpacing: '0.1px',
          color: selected ? '#1D1B20' : '#322F35',
          display: 'inline-flex',
          alignItems: 'center',
          gap: '8px',
          transition: 'border-color 120ms ease, background-color 120ms ease, color 120ms ease',
          ...pillButtonFocusRing,
        }}
      >
        <Box
          component="img"
          src={selected ? checkCircleIcon : iconSrc}
          alt=""
          sx={{
            width: '18px',
            height: '18px',
            display: 'block',
            flexShrink: 0,
          }}
        />
        {label}
      </Box>
    );
  };

  const continueDisabled = tf.mode === null;

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
          paddingBlockStart: '52px',
          gap: '24px',
        }}
      >
        <Box
          component="img"
          src={heroBalloon}
          alt=""
          sx={{
            width: '107px',
            height: '98px',
            alignSelf: 'center',
            display: 'block',
            objectFit: 'contain',
          }}
        />
        <Typography
          component="h1"
          sx={{
            fontFamily: "'Roboto', sans-serif",
            fontSize: '24px',
            fontWeight: 600,
            lineHeight: '32px',
            color: '#322F35',
            margin: 0,
          }}
        >
          {t('find.title')}
        </Typography>
        <Typography
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 400,
            lineHeight: '24px',
            letterSpacing: '0.5px',
            color: '#322F35',
            marginBlockStart: '-3px',
          }}
        >
          {t('find.subtitle')}
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: '16px', alignItems: 'flex-start', paddingBlockEnd: '4px' }}>
          {renderChip('create', t('find.createCard.title'), addTeamIcon)}
          {renderChip('join', t('find.joinCard.title'), joinTeamIcon)}
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <Box sx={{ display: 'flex', flexDirection: 'row', gap: '16px' }}>
            <Box
              component="button"
              type="button"
              onClick={() => navigate(-1)}
              sx={{
                all: 'unset',
                boxSizing: 'border-box',
                cursor: 'pointer',
                minHeight: '40px',
                paddingInline: '16px',
                borderRadius: '100px',
                border: '1px solid #CAC4D0',
                color: '#49454F',
                fontFamily: "'Roboto', sans-serif",
                fontSize: '14px',
                fontWeight: 500,
                lineHeight: '20px',
                letterSpacing: '0.1px',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                '&:hover': { backgroundColor: 'rgba(73, 69, 79, 0.04)' },
                ...pillButtonFocusRing,
              }}
            >
              {t('find.back')}
            </Box>
            <Box
              component="button"
              type="button"
              onClick={onContinue}
              disabled={continueDisabled}
              sx={{
                all: 'unset',
                boxSizing: 'border-box',
                cursor: continueDisabled ? 'not-allowed' : 'pointer',
                minHeight: '40px',
                paddingInline: '16px',
                borderRadius: '100px',
                backgroundColor: continueDisabled ? 'rgba(29, 27, 32, 0.10)' : '#4D69B9',
                color: continueDisabled ? '#605D64' : '#FFFFFF',
                fontFamily: "'Roboto', sans-serif",
                fontSize: '14px',
                fontWeight: 500,
                lineHeight: '20px',
                letterSpacing: '0.1px',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                '&:hover': {
                  backgroundColor: continueDisabled ? 'rgba(29, 27, 32, 0.10)' : '#3D54A0',
                },
                ...pillButtonFocusRing,
              }}
            >
              {t('find.continue')}
            </Box>
          </Box>
          {tf.mode === null && (
            <Typography
              sx={{
                fontFamily: "'Noto Sans', sans-serif",
                fontSize: '12px',
                fontWeight: 500,
                lineHeight: '16px',
                letterSpacing: '0.5px',
                color: '#000000',
              }}
            >
              {t('find.selectPrompt')}
            </Typography>
          )}
        </Box>
      </Box>
    </OnboardingShell>
  );
};

export default FindTeamStep;
