/**
 * Entry choice of the join flow: continue as a guest, create an account or
 * sign in. Routing depends on the wizard's `runCode`. The sign-in link
 * forwards `?join=<code>` and the post-login target travels in the navigation
 * state.
 */
import React, { useEffect } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

import OnboardingShell from '../components/OnboardingShell';
import ProgressIndicator from '../components/ProgressIndicator';
import heroProfile from '../assets/onboarding/hero-profile.svg';
import { pillButtonFocusRing } from '../styles/onboardingButtons';
import { useWizardState } from '../wizard/useWizardState';
import { getStepInfo } from '../wizard/wizardSteps';
import { getNextAfterAuth } from '../routes/ProfileSetupRedirect';

// 8 px between two 48 px bands, each wrapping a 40 px pill (4 + 40 + 4).
// Without the bands the stride between the pills shrinks from 56 px to 48 px.
const bandSx = { minHeight: '48px', display: 'flex', alignItems: 'center' } as const;

const pillSx = {
  all: 'unset',
  boxSizing: 'border-box',
  cursor: 'pointer',
  minHeight: '40px',
  paddingInline: '16px',
  borderRadius: '100px',
  fontFamily: "'Roboto', sans-serif",
  fontSize: '14px',
  fontWeight: 500,
  lineHeight: '20px',
  letterSpacing: '0.1px',
  whiteSpace: 'nowrap',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  ...pillButtonFocusRing,
} as const;

const filledSx = {
  backgroundColor: '#4D69B9',
  color: '#FFFFFF',
  '&:hover': { backgroundColor: '#3D54A0' },
} as const;

// An inset box-shadow draws the 1 px stroke inside the pill, so it stays
// 40 px. A CSS border would make it 42 px.
const outlineSx = {
  boxShadow: 'inset 0 0 0 1px var(--color-outline-variant, #CAC4D0)',
  color: 'text.secondary',
  '&:hover': { backgroundColor: 'rgba(73, 69, 79, 0.04)' },
} as const;

const EntryChoiceStep: React.FC = () => {
  const { t } = useTranslation('entryChoice');
  const navigate = useNavigate();
  const location = useLocation();
  const { code: routeCode } = useParams<{ code?: string }>();
  const wizard = useWizardState();
  const step = getStepInfo('entry-choice', wizard.pathKind);

  // A direct visit of /join/:code/auth-choice skips Welcome, so the wizard's
  // runCode is null. Take it from the route params, so Create and Login still
  // route into the join flow instead of /register.
  useEffect(() => {
    if (routeCode && wizard.pathKind !== 'with-code') {
      wizard.setPathKind('with-code');
    }
    if (routeCode && !wizard.runCode) {
      wizard.setRunCode(routeCode);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeCode]);

  const effectiveRunCode = routeCode ?? wizard.runCode;

  const goCreate = () => {
    if (!effectiveRunCode) {
      navigate({ pathname: '/register', search: location.search });
      return;
    }
    // `account=1` opens the profile step with the guest switch off: the
    // person chose an account one tap ago.
    const search = new URLSearchParams(location.search);
    search.set('account', '1');
    navigate({ pathname: `/join/${effectiveRunCode}/profile`, search: `?${search.toString()}` });
  };

  const goGuest = () => {
    const search = new URLSearchParams(location.search);
    search.delete('account');
    const qs = search.toString();
    navigate({ pathname: `/join/${effectiveRunCode}/profile`, search: qs ? `?${qs}` : '' });
  };

  const goLogin = () => {
    const postLogin = getNextAfterAuth(effectiveRunCode ?? undefined);
    const search = new URLSearchParams(location.search);
    if (effectiveRunCode) search.set('join', effectiveRunCode);
    const qs = search.toString();
    navigate(
      { pathname: '/login', search: qs ? `?${qs}` : '' },
      { state: { from: { pathname: postLogin } } },
    );
  };

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
          alignItems: 'stretch',
          paddingBlockStart: '57px',
          gap: '24px',
        }}
      >
        <Box
          component="img"
          src={heroProfile}
          alt=""
          sx={{
            width: '100px',
            height: '95px',
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
            letterSpacing: 0,
            color: '#322F35',
            margin: 0,
            marginBlockStart: '-2px',
          }}
        >
          {t('headline')}
        </Typography>
        <Typography
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 500,
            lineHeight: '24px',
            letterSpacing: '0.15px',
            color: '#605D64',
            marginBlockStart: '-3px',
          }}
        >
          {t('subtitle', { defaultValue: 'Select your preferred way to start:' })}
        </Typography>
        {/*
          Buttons column: 8 px between two 48 px bands. Each band centers a 40 px
          pill (4 px above, 4 px below). The band also moves the first pill down 4 px to
          the design's position. Without the bands the stride between the
          pills collapses from 56 px to 48 px and the outline button sits 8 px too
          high.
        */}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px', alignItems: 'flex-start' }}>
          {/* Guest first: with a join code in hand, the way without an account is the
              first choice. */}
          {effectiveRunCode && (
            <Box sx={bandSx}>
              <Box component="button" type="button" onClick={goGuest} sx={{ ...pillSx, ...filledSx, minWidth: '162px' }}>
                {t('continueAsGuest')}
              </Box>
            </Box>
          )}
          <Box sx={bandSx}>
            <Box
              component="button"
              type="button"
              onClick={goCreate}
              sx={{ ...pillSx, ...(effectiveRunCode ? outlineSx : filledSx), minWidth: '162px' }}
            >
              {t('createAccount')}
            </Box>
          </Box>
          <Box sx={bandSx}>
            <Box component="button" type="button" onClick={goLogin} sx={{ ...pillSx, ...outlineSx, minWidth: '140px' }}>
              {t('loginExisting')}
            </Box>
          </Box>
        </Box>
      </Box>
    </OnboardingShell>
  );
};

export default EntryChoiceStep;
