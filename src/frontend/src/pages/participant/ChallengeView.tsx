import React, { useCallback, useEffect, useRef, useState } from 'react';
import { visuallyHiddenSx } from '../../styles/visuallyHidden';
import SafeHtml from '../../components/SafeHtml';
import { Link as RouterLink, useOutletContext, useParams, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { dirAttr, resolveLocalized } from '../../i18n/contentLocales';
import type { PublicRunState } from '../../services/publicRuns';
import {
  Alert,
  AlertTitle,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  Stack,
  Typography,
} from '@mui/material';
import { listRunChallenges, listRunStations, listSubmissions, updateSubmission } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { MultilingualString, RunChallenge, RunStation, Submission } from '../../api/types';
import { AnswerStateChip, AnswerStateIcon } from '../../components/AnswerState';
import {
  ANSWER_STATE_COLOR,
  ANSWER_STATE_TEXT_COLOR,
  deriveAnswerState,
} from '../../utils/answerState';
import ErrorAlert from '../../components/ErrorAlert';
import SubmittedAnswer from '../../components/SubmittedAnswer';
import LoadingSpinner from '../../components/LoadingSpinner';
import BackLink from '../../components/BackLink';
import StationAddress from '../../components/StationAddress';
import StationTipsDialog from '../../components/StationTipsDialog';
import LightbulbOutlinedIcon from '@mui/icons-material/LightbulbOutlined';
import TextChallengeForm from './TextChallengeForm';
import QuizChallengeForm from './QuizChallengeForm';
import QrScanChallengeForm from './QrScanChallengeForm';
import PhotoChallengeForm from './PhotoChallengeForm';
import VideoChallengeForm from './VideoChallengeForm';
import AudioChallengeForm from './AudioChallengeForm';
import type { ChallengeFormProps } from './TextChallengeForm';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';

interface TeamTabOutletContext {
  run: PublicRunState | null;
}

/**
 * The address link as a touch target. An inline link is only as high as its
 * glyphs, about 18 px. As a block of its own it takes the whole line height of
 * its text, 24 px, the least a touch target may have (WCAG 2.5.8).
 */
const ADDRESS_LINK_TARGET_SX = { '& a': { display: 'inline-block', minHeight: 24 } } as const;

/**
 * How often an open page re-reads the team's answers. A grading that the
 * coordinator gives or takes back sends no live signal. The standing follows
 * it by asking every ten seconds. This page asks at the same pace, so the two
 * never show a different state for longer than one round.
 */
const ANSWERS_REFRESH_MS = 10_000;


const ChallengeView: React.FC = () => {
  const { runId, stationId, challengeId } = useParams<{
    runId: string;
    stationId: string;
    challengeId: string;
  }>();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const { t, i18n } = useTranslation('runs');
  // Optional: ChallengeView is normally nested under the Outlet of TeamTab,
  // which supplies `run`. Tests and other embeddings must not crash without it,
  // and `useOutletContext()` is undefined outside a matching Outlet.
  const outlet = useOutletContext<TeamTabOutletContext>();
  const run = outlet?.run ?? null;
  // An ended or paused run does not offer to change an answer. The edit window
  // is meant to close with scoring, not to stay open past the run itself.
  const runLocked = run?.state === 'ended' || run?.state === 'paused';
  // The app knows which of the two locked states it is, so each has its own text.
  const runEnded = run?.state === 'ended';

  const [challenge, setChallenge] = useState<RunChallenge | null>(null);
  // The station of this task. Its address and its tips belong on the task page
  // too, so a team never has to leave the task to find them. Null while unknown
  // or when the read failed.
  const [station, setStation] = useState<RunStation | null>(null);
  const [tipsOpen, setTipsOpen] = useState(false);
  const [alreadySubmitted, setAlreadySubmitted] = useState(false);
  const [submission, setSubmission] = useState<Submission | null>(null);
  // Replace mode re-enables the form so the team can swap its unscored answer.
  // It ends on a successful replacement or on cancel.
  const [replaceMode, setReplaceMode] = useState(false);
  // A teammate answered first, and this member's own input was refused. The
  // input must not be dropped, or the screen would claim a success that did
  // not happen. It is kept until the member leaves the task,
  // shown above the team's answer and offered as the start of "Antwort ändern".
  const [refusedContent, setRefusedContent] = useState<Submission['content'] | null>(null);
  // The way back after a submit needs the whole station, not just the open
  // challenge. Whether any task here is still unanswered decides where the
  // primary button leads.
  const [stationChallengeIds, setStationChallengeIds] = useState<string[]>([]);
  const [submittedChallengeIds, setSubmittedChallengeIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // How many answers were sent in this visit. It is never reset, so a slow
  // reader still finds the result. It counts and does not flag: every send
  // changes it, so the focus moves to the result on the second send of a visit
  // too.
  const [sentCount, setSentCount] = useState(0);
  const announced = sentCount > 0;
  const announcementRef = useRef<HTMLDivElement>(null);
  // The notice that a teammate answered first takes the focus once, when it
  // first appears after the refusal. After that refusal the form is gone and
  // the focus with it. The refusal sets the flag and the notice uses it up, so
  // "Antwort ändern" and cancel do not focus the notice again.
  const noticeFocusPending = useRef(false);
  const focusNotice = useCallback((el: HTMLElement | null) => {
    if (el && noticeFocusPending.current) {
      noticeFocusPending.current = false;
      el.focus();
    }
  }, []);

  const orgSlug = user?.organization_slug;

  const applySubmissions = useCallback(
    (items: Submission[]) => {
      setSubmittedChallengeIds(new Set(items.map((s: Submission) => s.run_challenge_id)));
      const existing = items.find((s: Submission) => s.run_challenge_id === challengeId);
      if (existing) {
        setAlreadySubmitted(true);
        setSubmission(existing);
      }
      return Boolean(existing);
    },
    [challengeId],
  );

  // A run's stations and tasks are a frozen snapshot from its start, so they
  // are read once. Only the answers change while the page is open: a
  // teammate's answer, a grade, a reopened grade.
  const load = useCallback(async () => {
    if (!orgSlug || !runId || !stationId || !challengeId) return;
    setLoading(true);
    setError(null);
    try {
      const [challengesResp, submissionsResp, stationsResp] = await Promise.all([
        listRunChallenges(orgSlug, runId, stationId),
        listSubmissions(orgSlug, runId),
        // The task works without its station: a failed read only hides the
        // address and the tips.
        listRunStations(orgSlug, runId).catch(() => null),
      ]);
      const found = challengesResp.items.find((c) => c.id === challengeId) ?? null;
      setChallenge(found);
      setStationChallengeIds(challengesResp.items.map((c) => c.id));
      setStation(stationsResp?.items.find((s) => s.id === stationId) ?? null);
      applySubmissions(submissionsResp.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, runId, stationId, challengeId, applySubmissions]);

  // Resolves to whether the read succeeded and this task has an answer.
  const refreshSubmissions = useCallback(async (): Promise<boolean> => {
    if (!orgSlug || !runId) return false;
    try {
      const submissionsResp = await listSubmissions(orgSlug, runId);
      setError(null);
      return applySubmissions(submissionsResp.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      return false;
    }
  }, [orgSlug, runId, applySubmissions]);

  useEffect(() => {
    void load();
  }, [load]);

  // Without the refresh an open challenge would keep claiming the answer is
  // merely submitted. It reads only the answers, because the task never changes
  // during a run.
  useAutoRefresh(async () => {
    await refreshSubmissions();
  }, ANSWERS_REFRESH_MS);

  // The run can lock (end or pause) while a team is mid-edit, and the refresh
  // above notices it. An edit in flight must not survive that moment, so leave
  // replace mode as soon as the run locks.
  useEffect(() => {
    if (runLocked) setReplaceMode(false);
  }, [runLocked]);

  // A sent answer replaces the form, and the send button that had the focus
  // goes with it. The person must land on the result text instead (WCAG 4.1.3
  // and 2.4.3). This focus move is the one path that speaks a success, on
  // every send of a visit. It is an effect and not an inline `focus()` call, so
  // it runs after the announcement text has committed and the focused region
  // reads it.
  useEffect(() => {
    if (sentCount > 0) announcementRef.current?.focus();
  }, [sentCount]);

  // The full UI language, not collapsed to de or en. The content locales of an
  // organization are free, such as ar or fa. A participant with an Arabic
  // browser gets Arabic content, right to left.
  const activeLang = i18n.language;
  const titleOf = (ml: MultilingualString) => resolveLocalized(ml, activeLang).text;
  const dirOf = (ml: MultilingualString) => dirAttr(resolveLocalized(ml, activeLang).locale);

  const handleSubmitted = (result: Submission) => {
    setSubmission(result);
    setAlreadySubmitted(true);
    setReplaceMode(false);
    setRefusedContent(null);
    // Count the fresh answer towards "station done" at once. The next refresh
    // would do it, but until then the target of the primary button would claim
    // that this very challenge is still open.
    setSubmittedChallengeIds((prev) => new Set(prev).add(result.run_challenge_id));
    noticeFocusPending.current = false;
    setSentCount((n) => n + 1);
    // The answered view replaces a taller form, and the browser keeps the
    // scroll offset of the submit button. The person would then look at the
    // middle of the page, with the confirmation and the way back both above
    // the fold.
    window.scrollTo({ top: 0, behavior: 'auto' });
  };

  if (loading) return <LoadingSpinner />;

  if (!challenge) {
    return <ErrorAlert message={error ?? t('participant.challenge.notFound')} onRetry={load} />;
  }

  // Never render the replace form while the run is locked. The effect above
  // resets `replaceMode`, and this guard also covers the single render between
  // the run locking and that effect committing.
  const editingActive = replaceMode && !runLocked;

  const formProps: ChallengeFormProps = {
    orgSlug: orgSlug ?? '',
    runId: runId ?? '',
    challenge,
    alreadySubmitted: alreadySubmitted && !editingActive,
    onSubmitted: handleSubmitted,
    // A duplicate refusal (TEAM_ALREADY_SUBMITTED) means a teammate's answer
    // exists that this screen never loaded. The reload is quiet, because a full
    // spinner would flash and unmount the message the form just showed. It
    // resolves to whether the team's answer is on screen now. The form shows no
    // refusal of its own while the notice is to take the focus.
    onReload: (attempted) => {
      if (attempted) {
        setRefusedContent(attempted);
        noticeFocusPending.current = true;
      }
      return refreshSubmissions();
    },
    ...(editingActive && submission
      ? {
          submitAction: (content: Submission['content']) =>
            updateSubmission(orgSlug ?? '', runId ?? '', submission.id, { content }),
          // The member's own refused input, if any, is what they meant to send.
          initialContent: refusedContent ?? submission.content,
        }
      : {}),
  };

  const renderForm = () => {
    switch (challenge.challenge_type) {
      case 'text':
        return <TextChallengeForm {...formProps} />;
      case 'quiz':
        return <QuizChallengeForm {...formProps} />;
      case 'qr_scan':
        return <QrScanChallengeForm {...formProps} />;
      case 'photo':
        return <PhotoChallengeForm {...formProps} />;
      case 'video':
        return <VideoChallengeForm {...formProps} />;
      case 'audio':
        return <AudioChallengeForm {...formProps} />;
      // Retired type, found only in runs started before its retirement. The
      // photo form takes what the creative form took: a photo, a text, or both.
      case 'creative':
        return <PhotoChallengeForm {...formProps} />;
      default:
        return <ErrorAlert message={t('participant.challenge.unsupportedType', { type: challenge.challenge_type })} />;
    }
  };

  // The chip colour ties the challenge to its kind.
  const typeChipColor: Record<RunChallenge['challenge_type'], 'primary' | 'secondary' | 'tertiary' | 'default'> = {
    text: 'primary',
    quiz: 'tertiary',
    qr_scan: 'secondary',
    photo: 'primary',
    creative: 'secondary',
    audio: 'secondary',
    video: 'primary',
  };

  // Mirrors the backend predicate in services/auto_score.py: auto_approve is
  // only honoured for QUIZ and QR_SCAN. On any other type the flag can survive
  // a type change and does nothing. Checking the flag alone would tell
  // participants that their text answer was auto-scored when a human scored it.
  const isAutoScored =
    challenge.scoring_rules.auto_approve === true &&
    (challenge.challenge_type === 'quiz' || challenge.challenge_type === 'qr_scan');

  // Three situations need three texts. An auto-scored answer comes back graded
  // in the same breath as the submission, so "submitted" alone reads as if
  // nothing happened. An answer that waits on a coordinator should say so. A
  // coordinator-scored answer keeps the neutral wording and lets the
  // `scoredLocked` line below carry the detail.
  const answerState = deriveAnswerState(submission);
  const submittedMessageKey = !submission?.is_scored
    ? 'participant.challenge.successPendingReview'
    : isAutoScored
      ? 'participant.challenge.successAutoScored'
      : 'participant.challenge.successGeneric';

  // Once an answer exists, the authored instructions have done their job and
  // push the answer off screen, such as "Wähle die richtige Antwort" above an
  // answered quiz. They come back while replacing, when they are needed again.
  const showInstructions = !submission || editingActive;

  // After a submission one primary button leads back. It goes to the task list
  // of the station while tasks remain open there, and straight to the stations
  // overview once the station is done. It restores the map when the
  // participant came from there (`?from=map`, the convention that the station
  // view uses for its own back link).
  const fromMap = searchParams.get('from') === 'map';
  const stationsOverviewTo = fromMap
    ? `/runs/${runId}/participant/stations?view=map`
    : `/runs/${runId}/participant/stations`;
  // The way back into the station keeps `from=map` too. The station view
  // decides its own back target from it, and without it a participant who
  // arrived from the map would be stranded on the plain list view.
  const stationTo = fromMap
    ? `/runs/${runId}/participant/stations/${stationId}?from=map`
    : `/runs/${runId}/participant/stations/${stationId}`;
  const stationHasOpenChallenges = stationChallengeIds.some(
    (id) => !submittedChallengeIds.has(id),
  );

  return (
    <Stack spacing={2}>
      {/* The submit result as text for assistive tech. The confirmation is
          spoken once, by the focus that moves here after a send. The region
          holds the text and has no live role, so it is not read a second time
          as a change. There is no toast. A failed send is not announced here,
          because the alert of the form is its one announcement. The region is
          in the tree from the first render on, and tabIndex={-1} makes it the
          focus target. */}
      <Box
        ref={announcementRef}
        tabIndex={-1}
        sx={visuallyHiddenSx}
        data-testid="submit-announcement"
      >
        {announced ? t('participant.challenge.answerSubmitted') : ''}
      </Box>
      <BackLink
        to={stationTo}
        label={t('participant.challenge.backToStation')}
      />
      <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', rowGap: 0.5 }}>
        {/*
          The vocabulary split is deliberate. Players see friendly prompt names
          via `participant.challenge.types.*` (text → "Question", quiz → "Quiz
          question"). Authors see the technical type names via
          `challenge.types.*` ("Text", "Quiz"). Keep the two namespaces apart.

          The quiz label says "Quiz question", not "Multiple Choice". A quiz
          challenge takes exactly one answer, and "Multiple Choice" would
          promise a control the form never offers.
        */}
        <Chip
          label={t(`participant.challenge.types.${challenge.challenge_type}`, {
            defaultValue: challenge.challenge_type,
          })}
          size="small"
          color={typeChipColor[challenge.challenge_type] ?? 'default'}
        />
        {typeof challenge.scoring_rules.max_points === 'number' && (
          <Chip
            label={t('participant.challenge.points', {
              points: challenge.scoring_rules.max_points,
            })}
            size="small"
            variant="outlined"
          />
        )}
      </Stack>
      <Typography variant="h2" component="h1" dir={dirOf(challenge.title)}>
        {titleOf(challenge.title) || t('participant.challenge.untitled', { defaultValue: 'Aufgabe' })}
      </Typography>

      {/* The place and the tips of the station, inside the task. Tapping the
          address opens the maps app, as on the station page. The tips open
          over the task, so an answer that is not sent yet stays in the form. */}
      {station?.address && (
        <Box sx={ADDRESS_LINK_TARGET_SX}>
          <StationAddress
            address={station.address}
            lat={station.location_lat}
            lng={station.location_lng}
            label={titleOf(station.title)}
            prominent
          />
        </Box>
      )}
      {station?.tips && (
        <>
          <Button
            variant="outlined"
            size="small"
            startIcon={<LightbulbOutlinedIcon />}
            onClick={() => setTipsOpen(true)}
            sx={{ alignSelf: 'flex-start', minHeight: 44 }}
          >
            {t('participant.stationChallenges.tipsButton')}
          </Button>
          <StationTipsDialog tips={station.tips} open={tipsOpen} onClose={() => setTipsOpen(false)} />
        </>
      )}

      {error && <ErrorAlert message={error} />}

      {titleOf(challenge.instructions) && showInstructions && (
        <Card
          sx={{
            bgcolor: 'primary.main',
            color: 'primary.contrastText',
            border: 'none',
          }}
        >
          <CardContent>
            <Typography
              variant="overline"
              gutterBottom
              sx={{ display: 'block', opacity: 0.85, letterSpacing: 0.6 }}
            >
              {t('participant.challenge.instructions')}
            </Typography>
            <SafeHtml
              html={titleOf(challenge.instructions)}
              dir={dirOf(challenge.instructions)}
              sx={{ '& a': { color: 'inherit', textDecorationColor: 'inherit' } }}
            />
          </CardContent>
        </Card>
      )}

      {submission && refusedContent && !editingActive && (
        // Not a live region. It takes the focus when it appears, which is the
        // one path that speaks it. The form that showed the refusal is gone by
        // then.
        <Alert severity="warning" role="none" tabIndex={-1} ref={focusNotice}>
          <AlertTitle>{t('participant.challenge.teammateFirst.title')}</AlertTitle>
          <Typography variant="body2">{t('participant.challenge.teammateFirst.body')}</Typography>
          <Typography variant="body2" sx={{ mt: 1, fontWeight: 600 }}>
            {t('participant.challenge.teammateFirst.yours')}
          </Typography>
          <SubmittedAnswer content={refusedContent} caption={null} />
          {!submission.is_scored && !runLocked && (
            <Typography variant="body2" sx={{ mt: 1 }}>
              {t('participant.challenge.teammateFirst.replaceHint')}
            </Typography>
          )}
        </Alert>
      )}

      {submission && (
        <Card
          variant="outlined"
          sx={{ borderColor: ANSWER_STATE_COLOR[answerState] }}
        >
          <CardContent>
            {/* The sentence says the state, so the sign in front of it is
                hidden from assistive technology. Blue while the answer waits
                for scoring, green once it is scored. */}
            <Stack direction="row" spacing={1} sx={{ alignItems: 'flex-start' }}>
              <AnswerStateIcon state={answerState} decorative fontSize="small" />
              <Typography
                variant="body2"
                sx={{ color: ANSWER_STATE_TEXT_COLOR[answerState] }}
              >
                {t(submittedMessageKey)}
              </Typography>
            </Stack>
            {/* What was submitted comes before what can be done about it.
                Reading your own answer must not require entering the edit flow. */}
            {!editingActive && <SubmittedAnswer content={submission.content} />}
            {/* The run itself closed the edit window (ended or paused).
                Offering "Antwort ändern" here would promise an edit the team
                can no longer make. The check is independent of
                `submission.is_scored`, so a graded answer in a locked run gets
                this note too, not just the "already scored" one. */}
            {runLocked ? (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
                {t(
                  runEnded
                    ? 'participant.challenge.changeLockedEnded'
                    : 'participant.challenge.changeLockedPaused',
                )}
              </Typography>
            ) : (
              <>
                {!submission.is_scored && !editingActive && (
                  <>
                    <Button
                      variant="outlined"
                      size="small"
                      sx={{ mt: 1.5 }}
                      onClick={() => setReplaceMode(true)}
                    >
                      {t('participant.challenge.changeAnswer')}
                    </Button>
                    {/* The edit window this button opens ends with scoring. Say
                        so, or teams read "submitted" as final. */}
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                      {t('participant.challenge.editWindowHint')}
                    </Typography>
                  </>
                )}
                {!submission.is_scored && editingActive && (
                  <Button
                    variant="text"
                    size="small"
                    sx={{ mt: 1.5 }}
                    onClick={() => setReplaceMode(false)}
                  >
                    {t('participant.challenge.changeCancel')}
                  </Button>
                )}
              </>
            )}
            {submission.is_scored && (
              <>
                {/* The result comes first. A team opens a graded challenge to
                    learn what it scored, not to read that it may no longer edit. */}
                {submission.points !== null && (
                  <AnswerStateChip
                    state="scored"
                    sx={{ mt: 1.5 }}
                    label={t('participant.challenge.scoredWithPoints', {
                      points: submission.points,
                      maxPoints: submission.max_points ?? challenge.scoring_rules.max_points ?? 0,
                    })}
                  />
                )}
                <Typography variant="body2" sx={{ mt: 1, color: 'text.secondary' }}>
                  {t(
                    isAutoScored
                      ? 'participant.challenge.autoScoredLocked'
                      : 'participant.challenge.scoredLocked',
                  )}
                </Typography>
              </>
            )}
            {/* The one primary way back once an answer exists. Hidden while
                replacing, because mid-edit is no time to leave. */}
            {!editingActive && (
              <Button
                variant="contained"
                size="large"
                fullWidth
                component={RouterLink}
                sx={{ mt: 2 }}
                to={stationHasOpenChallenges ? stationTo : stationsOverviewTo}
              >
                {stationHasOpenChallenges
                  ? t('participant.challenge.backToTasks')
                  : t('participant.stationChallenges.backToStations')}
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {/* Once an answer exists and none is being changed, every form renders
          only the line "already answered". The box above says the same with
          more, so form and divider are left out then. */}
      {!(alreadySubmitted && !editingActive) && <Divider />}

      {/* An unanswered task in an ended or paused run offers no form. The
          server refuses the answer, so a form would invite a send that can
          only fail. */}
      {!submission && runLocked ? (
        <Alert severity="info">
          {t(
            runEnded
              ? 'participant.challenge.answerLockedEnded'
              : 'participant.challenge.answerLockedPaused',
          )}
        </Alert>
      ) : (
        <>
          {/* One answer counts for the whole team. Say so while the form is
              open, because two members who each submit "their" answer is the
              confusion this prevents. */}
          {(!submission || editingActive) && (
            <Typography variant="body2" color="text.secondary">
              {t('participant.challenge.teamSubmitHint')}
            </Typography>
          )}

          {/* The key forces a fresh form mount when replace mode toggles, so
              the form's initial state picks up the prefill (`initialContent`). */}
          {!(alreadySubmitted && !editingActive) && (
            <Box key={editingActive ? 'replace' : 'initial'}>{renderForm()}</Box>
          )}
        </>
      )}
    </Stack>
  );
};

export default ChallengeView;
