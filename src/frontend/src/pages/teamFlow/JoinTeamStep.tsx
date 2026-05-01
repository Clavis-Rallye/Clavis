/**
 * Join step of a teammate: scan the team's QR code or type the code by hand.
 * Maps join errors, branches to the later-team-joining flow and remembers the
 * team of the run in sessionStorage.
 */
import { Box, Typography } from '@mui/material';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

import { ApiError } from '../../api/client';
import {
  isAlreadyInTeam,
  isRunEnded,
  isRunNotStarted,
  isRunPaused,
  isTeamFull,
  cameraErrorCode,
  isDeparted,
  isOrgArchived,
} from './joinErrorReasons';
import { joinAsGuest } from '../../api/guest';
import ClavisCircularProgress from '../../components/ClavisCircularProgress';
import ClavisTextField from '../../components/ClavisTextField';
import OnboardingShell from '../../components/OnboardingShell';
import ProgressIndicator from '../../components/ProgressIndicator';
import heroJoin from '../../assets/onboarding/hero-team-join.svg';
import QrScanner from '../../components/qr/QrScanner';
import { useAuth } from '../../auth/useAuth';
import { useWizardState } from '../../wizard/useWizardState';
import { getStepInfo } from '../../wizard/wizardSteps';
import { pillButtonFocusRing } from '../../styles/onboardingButtons';
import { extractTeamCode } from './teamCode';
import { useDelayedFlag } from '../../hooks/useDelayedFlag';

// Kept local rather than on the shared wizard TeamFlowState (mirrors
// InviteTeamStep's BootstrapErrorCode): nothing outside this step reads a
// join failure, and `paused`/`ended` are two more codes than the run-state
// 422 the invite step's create path already covers.
type JoinErrorCode =
  | 'not_team_code'
  | 'not_found'
  | 'full'
  | 'not_started'
  | 'paused'
  | 'ended'
  | 'rate_limited'
  | 'already_in_team'
  | 'cameraDenied'
  | 'cameraUnavailable'
  | 'departed'
  | 'org_archived'
  | 'unknown';

