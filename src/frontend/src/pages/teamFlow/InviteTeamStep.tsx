/**
 * Invite step of the team creator: shows the team's QR code and join code. It
 * bootstraps the team on entry, checks the run and the identity, copies the
 * code to the clipboard, advances the team stage on Continue and maps errors.
 */
import { Box, Snackbar, Typography } from '@mui/material';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

import { ApiError } from '../../api/client';
import {
  isAlreadyInTeam,
  isRunEnded,
  isRunNotStarted,
  isRunPaused,
  isTeamLimitReached,
  isDeparted,
  isOrgArchived,
} from './joinErrorReasons';
import { joinAsGuest } from '../../api/guest';
import { advanceTeamStage } from '../../api/teams';
import { useAuth } from '../../auth/useAuth';
import { fetchByCode, fetchMyTeam } from '../../services/publicRuns';
import ClavisCircularProgress from '../../components/ClavisCircularProgress';
import OnboardingShell from '../../components/OnboardingShell';
import ProgressIndicator from '../../components/ProgressIndicator';
import heroInvite from '../../assets/onboarding/hero-team-invite.svg';
import QrCodeDisplay from '../../components/qr/QrCodeDisplay';
import type { TeamFlowState, WizardStateContextValue } from '../../wizard/WizardStateContext';
import { useWizardState } from '../../wizard/useWizardState';
import { getStepInfo } from '../../wizard/wizardSteps';
import { pillButtonFocusRing } from '../../styles/onboardingButtons';
import { pollWhileVisible } from '../../utils/pollWhileVisible';
import { useDelayedFlag } from '../../hooks/useDelayedFlag';

type BootstrapErrorCode =
  | 'not_started'
  | 'paused'
  | 'ended'
  | 'team_limit'
  | 'already_in_team'
  | 'departed'
  | 'org_archived'
  | 'unknown';

type BootstrapOutcome =
  | { kind: 'settled' }
  | { kind: 'lookup-failed' }
  | { kind: 'create-failed'; error: BootstrapErrorCode };

/**
 * Dedupes the my-team lookup and the create that may follow it, per rallye
 * code, at module scope instead of on a ref of the component.
 *
 * A `useRef` guard resets when its component instance is thrown away. A real
 * unmount and remount of this step while the first request is in flight would
 * see a fresh `false` and fire a second create. StrictMode's double invoke is
 * not the case, because it replays effects on the *same* instance. The
 * `<WizardStateProvider>` lives above `<Routes>` in App.tsx and never remounts
 * with this step. So every caller shares one promise here and sees its result
 * applied to the same wizard state.
 */
const bootstrapsInFlight = new Map<string, Promise<BootstrapOutcome>>();

