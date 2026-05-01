/**
 * Wait screen of a team member while the creator names the team.
 *
 * Subscribes to the websocket, falls back to polling and shows a timeout
 * banner. Advances on `team.name_updated`.
 */
import { Box, Typography } from '@mui/material';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';

import { getTeam } from '../../api/teams';
import OnboardingShell from '../../components/OnboardingShell';
import ProgressIndicator from '../../components/ProgressIndicator';
import heroPending from '../../assets/onboarding/hero-team-pending.svg';
import infoIcon from '../../assets/onboarding/info.svg';
import { useWebSocket } from '../../hooks/useWebSocket';
import { useWizardState } from '../../wizard/useWizardState';
import { getStepInfo } from '../../wizard/wizardSteps';
import { pillButtonFocusRing } from '../../styles/onboardingButtons';

const WS_SILENCE_TO_POLLING_MS = 15_000;
const POLLING_INTERVAL_MS = 5_000;
const TOTAL_TIMEOUT_MS = 30_000;

const TeamNamePendingStep: React.FC = () => {
  const { t } = useTranslation('onboarding');
  const { t: tc } = useTranslation('common');
  const navigate = useNavigate();
  const wizard = useWizardState();
  const tf = wizard.teamFlow;

  // The banner needs both halves: the wait ran out (`timedOut`) AND a poll
  // failed (`pollFailed`). A socket that is open, or a creator who simply takes
  // their time, is not a connection problem.
  const [timedOut, setTimedOut] = useState(false);
  const [pollFailed, setPollFailed] = useState(false);
  const [members, setMembers] = useState<Array<{ id: string; display_name: string }>>([]);
  const lastWsActivityRef = useRef<number | null>(null);
  const pollingTimerRef = useRef<number | null>(null);

  const step = getStepInfo('team-name-pending', 'with-code');
  const canQuery = !!wizard.organizationSlug && !!tf.runId && !!tf.teamId;

  const advance = useCallback(
    (newName: string) => {
      wizard.patchTeamFlow({ teamNameFromServer: newName });
      if (tf.runId) {
        if (tf.teamId) {
          try {
            sessionStorage.setItem(`clavis_team_for_run_${tf.runId}`, tf.teamId);
          } catch { /* sessionStorage disabled */ }
        }
        // `replace`: the wizard ends here, so a back from
        // the run view must not land on the pending screen again.
        navigate(`/runs/${tf.runId}/participant/stations`, { replace: true });
      }
    },
    [navigate, tf.runId, tf.teamId, wizard],
  );

  const { status: wsStatus } = useWebSocket({
    path:
      canQuery
        ? `/api/orgs/${wizard.organizationSlug}/runs/${tf.runId}/ws`
        : '',
    enabled: canQuery,
    handlers: {
      'team.name_updated': (data) => {
        lastWsActivityRef.current = Date.now();
        const payload = data as { team_id: string; name: string | null } | undefined;
        if (!payload) return;
        if (payload.team_id !== tf.teamId) return;
        if (payload.name) advance(payload.name);
      },
    },
  });

  useEffect(() => {
    if (!canQuery) return;
    if (lastWsActivityRef.current === null) lastWsActivityRef.current = Date.now();

    const startPolling = () => {
      if (pollingTimerRef.current !== null) return;
      pollingTimerRef.current = window.setInterval(async () => {
        try {
          const team = await getTeam({
            orgSlug: wizard.organizationSlug!,
            runId: tf.runId!,
            teamId: tf.teamId!,
          });
          if (team.members) setMembers(team.members);
          if (team.name) {
            advance(team.name);
          }
        } catch {
          // A failed poll is the only evidence this client has that status
          // really cannot be refreshed. It is one half of the banner condition.
          setPollFailed(true);
        }
      }, POLLING_INTERVAL_MS);
    };

    const silenceCheck = window.setInterval(() => {
      const lastActivity = lastWsActivityRef.current ?? Date.now();
      if (Date.now() - lastActivity > WS_SILENCE_TO_POLLING_MS) {
        startPolling();
      }
    }, 1_000);

    return () => {
      window.clearInterval(silenceCheck);
      if (pollingTimerRef.current !== null) {
        window.clearInterval(pollingTimerRef.current);
        pollingTimerRef.current = null;
      }
    };
  }, [canQuery, advance, tf.runId, tf.teamId, wizard.organizationSlug]);

  // The wait clock runs only while the socket is down. An
  // open socket delivers `team.name_updated` the moment the creator submits,
  // however long that takes, so there is nothing to warn about.
  useEffect(() => {
    if (!canQuery) return;
    if (wsStatus === 'open') return;
    const timer = window.setTimeout(() => setTimedOut(true), TOTAL_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [canQuery, wsStatus]);

  // A socket that comes back also takes the banner away again.
  const showConnectionLost = timedOut && pollFailed && wsStatus !== 'open';

  const memberList =
    members.length === 0
      ? wizard.displayName || '...'
      : members.map((m) => m.display_name).join(', ');

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
          paddingBlockStart: '66px',
          gap: '24px',
        }}
      >
        <Box
          component="img"
          src={heroPending}
          alt=""
          sx={{
            width: '93px',
            height: '86px',
            alignSelf: 'center',
            display: 'block',
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
            // headline/small half-leading: glyph sits 2 px below the line-box top.
            marginBlockStart: '-2px',
            // The CSS line height (32) is 4 px taller than the design's text box (28).
            // Pull -3 px at the bottom to neutralize the propagation to all downstream
            // siblings.
            marginBlockEnd: '-3px',
          }}
        >
          {t('pending.title')}
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <Typography
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '16px',
              fontWeight: 500,
              lineHeight: '24px',
              letterSpacing: '0.15px',
              color: '#322F35',
              // The body/large line box has 3 px of leading above the glyph. The
              // design's text box has none. Pull the element up so the glyph lands at
              // the design's position.
              marginBlockStart: '-3px',
            }}
          >
            {t('pending.membersHeading')}
          </Typography>
          <Typography
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '14px',
              fontWeight: 400,
              lineHeight: '20px',
              letterSpacing: '0.25px',
              color: '#322F35',
              // The design's members-value box is 23 px tall (body/medium) and the CSS line
              // height is 20 px, so the CSS box is 3 px shorter. Extend the block bottom so
              // the info box below lands at the design's position.
              paddingBlockEnd: '3px',
            }}
          >
            {memberList}
          </Typography>
        </Box>
        <Box
          sx={{
            backgroundColor: '#E6E9F4',
            borderRadius: '22px',
            // Symmetric paddings 13/14/13/14. The design's box is 107 px tall and the CSS
            // content 94 px. The 14 px of slack lies inside the design's body box, as empty
            // space below the last glyph. Apply it as paddingBlockEnd of the body
            // Typography, not as asymmetric box padding, so the body visually extends
            // close to the box bottom.
            paddingBlockStart: '13px',
            paddingBlockEnd: '13px',
            // Hug overflow: the design's body text overflows the container's paddingRight
            // of 14 px by 5 px (effective right margin 9 px). Match it in CSS, so the body
            // wraps the same way.
            paddingLeft: '14px',
            paddingRight: '9px',
            display: 'flex',
            flexDirection: 'column',
            gap: '10px',
          }}
        >
          <Box
            sx={{
              alignSelf: 'flex-start',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px',
              backgroundColor: '#4D69B9',
              color: '#FFFFFF',
              borderRadius: '8px',
              paddingInline: '8px',
              paddingBlock: '1px',
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '11px',
              fontWeight: 500,
              lineHeight: '16px',
              letterSpacing: '0.5px',
            }}
          >
            <Box
              component="img"
              src={infoIcon}
              alt=""
              aria-hidden
              sx={{
                width: '12px',
                height: '12px',
                display: 'block',
                // info.svg paths are #1D1B20. Flip them to white to match the chip fill.
                filter: 'brightness(0) invert(1)',
              }}
            />
            {tc('infoBadge')}
          </Box>
          <Typography
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '14px',
              fontWeight: 400,
              lineHeight: '20px',
              letterSpacing: '0.25px',
              color: '#00348D',
              // The design's body box is 54 px tall and the CSS two lines come to 40 px. The
              // 14 px of descender slack lies inside the body box. Put the slack here, not on
              // the info box, so the body visually extends close to the info-box bottom and
              // is not top-weighted with an empty bottom.
              paddingBlockEnd: '14px',
            }}
          >
            {t('pending.info')}
          </Typography>
        </Box>
        <Typography
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 400,
            lineHeight: '24px',
            letterSpacing: '0.5px',
            color: '#322F35',
            // Upstream cascade is balanced (title mb-end-3 + info-box pad-end
            // 26 + members-block half-leading). The natural box top of the
            // discussion text lands 24 px below the info box, the design's
            // position. So its glyph (with half-leading +3) lands within 1 px of
            // the design's glyph. No own mb needed.
          }}
        >
          {t('pending.discussionPrompt')}
        </Typography>
        <Box
          role="status"
          aria-live="polite"
          sx={{
            display: 'flex',
            flexDirection: 'row',
            alignItems: 'center',
            gap: '24px',
          }}
        >
          {/* M3 Loading Indicator from @material/web (registered in main.tsx).
              Sized and colored via CSS custom properties: 44×44, brand #4D69B9, 8-dp
              thickness (≈ 18% of size). React 19 handles the custom element natively. */}
          <md-circular-progress
            indeterminate
            aria-label={t('pending.spinnerLabel')}
            style={{
              ['--md-circular-progress-size' as string]: '44px',
              ['--md-circular-progress-active-indicator-color' as string]: '#4D69B9',
              ['--md-circular-progress-active-indicator-width' as string]: '18',
            } as React.CSSProperties}
          />
          <Typography
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '14px',
              fontWeight: 500,
              lineHeight: '20px',
              letterSpacing: '0.1px',
              color: '#1D47A4',
            }}
          >
            {t('pending.spinnerLabel')}
          </Typography>
        </Box>
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'row',
            gap: '16px',
            // The row is 48 px tall with 40 px pills centered (M3 state-layer band).
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
              // An inset shadow draws the stroke inside the box and keeps the pill at 40 px
              // of width. A CSS border would add 2 px to the rendered width.
              border: 'none',
              boxShadow: 'inset 0 0 0 1px #CAC4D0',
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
            {t('pending.back')}
          </Box>
          <Box
            component="button"
            type="button"
            disabled
            sx={{
              all: 'unset',
              boxSizing: 'border-box',
              cursor: 'not-allowed',
              minHeight: '40px',
              paddingInline: '16px',
              borderRadius: '100px',
              // M3 disabled container: on-surface @ 12% (rgba(29,27,32,0.12)).
              backgroundColor: 'rgba(29, 27, 32, 0.12)',
              // M3 disabled label: on-surface @ 38%.
              color: 'rgba(29, 27, 32, 0.38)',
              fontFamily: "'Roboto', sans-serif",
              fontSize: '14px',
              fontWeight: 500,
              lineHeight: '20px',
              letterSpacing: '0.1px',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            {t('pending.submit')}
          </Box>
        </Box>
        {showConnectionLost && (
          <Typography
            role="alert"
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '14px',
              fontWeight: 400,
              lineHeight: '20px',
              color: '#8A4A00',
              backgroundColor: '#FFF4E5',
              border: '1px solid #F0B27A',
              borderRadius: '8px',
              padding: '12px',
            }}
          >
            {t('pending.connectionLost')}
          </Typography>
        )}
      </Box>
    </OnboardingShell>
  );
};

export default TeamNamePendingStep;