const JoinTeamStep: React.FC = () => {
  const { t } = useTranslation('onboarding');
  const navigate = useNavigate();
  const { code: rallyeCode } = useParams<{ code: string }>();
  const wizard = useWizardState();
  const { checkAuth } = useAuth();
  const tf = wizard.teamFlow;

  const [scannerOpen, setScannerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [joinError, setJoinError] = useState<JoinErrorCode | null>(null);
  // Synchronous in-flight guard: `submitting` (React state) only updates on
  // the next render, so two submit triggers landing in the same tick (Enter
  // keydown + button click, double-tap, QR decode + click) could both pass a
  // `!submitting` check and double-fire the join request.
  const inFlightRef = useRef(false);
  const showJoiningIndicator = useDelayedFlag(submitting);

  const step = getStepInfo('team-join', 'with-code');

  const submitCode = async (rawCode: string) => {
    if (!rallyeCode) return;
    if (inFlightRef.current) return;
    const code = rawCode.trim().toUpperCase();
    if (!code) return;
    inFlightRef.current = true;
    setSubmitting(true);
    setJoinError(null);
    try {
      const resp = await joinAsGuest(rallyeCode, {
        mode: 'join',
        team_code: code,
        display_name: wizard.displayName || undefined,
      });
      wizard.patchTeamFlow({
        teamId: resp.team_id,
        teamShareCode: resp.team_share_code,
        teamNameFromServer: resp.team_name,
        runId: resp.run_id,
        isCreator: false,
        enteredCode: code,
      });
      await checkAuth();
      if (resp.team_name !== null) {
        try {
          sessionStorage.setItem(
            `clavis_team_for_run_${resp.run_id}`,
            resp.team_id,
          );
        } catch {
          // sessionStorage disabled
        }
        navigate(`/runs/${resp.run_id}/participant/stations`, { replace: true });
      } else {
        navigate(`/join/${rallyeCode}/team/waiting`);
      }
    } catch (err) {
      let mapped: JoinErrorCode = 'unknown';
      if (err instanceof ApiError) {
        if (err.status === 404) mapped = 'not_found';
        // Both refusals are permanent: no "try again".
        else if (isDeparted(err)) mapped = 'departed';
        else if (isOrgArchived(err)) mapped = 'org_archived';
        else if (err.status === 422) {
          // error_code is checked first. The prose match inside each `is*` helper is
          // only the fallback, for backend details that carry no code. Mirrors the
          // InviteTeamStep mapping. `full` reads TEAM_FULL, the target team's own
          // member-limit refusal.
          // Being in another team is permanent from the participant's side and has a
          // trap next to it: a guest who leaves cannot re-enter the run at all, and the
          // coordinator's only tool, removing the membership, bans them the same way.
          // So the generic fallback must not tell them to try again.
          if (isAlreadyInTeam(err)) {
            mapped = 'already_in_team';
          } else if (isTeamFull(err)) {
            mapped = 'full';
          } else if (isRunPaused(err)) {
            mapped = 'paused';
          } else if (isRunEnded(err)) {
            mapped = 'ended';
          } else if (isRunNotStarted(err)) {
            mapped = 'not_started';
          }
        } else if (err.status === 429) mapped = 'rate_limited';
      }
      setJoinError(mapped);
    } finally {
      inFlightRef.current = false;
      setSubmitting(false);
    }
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
          paddingBlockStart: '52px',
          gap: '24px',
        }}
      >
        <Box
          component="img"
          src={heroJoin}
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
            letterSpacing: 0,
            color: '#322F35',
            margin: 0,
          }}
        >
          {t('join.title')}
        </Typography>
        <Typography
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 400,
            lineHeight: '24px',
            letterSpacing: '0.5px',
            color: '#322F35',
            // The body/large line box has 3 px of leading above the glyph. The
            // design's text box has none. Pull the element up so the glyph lands at
            // the design's position.
            marginBlockStart: '-3px',
          }}
        >
          {t('join.body')}
        </Typography>
        {scannerOpen ? (
          <QrScanner
            size={260}
            onDecode={(text) => {
              setScannerOpen(false);
              const code = extractTeamCode(text);
              if (code === null) {
                // Some other QR code. Say so and leave the field alone.
                setJoinError('not_team_code');
                document.getElementById('team-code-input')?.focus();
                return;
              }
              wizard.patchTeamFlow({ enteredCode: code });
              void submitCode(code);
            }}
            onError={(err) => {
              setScannerOpen(false);
              setJoinError(cameraErrorCode(err));
              const el = document.getElementById('team-code-input');
              el?.focus();
            }}
          />
        ) : (
          <Box
            component="button"
            type="button"
            onClick={() => {
              setJoinError(null);
              setScannerOpen(true);
            }}
            sx={{
              all: 'unset',
              boxSizing: 'border-box',
              cursor: 'pointer',
              alignSelf: 'flex-start',
              // Offset calibration: the -3 px margin of the first body text propagates 3 px
              // upward to all later siblings. Scan needs a net shift of -6 px to land at the
              // design's position, so its own marginBlockStart is -3 px.
              marginBlockStart: '-3px',
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
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              justifyContent: 'center',
              '&:hover': { backgroundColor: '#3D54A0' },
              ...pillButtonFocusRing,
            }}
          >
            {t('join.scanCta')}
          </Box>
        )}
        <Typography
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 400,
            lineHeight: '24px',
            letterSpacing: '0.5px',
            color: '#322F35',
            // Half-leading compensation, inverted from the first body text: Scan above has
            // a -3 px marginBlockStart that already pulled this element up by 3 px. The
            // body/large half-leading would pull it up by another 3 px, 6 px too high in
            // total. Push it back down by 3 px to restore the design's position.
            marginBlockStart: '3px',
          }}
        >
          {t('join.fallbackHint')}
        </Typography>
        <ClavisTextField
          id="team-code-input"
          label={t('join.codeLabel')}
          value={tf.enteredCode}
          onChange={(e) => {
            wizard.patchTeamFlow({ enteredCode: e.target.value.toUpperCase() });
            setJoinError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !submitting) void submitCode(tf.enteredCode);
          }}
          slotProps={{ htmlInput: { maxLength: 8 } }}
          helperText={
            joinError !== null ? (
              <>
                {t(`join.errors.${joinError}`)}
                {joinError === 'not_found' && (
                  <Box
                    component="span"
                    sx={{ display: 'block', marginBlockStart: '4px' }}
                  >
                    {t('join.errors.scanHint')}
                  </Box>
                )}
              </>
            ) : (
              ' '
            )
          }
          error={joinError !== null}
          disabled={submitting}
          fullWidth
          variant="outlined"
          sx={{
            // Half-leading and cascade compensation: the +3 px of the second body text
            // propagates 3 px upward into the text field's natural position. The field needs
            // a net shift of -9 px to land at the design's position. After the compensations
            // upstream, the remainder here is -6 px.
            marginBlockStart: '-6px',
            // Error state: stroke, label and supporting text are all #8C1D18 in the design.
            // MUI's default error.main is #d32f2f.
            '& .MuiOutlinedInput-root.Mui-error .MuiOutlinedInput-notchedOutline':
              { borderColor: '#8C1D18' },
            '& .MuiOutlinedInput-root.Mui-error:hover .MuiOutlinedInput-notchedOutline':
              { borderColor: '#8C1D18' },
            '& .MuiInputLabel-root.Mui-error': { color: '#8C1D18' },
            '& .MuiFormHelperText-root.Mui-error': { color: '#8C1D18' },
          }}
        />
        {/* The design shows only Back here, but the soft-keyboard "Done" key is not
            reliable for form submit on mobile. An explicit Join button next to Back
            appears as soon as the user has typed something. It stays hidden while the
            input is empty, so the default state matches the design. */}
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'row',
            gap: '16px',
            // Offset calibration: the compensations upstream add up to -9 px (first body
            // text -3, Scan -3, second body text +3, field -6). The Back pill needs a total
            // shift of -25 px to land at the design's position, so its own marginBlockStart
            // is -16 px. The total includes the helper text overflow inside MUI (+20) and
            // the offset of the button row's state-layer band (-4).
            marginBlockStart: '-16px',
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
            {t('join.back')}
          </Box>
          {tf.enteredCode.trim().length > 0 && (
            <Box
              component="button"
              type="button"
              onClick={() => void submitCode(tf.enteredCode)}
              disabled={submitting}
              aria-busy={submitting}
              sx={{
                all: 'unset',
                boxSizing: 'border-box',
                cursor: submitting ? 'not-allowed' : 'pointer',
                minHeight: '40px',
                paddingInline: '16px',
                borderRadius: '100px',
                backgroundColor: submitting
                  ? 'rgba(29, 27, 32, 0.12)'
                  : '#4D69B9',
                color: submitting ? '#605D64' : '#FFFFFF',
                fontFamily: "'Roboto', sans-serif",
                fontSize: '14px',
                fontWeight: 500,
                lineHeight: '20px',
                letterSpacing: '0.1px',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                justifyContent: 'center',
                '&:hover': {
                  backgroundColor: submitting
                    ? 'rgba(29, 27, 32, 0.12)'
                    : '#3D54A0',
                },
                ...pillButtonFocusRing,
              }}
            >
              {showJoiningIndicator && (
                <ClavisCircularProgress size={16} indeterminate activeColor="#605D64" />
              )}
              {showJoiningIndicator ? t('join.joining') : t('join.submitTeam')}
            </Box>
          )}
        </Box>
      </Box>
    </OnboardingShell>
  );
};

export default JoinTeamStep;