async function runBootstrap(
  rallyeCode: string,
  tf: TeamFlowState,
  wizard: WizardStateContextValue,
  checkAuth: () => Promise<void>,
): Promise<BootstrapOutcome> {
  const existing = bootstrapsInFlight.get(rallyeCode);
  if (existing) return existing;

  const promise = (async (): Promise<BootstrapOutcome> => {
    try {
      if (tf.teamId && tf.runId) {
        const mine = await fetchMyTeam(rallyeCode);
        const cachedStillMine =
          mine.kind === 'ok' &&
          mine.data.run_id === tf.runId &&
          mine.data.team_id === tf.teamId;
        if (cachedStillMine) {
          wizard.patchTeamFlow({
            teamShareCode: mine.data.team_share_code,
            teamNameFromServer: mine.data.team_name,
          });
          return { kind: 'settled' };
        }
        wizard.resetTeamFlow();
      }
      // Ask the server whether this user already has a team before creating
      // one. An unconditional create would give a creator who presses the
      // browser back button a second team, and the first team's members would
      // wait for a name nobody can set.
      const mine = await fetchMyTeam(rallyeCode);
      if (mine.kind === 'error') {
        // 429, a timeout, a network error or a 5xx: the lookup cannot tell
        // whether a team already exists, so creating here risks the very
        // duplicate the lookup prevents. Offer a retry instead.
        return { kind: 'lookup-failed' };
      }
      if (mine.kind === 'ok' && mine.data.team_id) {
        // Already in a team: adopt it. `is_creator` comes from the server
        // because a back or a reload loses the local flag.
        wizard.patchTeamFlow({
          teamId: mine.data.team_id,
          runId: mine.data.run_id,
          teamShareCode: mine.data.team_share_code,
          teamNameFromServer: mine.data.team_name,
          isCreator: mine.data.is_creator,
        });
        return { kind: 'settled' };
      }
      // The lookup said "no team": a 404, a 200 with team_id null, or a 401.
      // A visitor without a session cannot be in a team yet, and this create
      // call makes the guest account and sets the cookie.
      const resp = await joinAsGuest(rallyeCode, {
        mode: 'create',
        team_name: null,
        display_name: wizard.displayName || undefined,
      });
      wizard.patchTeamFlow({
        teamId: resp.team_id,
        teamShareCode: resp.team_share_code,
        teamNameFromServer: resp.team_name,
        runId: resp.run_id,
        isCreator: true,
      });
      await checkAuth();
      return { kind: 'settled' };
    } catch (err) {
      // A team can only be created or joined in an active run. The run-state
      // 422 of the guest-join and team-create endpoints maps to an explanation
      // instead of the generic "try again".
      let mapped: BootstrapErrorCode = 'unknown';
      // Both refusals are permanent: no "try again".
      if (isDeparted(err instanceof ApiError ? err : undefined)) {
        mapped = 'departed';
      } else if (isOrgArchived(err instanceof ApiError ? err : undefined)) {
        mapped = 'org_archived';
      } else if (err instanceof ApiError && err.status === 422) {
        // Like team_limit below, but worse: a participant already in a team
        // here cannot fix it at all, and both exits that look available ban
        // them from the run.
        if (isAlreadyInTeam(err)) {
          mapped = 'already_in_team';
        }
        // A reached team limit stays until a coordinator raises it, so
        // "please try again" would mislead.
        else if (isTeamLimitReached(err)) {
          mapped = 'team_limit';
        } else if (isRunPaused(err)) {
          mapped = 'paused';
        } else if (isRunEnded(err)) {
          mapped = 'ended';
        } else if (isRunNotStarted(err)) {
          mapped = 'not_started';
        }
      }
      return { kind: 'create-failed', error: mapped };
    }
  })();

  bootstrapsInFlight.set(rallyeCode, promise);
  void promise.finally(() => {
    if (bootstrapsInFlight.get(rallyeCode) === promise) {
      bootstrapsInFlight.delete(rallyeCode);
    }
  });
  return promise;
}

