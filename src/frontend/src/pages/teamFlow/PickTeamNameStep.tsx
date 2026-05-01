/**
 * Naming step of the team creator, with its error state. Guards deep links,
 * polls the members, sends the name with a PATCH on submit, maps errors,
 * remembers the team in sessionStorage and navigates on.
 */
import { Box, Typography } from '@mui/material';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router';

import { ApiError } from '../../api/client';
import { listTeams } from '../../api/runs';
import { getTeam, patchTeamName } from '../../api/teams';
import ClavisTextField from '../../components/ClavisTextField';
import OnboardingShell from '../../components/OnboardingShell';
import ProgressIndicator from '../../components/ProgressIndicator';
import heroFerris from '../../assets/onboarding/hero-team-ferris.svg';
import infoIcon from '../../assets/onboarding/info.svg';
import { useWizardState } from '../../wizard/useWizardState';
import { getStepInfo } from '../../wizard/wizardSteps';
import { pillButtonFocusRing } from '../../styles/onboardingButtons';

interface MemberEntry {
  id: string;
  display_name: string;
}

const POLL_INTERVAL_MS = 5_000;

const PickTeamNameStep: React.FC = () => {
  const { t } = useTranslation('onboarding');
  const navigate = useNavigate();
  const { code: rallyeCode } = useParams<{ code: string }>();
  const wizard = useWizardState();
  const tf = wizard.teamFlow;

  const [submitting, setSubmitting] = useState(false);
  const [members, setMembers] = useState<MemberEntry[]>([]);
  const pollTimerRef = useRef<number | null>(null);
  // Names already taken in this run, lowercased/trimmed for
  // a case-insensitive comparison against the i18n suggestion strings.
  const [takenTeamNames, setTakenTeamNames] = useState<Set<string>>(new Set());
  // The lookup is async, so `takenTeamNames` starts empty — rendering
  // suggestions before it resolves would flash a name that turns out to be
  // taken. Gate the whole suggestions block on this instead of on the (always
  // populated) local `suggestions` list.
  const [takenNamesLoaded, setTakenNamesLoaded] = useState(false);
  // TEAM_NAME_TAKEN gets its own message, kept separate from
  // `tf.teamNameError` (still 'forbidden' | 'unknown') so the shared wizard
  // state doesn't need a third literal for this component-local case.
  const [takenNameError, setTakenNameError] = useState(false);

  const step = getStepInfo('team-pick-name', 'with-code');
  const suggestions: string[] = t('pickName.suggestions', { returnObjects: true }) as string[];
  const availableSuggestions = suggestions.filter(
    (s) => !takenTeamNames.has(s.trim().toLowerCase()),
  );

  useEffect(() => {
    if (!tf.teamId && rallyeCode) {
      navigate(`/join/${rallyeCode}/team/invite`, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!tf.teamId || !tf.runId || !wizard.organizationSlug) return;

    const fetchOnce = async () => {
      try {
        const team = await getTeam({
          orgSlug: wizard.organizationSlug!,
          runId: tf.runId!,
          teamId: tf.teamId!,
        });
        if (team.members) {
          setMembers(
            team.members.map((m) => ({ id: m.id, display_name: m.display_name })),
          );
        }
      } catch {
        // swallow
      }
    };

    void fetchOnce();
    pollTimerRef.current = window.setInterval(fetchOnce, POLL_INTERVAL_MS);
    return () => {
      if (pollTimerRef.current !== null) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [tf.teamId, tf.runId, wizard.organizationSlug]);

  useEffect(() => {
    if (!tf.runId || !wizard.organizationSlug) return;
    let cancelled = false;

    const fetchTakenNames = async () => {
      try {
        const result = await listTeams(wizard.organizationSlug!, tf.runId!);
        if (cancelled) return;
        // The run's own team — the one this step is naming — is listed too,
        // and its `name` is still null at this point in the flow (the API
        // types claim `string`, but the backend really does send null for an
        // unnamed team). Drop unnamed entries before lowercasing/trimming so
        // that doesn't throw and silently unfilter every suggestion.
        const taken = result.items
          .map((team) => team.name)
          .filter((name): name is string => Boolean(name))
          .map((name) => name.trim().toLowerCase());
        setTakenTeamNames(new Set(taken));
      } catch {
        // Suggestions simply stay unfiltered; the server still rejects a
        // taken name on submit either way.
      } finally {
        if (!cancelled) setTakenNamesLoaded(true);
      }
    };

    void fetchTakenNames();
    return () => {
      cancelled = true;
    };
  }, [tf.runId, wizard.organizationSlug]);

  const onChip = (value: string) => {
    wizard.patchTeamFlow({ teamName: value, teamNameError: null });
    setTakenNameError(false);
  };

  const onSubmit = async () => {
    if (!tf.teamId || !tf.runId || !wizard.organizationSlug) return;
    const name = tf.teamName.trim();
    if (!name) return;
    setSubmitting(true);
    wizard.patchTeamFlow({ teamNameError: null });
    setTakenNameError(false);
    try {
      const result = await patchTeamName({
        orgSlug: wizard.organizationSlug,
        runId: tf.runId,
        teamId: tf.teamId,
        name,
      });
      wizard.patchTeamFlow({ teamNameFromServer: result.name });
      if (tf.runId && tf.teamId) {
        try {
          sessionStorage.setItem(`clavis_team_for_run_${tf.runId}`, tf.teamId);
        } catch {
          // sessionStorage disabled
        }
      }
      // `tf.runId` is guaranteed truthy here (guarded at the top of onSubmit),
      // so the happy path always lands on the participant run view.
      //
      // `replace`: the wizard is finished here. Keeping the entry would let a
      // browser back from the run view re-enter the naming step, which then
      // tries to rename a team that already has a name.
      navigate(`/runs/${tf.runId}/participant/stations`, { replace: true });
    } catch (err) {
      let mapped: 'forbidden' | 'unknown' | null = 'unknown';
      let serverDetail: string | null = null;
      let taken = false;
      if (err instanceof ApiError) {
        serverDetail = err.detail || null;
        if (err.errorCode === 'TEAM_NAME_TAKEN') {
          taken = true;
          mapped = null;
        } else if (err.status === 403 || err.status === 409 || err.status === 422) {
          mapped = 'forbidden';
        }
      }
      setTakenNameError(taken);
      wizard.patchTeamFlow({ teamNameError: mapped, teamNameErrorDetail: serverDetail });
    } finally {
      setSubmitting(false);
    }
  };

  // Show only real members from the server. Falling back to wizard.displayName
  // would surface a stale per-user name (e.g. a previous run) on every team —
  // so during the polling-empty window we render a neutral placeholder.
  const memberList =
    members.length === 0 ? '…' : members.map((m) => m.display_name).join(', ');

  const hasError = tf.teamNameError !== null || takenNameError;
  const submitDisabled = !tf.teamName.trim() || submitting;

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
          src={heroFerris}
          alt=""
          sx={{
            width: '93px',
            height: '86px',
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
          {t('pickName.title')}
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
            {t('pickName.membersHeading')}
          </Typography>
          <Typography
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '14px',
              fontWeight: 400,
              lineHeight: '20px',
              letterSpacing: '0.25px',
              color: '#322F35',
              // The heading carries -3 px half-leading, but its CSS box is 1 px taller than
              // the design's box. Without compensation the value glyph lands 3 px too low.
              // Pull it up to match.
              marginBlockStart: '-3px',
              // The design's value box is 23 px tall and the CSS line height is 20 px, so the
              // block bottom is 3 px shorter. Pad the bottom so the info box below lands at
              // the design's position. Otherwise it drifts up by 3 px.
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
            // The design's message box is 107 px tall. The CSS content sums to about 95 px
            // (13 + 18 chip + 12 gap + about 40 for the two-line body + 13). The 12 px delta
            // is auto-layout slack in the design. Render it as asymmetric bottom padding,
            // so the visible box matches the design's height.
            paddingBlockStart: '13px',
            paddingBlockEnd: '25px',
            paddingInline: '14px',
            display: 'flex',
            flexDirection: 'column',
            gap: '12px',
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
                // The vendored info.svg paths are #1D1B20. Flip them to white to match the
                // chip icon fill without shipping a duplicate.
                filter: 'brightness(0) invert(1)',
              }}
            />
            Info
          </Box>
          <Typography
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '14px',
              fontWeight: 400,
              lineHeight: '20px',
              letterSpacing: '0.25px',
              color: '#00348D',
            }}
          >
            {t('pickName.info')}
          </Typography>
        </Box>
        <ClavisTextField
          label={t('pickName.nameLabel')}
          helperText={
            hasError
              ? takenNameError
                ? t('pickName.errors.taken')
                : `${t(`pickName.errors.${tf.teamNameError}`)}${
                    tf.teamNameErrorDetail ? ` (${tf.teamNameErrorDetail})` : ''
                  }`
              : t('pickName.nameHelper')
          }
          error={hasError}
          value={tf.teamName}
          onChange={(e) => {
            wizard.patchTeamFlow({
              teamName: e.target.value,
              teamNameError: null,
            });
            setTakenNameError(false);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void onSubmit();
          }}
          slotProps={{ htmlInput: { maxLength: 100 } }}
          fullWidth
          variant="outlined"
          sx={{
            // A residual drift of 3 px remains at the text field after the compensations
            // above. Pull the field up by 6 px so the glyph lands at the design's position.
            marginBlockStart: '-6px',
            // The design's supporting text sits flush with the bottom stroke of the field.
            // MUI's default `FormHelperText` has marginTop=3, which pushes the helper 3 px
            // lower. Override it to 0 so the helper matches the design.
            '& .MuiFormHelperText-root': { marginTop: 0 },
            // Error state: stroke, label and supporting text are all #8C1D18 in the design.
            // MUI's default error.main is #d32f2f. Override it locally.
            '& .MuiOutlinedInput-root.Mui-error .MuiOutlinedInput-notchedOutline':
              { borderColor: '#8C1D18' },
            '& .MuiOutlinedInput-root.Mui-error:hover .MuiOutlinedInput-notchedOutline':
              { borderColor: '#8C1D18' },
            '& .MuiInputLabel-root.Mui-error': { color: '#8C1D18' },
            '& .MuiFormHelperText-root.Mui-error': { color: '#8C1D18' },
          }}
        />
        {takenNamesLoaded && availableSuggestions.length > 0 && (
        <Box
          sx={{
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
            // The helper's marginTop of 0 collapses the MUI box by 3 px, so the suggestions
            // would start 3 px higher. The offset is 7 px instead of 4 px to keep the
            // suggestion title at the design's position.
            marginBlockStart: '7px',
          }}
        >
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
            {t('pickName.suggestionsTitle')}
          </Typography>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
            {availableSuggestions.map((s) => (
              <Box
                key={s}
                component="button"
                type="button"
                onClick={() => onChip(s)}
                sx={{
                  all: 'unset',
                  boxSizing: 'border-box',
                  cursor: 'pointer',
                  height: '32px',
                  paddingInline: '12px',
                  borderRadius: '8px',
                  // An inset shadow draws the stroke inside the box. A CSS border would add 2 px
                  // of width.
                  border: 'none',
                  boxShadow: 'inset 0 0 0 1px #CAC5CD',
                  fontFamily: "'Roboto', sans-serif",
                  fontSize: '14px',
                  fontWeight: 500,
                  lineHeight: '20px',
                  letterSpacing: '0.1px',
                  color: '#322F35',
                  display: 'inline-flex',
                  alignItems: 'center',
                  '&:hover': { backgroundColor: 'rgba(0,0,0,0.04)' },
                  ...pillButtonFocusRing,
                }}
              >
                {s}
              </Box>
            ))}
          </Box>
        </Box>
        )}
        <Box sx={{ display: 'flex', flexDirection: 'row', gap: '16px', marginBlockStart: '6px' }}>
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
            {t('pickName.back')}
          </Box>
          <Box
            component="button"
            type="button"
            onClick={onSubmit}
            disabled={submitDisabled}
            sx={{
              all: 'unset',
              boxSizing: 'border-box',
              cursor: submitDisabled ? 'not-allowed' : 'pointer',
              minHeight: '40px',
              paddingInline: '16px',
              borderRadius: '100px',
              backgroundColor: submitDisabled
                ? 'rgba(29, 27, 32, 0.12)'
                : '#4D69B9',
              color: submitDisabled ? '#605D64' : '#FFFFFF',
              fontFamily: "'Roboto', sans-serif",
              fontSize: '14px',
              fontWeight: 500,
              lineHeight: '20px',
              letterSpacing: '0.1px',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              '&:hover': {
                backgroundColor: submitDisabled
                  ? 'rgba(29, 27, 32, 0.12)'
                  : '#3D54A0',
              },
              ...pillButtonFocusRing,
            }}
          >
            {t('pickName.submit')}
          </Box>
        </Box>
      </Box>
    </OnboardingShell>
  );
};

export default PickTeamNameStep;
