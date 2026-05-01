/**
 * Welcome step: language selection and entry into a join flow. It looks up the
 * run by its code, seeds the wizard state, applies a language change before
 * navigating and branches on the navigation target.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  useNavigate,
  useParams,
  useSearchParams,
  useLocation,
} from 'react-router';
import { useTranslation, Trans } from 'react-i18next';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Skeleton from '@mui/material/Skeleton';
import Typography from '@mui/material/Typography';

import OnboardingShell from '../components/OnboardingShell';
import ProgressIndicator from '../components/ProgressIndicator';
import LanguageDropdown from '../components/LanguageDropdown';
import heroWelcome from '../assets/onboarding/hero-language.svg';
import { pillButtonFocusRing } from '../styles/onboardingButtons';
import {
  resolveDefaultLanguage,
  type SupportedLanguage,
} from '../i18n/resolveDefaultLanguage';
import { fetchByCode, type PublicRun } from '../services/publicRuns';
import { useWizardState } from '../wizard/useWizardState';
import { formatDateTime } from '../utils/formatDate';
import { pollWhileVisible } from '../utils/pollWhileVisible';
import { getStepInfo } from '../wizard/wizardSteps';

const HELPER_TEXT_ID = 'welcome-language-helper';

type RallyeFetchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'loaded'; run: PublicRun }
  | { status: 'org_archived' }
  | { status: 'error' };

const WelcomeStep: React.FC = () => {
  const { t, i18n } = useTranslation('welcome');
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const { code: runCode } = useParams<{ code?: string }>();
  const wizard = useWizardState();

  const initialLang = useMemo<SupportedLanguage>(
    () => resolveDefaultLanguage(searchParams.get('lang')),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const [lang, setLang] = useState<SupportedLanguage>(initialLang);

  const [rallyeState, setRallyeState] = useState<RallyeFetchState>({
    status: runCode ? 'loading' : 'idle',
  });

  useEffect(() => {
    if (!runCode) {
      setRallyeState({ status: 'idle' });
      return;
    }
    // Set the routing-relevant wizard state up front — independent of the
    // public-run lookup, which only supplies the display name. Without this,
    // a fast click through Welcome reaches EntryChoice/Profile before the
    // fetch resolves and the wizard has no runCode/pathKind to route on.
    wizard.setRunCode(runCode);
    wizard.setPathKind('with-code');
    let cancelled = false;
    setRallyeState({ status: 'loading' });
    // The lookup answers 404 ORG_ARCHIVED for a run of an archived
    // organization; this page then says so and offers no way on.
    let archived = false;
    fetchByCode(runCode, undefined, {
      onOrgArchived: () => {
        archived = true;
      },
    }).then((run) => {
      if (cancelled) return;
      if (run) {
        setRallyeState({ status: 'loaded', run });
        if (
          wizard.teamFlow.teamId !== null &&
          wizard.teamFlow.runId !== null &&
          wizard.teamFlow.runId !== run.run_id
        ) {
          wizard.resetTeamFlow();
        }
        if (run.organization_slug) {
          wizard.setOrganizationSlug(run.organization_slug);
        }
      } else setRallyeState({ status: archived ? 'org_archived' : 'error' });
    });
    return () => {
      cancelled = true;
    };
    // Intentionally depend only on `runCode`. The `wizard` context value is a
    // fresh object on every state update, so including it would re-run this
    // effect on every render. Same pattern as ProfileSetupStep / FindTeamStep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runCode]);

  // A run that cannot be joined says so here, on
  // the first step, not on the fifth. While it has not started, the page asks
  // again and says when it starts. "Weiter" stays usable: this step is also
  // the only way to the login for a participant whose session ran out.
  const loadedRun = rallyeState.status === 'loaded' ? rallyeState.run : null;
  const blocked: 'not_started' | 'ended' | null =
    loadedRun?.state === 'configured' || loadedRun?.state === 'scheduled'
      ? 'not_started'
      : loadedRun?.state === 'ended'
        ? 'ended'
        : null;
  const [sawStart, setSawStart] = useState(false);

  useEffect(() => {
    if (!runCode || blocked !== 'not_started') return;
    return pollWhileVisible((isStopped) =>
      fetchByCode(runCode).then((run) => {
        if (!run || isStopped()) return;
        if (run.state === 'active') setSawStart(true);
        setRallyeState({ status: 'loaded', run });
      }),
    );
  }, [runCode, blocked]);

  // Apply the language change immediately when the user picks a different
  // entry in the dropdown. The surrounding UI (including this page's own
  // labels) re-renders in the new language right away, so the user sees the
  // effect before clicking Continue.
  const onLanguageChange = (next: SupportedLanguage) => {
    setLang(next);
    void i18n.changeLanguage(next);
  };

  const onContinue = () => {
    const target = runCode ? `/join/${runCode}/auth-choice` : '/auth-choice';
    navigate({ pathname: target, search: location.search });
  };

  const orgArchived = rallyeState.status === 'org_archived';

  const step = getStepInfo('welcome', runCode ? 'with-code' : 'no-code');

  const rallyeSubtitle = (() => {
    if (rallyeState.status === 'loading') {
      return <Skeleton variant="text" width="60%" aria-label={t('rallyeLoading')} />;
    }
    if (rallyeState.status === 'loaded') {
      return (
        <Typography
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '14px',
            fontWeight: 500,
            lineHeight: '20px',
            letterSpacing: '0.10px',
            color: 'text.primary',
            // The design's rallye-subtitle box is 15 px tall and the CSS line height is
            // 20 px, so the CSS box is 5 px taller. Neutralize the forward push so no
            // sibling below drifts by 5 px.
            marginBlockEnd: '-5px',
          }}
        >
          {t('rallyePrefix')}
          {rallyeState.run.rallye_name}
        </Typography>
      );
    }
    return null;
  })();

  return (
    <OnboardingShell
      contentSx={{ paddingInline: 0 }}
      progress={
        <ProgressIndicator
          current={step.current}
          total={step.total}
          ariaLabel={t('progressAriaLabel', {
            current: step.current,
            total: step.total,
          })}
        />
      }
    >
      <Box
        sx={{
          backgroundColor: 'var(--color-primary-95, #E6E9F4)',
          width: '100%',
          aspectRatio: '393 / 280',
          display: 'flex',
          alignItems: 'flex-end',
          overflow: 'hidden',
        }}
      >
        <Box
          component="img"
          src={heroWelcome}
          alt=""
          sx={{
            width: '100%',
            height: 'auto',
            display: 'block',
          }}
        />
      </Box>
      <Box
        sx={{
          display: 'flex',
          flexDirection: 'column',
          gap: '24px',
          paddingInline: '40px',
          paddingBlockStart: '24px',
        }}
      >
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <Typography
            component="h1"
            sx={{
              fontFamily: "'Roboto', sans-serif",
              fontSize: '24px',
              fontWeight: 600,
              lineHeight: '32px',
              color: 'text.primary',
              margin: 0,
              marginBlockStart: '-2px',
            }}
          >
            <Trans
              t={t}
              i18nKey="headline"
              components={{
                brand: <Box component="span" sx={{ color: 'primary.main' }} />,
              }}
            />
          </Typography>
          {rallyeSubtitle}
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <Typography
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '16px',
              fontWeight: 500,
              lineHeight: '24px',
              letterSpacing: '0.15px',
              color: 'text.primary',
              marginBlockStart: '-3px',
              // The design's prompt box is 28 px tall and the CSS line height is 24 px, so
              // the CSS box is 4 px shorter. Extend the block bottom so the next sibling (the
              // dropdown wrapper) stays at the design's position.
              paddingBlockEnd: '4px',
            }}
          >
            {t('selectLanguagePrompt')}
          </Typography>
          {/*
            Half-leading compensation: the prompt above is body/large (line height 24,
            font about 18) with 3 px of CSS line-box leading per side that the design's
            tight text box does not carry. Pulling the prompt up by 3 px lands its glyph
            at the design's position. This wrapper pushes the dropdown back down by 3 px,
            so the dropdown and everything below it (helper, Continue) stays where it
            was.
          */}
          <Box sx={{ marginBlockStart: '3px', paddingBlockEnd: '4px' }}>
            <LanguageDropdown
              value={lang}
              onChange={onLanguageChange}
              label={t('languageLabel')}
              describedById={HELPER_TEXT_ID}
              options={[
                { code: 'de', label: t('langOptionDe') },
                { code: 'en', label: t('langOptionEn') },
              ]}
            />
          </Box>
        </Box>
        <Typography
          id={HELPER_TEXT_ID}
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '12px',
            fontWeight: 500,
            lineHeight: '16px',
            letterSpacing: '0.50px',
            color: '#000000',
          }}
        >
          {t('helperLanguageChangeable')}
        </Typography>
        {(blocked || sawStart) && (
          <Alert severity={blocked ? 'warning' : 'success'} role="status">
            {blocked === 'ended'
              ? t('runEnded')
              : blocked === 'not_started'
                ? loadedRun?.scheduled_for
                  ? t('runStartsAt', { when: formatDateTime(loadedRun.scheduled_for, i18n.language) })
                  : t('runNotStarted')
                : t('runStarted')}
          </Alert>
        )}
        {orgArchived ? (
          <Alert severity="warning" role="status">
            {t('join.errors.org_archived', { ns: 'onboarding' })}
          </Alert>
        ) : (
          <Box
            component="button"
            type="button"
            onClick={onContinue}
            // Until the first lookup answered, the page does not know
            // whether the run can be joined, so it offers no way on yet.
            disabled={rallyeState.status === 'loading'}
            sx={{
              all: 'unset',
              boxSizing: 'border-box',
              cursor: 'pointer',
              // Full-width primary action, like the primary button of every other
              // onboarding step. At 390 px a pill that fits its text sits lost in the left margin.
              alignSelf: 'stretch',
              width: '100%',
              minWidth: '89px',
              minHeight: '40px',
              paddingInline: '16px',
              borderRadius: '100px',
              backgroundColor: '#4D69B9',
              color: '#FFFFFF',
              fontFamily: "'Roboto', sans-serif",
              fontSize: '14px',
              fontWeight: 500,
              lineHeight: '20px',
              letterSpacing: '0.1px',
              whiteSpace: 'nowrap',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              '&:hover': { backgroundColor: '#3D54A0' },
              '&:disabled': { cursor: 'default', opacity: 0.6, backgroundColor: '#4D69B9' },
              ...pillButtonFocusRing,
            }}
          >
            {t('continue')}
          </Box>
        )}
      </Box>
    </OnboardingShell>
  );
};

export default WelcomeStep;