const InviteTeamStep: React.FC = () => {
  const { t } = useTranslation('onboarding');
  const navigate = useNavigate();
  const { code: rallyeCode } = useParams<{ code: string }>();
  const { checkAuth } = useAuth();
  const wizard = useWizardState();
  const tf = wizard.teamFlow;

  const [copied, setCopied] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(false);
  const [lookupFailed, setLookupFailed] = useState(false);
  const [bootstrapError, setBootstrapError] = useState<BootstrapErrorCode | null>(null);
  const bootstrappedRef = useRef(false);
  const showBootstrapIndicator = useDelayedFlag(bootstrapping);

  const step = getStepInfo('team-invite', 'with-code');

  const bootstrap = () => {
    if (!rallyeCode) return;
    setBootstrapping(true);
    setLookupFailed(false);
    setBootstrapError(null);
    void runBootstrap(rallyeCode, tf, wizard, checkAuth)
      .then((outcome) => {
        if (outcome.kind === 'lookup-failed') setLookupFailed(true);
        else if (outcome.kind === 'create-failed') setBootstrapError(outcome.error);
      })
      .finally(() => setBootstrapping(false));
  };

  useEffect(() => {
    if (bootstrappedRef.current) return;
    if (!rallyeCode) return;
    bootstrappedRef.current = true;
    bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rallyeCode]);

  const shareCode = tf.teamShareCode ?? '';
  const joinUrl =
    typeof window !== 'undefined' && rallyeCode && shareCode
      ? `${window.location.origin}/join/${rallyeCode}?team=${shareCode}`
      : '';

  const onCopy = async () => {
    if (!shareCode) return;
    try {
      await navigator.clipboard.writeText(shareCode);
      setCopied(true);
    } catch {
      // Some embedded contexts have no clipboard.
    }
  };

  const onContinue = async () => {
    if (tf.teamId && tf.runId && wizard.organizationSlug) {
      try {
        await advanceTeamStage({
          orgSlug: wizard.organizationSlug,
          runId: tf.runId,
          teamId: tf.teamId,
          stage: 'naming',
        });
      } catch {
        // Best effort: the creator moves on to naming even if the stage is not advanced.
      }
    }
    // `replace`: this step is done once the creator moves on. Kept in the
    // history, it would reopen "Lade dein Team ein" on a browser back from the
    // run. The naming step replaces itself the same way, so a back from the run
    // lands on the find step, which sends a member of a named team straight
    // back into the run.
    navigate(`/join/${rallyeCode}/team/name`, { replace: true });
  };

  // A guest who waits here for the start learns of it: while the run
  // has not started or is paused, ask its state about every 30 seconds and try
  // the team again once it runs. The question is a public read, so it adds
  // nothing to the count of refused joins on the run page.
  const waitsForRun = bootstrapError === 'not_started' || bootstrapError === 'paused';
  useEffect(() => {
    if (!waitsForRun || !rallyeCode) return;
    // `isStopped` guards an answer that arrives after the guest left the
    // page: a late "active" must not create a team behind their back.
    return pollWhileVisible((isStopped) =>
      fetchByCode(rallyeCode).then((run) => {
        if (!isStopped() && run?.state === 'active') bootstrap();
      }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [waitsForRun, rallyeCode]);

  // A refusal gets its own page: small red type on "Lade dein Team ein" would
  // sit between an empty QR box, the code "------" and a dead "Weiter".
  // `unknown` stays on the normal page: it can be retried.
  if (bootstrapError && bootstrapError !== 'unknown') {
    const goJoinExisting = () => {
      wizard.patchTeamFlow({ mode: 'join' });
      navigate(`/join/${rallyeCode}/team/join`, { replace: true });
    };
    return (
      <OnboardingShell
        progress={<ProgressIndicator current={step.current} total={step.total} />}
      >
        <Box sx={{ display: 'flex', flexDirection: 'column', paddingBlockStart: '40px', gap: '24px' }}>
          <Box
            component="img"
            src={heroInvite}
            alt=""
            sx={{ width: '107px', height: '98px', alignSelf: 'center', display: 'block', objectFit: 'contain' }}
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
            {t(`invite.refusedTitle.${bootstrapError}`, { defaultValue: t('invite.refusedTitle.default') })}
          </Typography>
          <Typography
            role="alert"
            sx={{ fontFamily: "'Noto Sans', sans-serif", fontSize: '16px', lineHeight: '24px', color: '#322F35' }}
          >
            {t(`invite.errors.${bootstrapError}`)}
          </Typography>
          {waitsForRun && (
            <Typography
              role="status"
              sx={{ fontFamily: "'Noto Sans', sans-serif", fontSize: '14px', lineHeight: '20px', color: '#605D64' }}
            >
              {t('invite.waitsForRun')}
            </Typography>
          )}
          <Box sx={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', minHeight: '48px' }}>
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
                display: 'inline-flex',
                alignItems: 'center',
                ...pillButtonFocusRing,
              }}
            >
              {t('invite.back')}
            </Box>
            {bootstrapError === 'team_limit' && (
              <Box
                component="button"
                type="button"
                onClick={goJoinExisting}
                sx={{
                  all: 'unset',
                  boxSizing: 'border-box',
                  cursor: 'pointer',
                  minHeight: '40px',
                  paddingInline: '16px',
                  borderRadius: '100px',
                  backgroundColor: '#4D69B9',
                  color: '#FFFFFF',
                  fontFamily: "'Roboto', sans-serif",
                  fontSize: '14px',
                  fontWeight: 500,
                  display: 'inline-flex',
                  alignItems: 'center',
                  '&:hover': { backgroundColor: '#3D54A0' },
                  ...pillButtonFocusRing,
                }}
              >
                {t('invite.joinExisting')}
              </Box>
            )}
          </Box>
        </Box>
      </OnboardingShell>
    );
  }

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
          paddingBlockStart: '40px',
          gap: '24px',
        }}
      >
        <Box
          component="img"
          src={heroInvite}
          alt=""
          sx={{
            width: '107px',
            height: '98px',
            alignSelf: 'center',
            display: 'block',
            objectFit: 'contain',
            marginBlockEnd: '13px',
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
            // headline/small: the CSS line box (32) is 4 px taller than the design's text
            // box (28). The half-leading puts the glyph 2 px below the line-box top. Pull
            // the element up 2 px so the glyph lands at the design's position and the
            // drift that propagates to the siblings drops from 4 px to 2 px.
            marginBlockStart: '-2px',
          }}
        >
          {t('invite.title')}
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
          {t('invite.qrHelp')}
        </Typography>
        {bootstrapError && (
          <Typography
            role="alert"
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '14px',
              color: '#B3261E',
            }}
          >
            {t(`invite.errors.${bootstrapError}`)}
          </Typography>
        )}
        {lookupFailed && (
          <Box
            role="alert"
            sx={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-start',
              gap: '8px',
            }}
          >
            <Typography
              sx={{
                fontFamily: "'Noto Sans', sans-serif",
                fontSize: '14px',
                color: '#B3261E',
              }}
            >
              {t('invite.errors.lookupFailed')}
            </Typography>
            <Box
              component="button"
              type="button"
              onClick={bootstrap}
              disabled={bootstrapping}
              sx={{
                all: 'unset',
                boxSizing: 'border-box',
                cursor: bootstrapping ? 'not-allowed' : 'pointer',
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
              {t('invite.retry')}
            </Box>
          </Box>
        )}
        {/* The QR block is 314 px wide at most (282 px code + 16 px padding each side) and shrinks
            with the window, square: the code box takes the width, the SVG scales by its viewBox. */}
        <Box
          sx={{
            alignSelf: 'stretch',
            marginBlockStart: '3px',
            minWidth: 0,
            '& > [role="img"]': { display: 'block', width: '314px', maxWidth: '100%', boxSizing: 'border-box' },
            '& > [role="img"] > span': { display: 'block' },
            '& > [role="img"] svg': { width: '100%', height: 'auto', display: 'block' },
          }}
        >
          {joinUrl ? (
            <QrCodeDisplay value={joinUrl} size={282} />
          ) : (
            <Box
              aria-busy={bootstrapping}
              sx={{
                width: '314px',
                maxWidth: '100%',
                aspectRatio: '1 / 1',
                backgroundColor: '#F5EFF7',
                borderRadius: '8px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              {showBootstrapIndicator && <ClavisCircularProgress indeterminate />}
            </Box>
          )}
        </Box>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <Typography
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '16px',
              fontWeight: 400,
              lineHeight: '24px',
              letterSpacing: '0.5px',
              color: '#322F35',
              // No half-leading pull-up on purpose. One would shrink the 88 px
              // block of text and code by 3 px and move "Once all…" and the
              // button row 4 px too high.
            }}
          >
            {t('invite.codeFallback')}
          </Typography>
          <Box
            component="button"
            type="button"
            onClick={onCopy}
            disabled={!shareCode}
            aria-label={`${shareCode} — ${t('invite.copyAria')}`}
            sx={{
              all: 'unset',
              boxSizing: 'border-box',
              cursor: shareCode ? 'pointer' : 'default',
              alignSelf: 'flex-start',
              fontFamily: "'Roboto', sans-serif",
              fontSize: '24px',
              fontWeight: 600,
              lineHeight: '32px',
              letterSpacing: 0,
              color: '#322F35',
              ...pillButtonFocusRing,
            }}
          >
            {shareCode || '------'}
          </Box>
        </Box>
        <Typography
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 400,
            lineHeight: '24px',
            letterSpacing: '0.5px',
            color: '#322F35',
          }}
        >
          {t('invite.footerHint')}
        </Typography>
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'row',
            gap: '16px',
            // The row is 48 px tall and centers 40 px button pills in it. The 4 px above
            // and below is the M3 state-layer band around each button. Reproduce the band,
            // so the visible pill sits at the design's position and not flush against the
            // row top.
            alignItems: 'center',
            minHeight: '48px',
          }}
        >
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
            {t('invite.back')}
          </Box>
          <Box
            component="button"
            type="button"
            onClick={onContinue}
            disabled={!shareCode || bootstrapping}
            sx={{
              all: 'unset',
              boxSizing: 'border-box',
              cursor: !shareCode || bootstrapping ? 'not-allowed' : 'pointer',
              minHeight: '40px',
              paddingInline: '16px',
              borderRadius: '100px',
              backgroundColor:
                !shareCode || bootstrapping ? 'rgba(29, 27, 32, 0.12)' : '#4D69B9',
              color: !shareCode || bootstrapping ? '#605D64' : '#FFFFFF',
              fontFamily: "'Roboto', sans-serif",
              fontSize: '14px',
              fontWeight: 500,
              lineHeight: '20px',
              letterSpacing: '0.1px',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              '&:hover': {
                backgroundColor:
                  !shareCode || bootstrapping
                    ? 'rgba(29, 27, 32, 0.12)'
                    : '#3D54A0',
              },
              ...pillButtonFocusRing,
            }}
          >
            {t('invite.continue')}
          </Box>
        </Box>
      </Box>
      <Snackbar
        open={copied}
        autoHideDuration={2000}
        onClose={() => setCopied(false)}
        message={t('invite.copied')}
      />
    </OnboardingShell>
  );
};

export default InviteTeamStep;
