import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { dirAttr, localizedText, resolveLocalized } from '../../i18n/contentLocales';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import {
  listSubmissions,
  listTeams,
  listRunStations,
  listRunChallenges,
  listScores,
  createScore,
  deleteScore,
} from '../../api/runs';
import { getFileUrl } from '../../api/files';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { RunChallenge, Score, Submission, Team } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import BackLink from '../../components/BackLink';
import ConfirmDialog from '../../components/ConfirmDialog';
import SafeHtml from '../../components/SafeHtml';
import { AnswerStateChip, AnswerStateIcon } from '../../components/AnswerState';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';

/**
 * How often an open grading view asks for new answers. A coordinator sits on
 * this page during a run and waits for answers. A run has one to three people
 * on it, so three small requests every 10 s cost the server next to nothing.
 */
const GRADING_REFRESH_MS = 10_000;

type ScoreFilter = 'all' | 'scored' | 'unscored';

interface ScoringRowProps {
  orgSlug: string;
  runId: string;
  submission: Submission;
  maxPoints: number;
  onScored: (score: Score) => void;
}

const ScoringRow: React.FC<ScoringRowProps> = ({
  orgSlug,
  runId,
  submission,
  maxPoints,
  onScored,
}) => {
  const { t } = useTranslation('runs');
  const [points, setPoints] = useState('0');
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleScore = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const score = await createScore(orgSlug, runId, {
        submission_id: submission.id,
        points: parseInt(points, 10),
        comment: comment.trim() || undefined,
      });
      onScored(score);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Stack spacing={1} sx={{ mt: 1 }}>
      {error && <ErrorAlert message={error} />}
      <Stack direction="row" spacing={1} sx={{
        alignItems: "center"
      }}>
        <TextField
          label={t('submissions.scoreLabel')}
          value={points}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              setPoints("");
              return;
            }
            const n = Number(raw);
            if (Number.isNaN(n)) return;
            setPoints(String(Math.min(Math.max(n, 0), maxPoints)));
          }}
          type="number"
          slotProps={{ htmlInput: { min: 0, max: maxPoints } }}
          size="small"
          helperText={t('submissions.maxPointsHint', { max: maxPoints })}
          sx={{ width: 120 }}
        />
        <TextField
          label={t('submissions.commentLabel')}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          size="small"
          helperText={t('submissions.commentHelper')}
          sx={{ flexGrow: 1 }}
        />
        <Button
          variant="contained"
          size="small"
          onClick={() => void handleScore()}
          disabled={submitting}
        >
          {t('submissions.scoreButton')}
        </Button>
      </Stack>
    </Stack>
  );
};

const SubmissionReview: React.FC = () => {
  const { t, i18n } = useTranslation('runs');
  const { user } = useAuth();
  const { runId } = useParams<{ runId: string }>();

  const orgSlug = user?.organization_slug ?? '';
  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';

  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [challenges, setChallenges] = useState<RunChallenge[]>([]);
  const [scores, setScores] = useState<Score[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [scoreFilter, setScoreFilter] = useState<ScoreFilter>('all');
  const [challengeFilter, setChallengeFilter] = useState<string>('all');

  // Re-open grading: deleting the score lets the team replace its answer.
  const [reopenTarget, setReopenTarget] = useState<Score | null>(null);
  const [reopenLoading, setReopenLoading] = useState(false);
  const [reopenError, setReopenError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!orgSlug || !runId) return;
    setLoading(true);
    setError(null);
    try {
      const [subsData, teamsData, stationsData, scoresData] = await Promise.all([
        listSubmissions(orgSlug, runId),
        listTeams(orgSlug, runId),
        listRunStations(orgSlug, runId),
        listScores(orgSlug, runId),
      ]);

      const challengeResults = await Promise.all(
        stationsData.items.map((station) =>
          listRunChallenges(orgSlug, runId, station.id),
        ),
      );
      const allChallenges = challengeResults.flatMap((r) => r.items);

      setSubmissions(subsData.items);
      setTeams(teamsData.items);
      setChallenges(allChallenges);
      setScores(scoresData.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  // The tasks are a frozen snapshot from the run's start. Answers, grades and
  // teams change, so a refresh reads those three and nothing else. It is
  // quiet: no spinner, and a score typed into a row stays, because the rows
  // are keyed by the answer. A failed refresh keeps the shown state and tries
  // again on the next tick.
  const refresh = useCallback(async () => {
    if (!orgSlug || !runId) return;
    try {
      const [subsData, teamsData, scoresData] = await Promise.all([
        listSubmissions(orgSlug, runId),
        listTeams(orgSlug, runId),
        listScores(orgSlug, runId),
      ]);
      setSubmissions(subsData.items);
      setTeams(teamsData.items);
      setScores(scoresData.items);
    } catch {
      // Next tick.
    }
  }, [orgSlug, runId]);

  useAutoRefresh(refresh, GRADING_REFRESH_MS);

  const teamMap = useMemo(
    () => new Map(teams.map((t) => [t.id, t])),
    [teams],
  );

  const challengeMap = useMemo(
    () => new Map(challenges.map((c) => [c.id, c])),
    [challenges],
  );

  const scoredSet = useMemo(
    () => new Set(scores.map((s) => s.submission_id)),
    [scores],
  );

  const challengeTitle = (challengeId: string) => {
    const c = challengeMap.get(challengeId);
    if (!c) return challengeId;
    return localizedText(c.title, activeLang) || challengeId;
  };

  // Group submissions by run_challenge_id
  const grouped = useMemo(() => {
    const map = new Map<string, Submission[]>();
    for (const sub of submissions) {
      const arr = map.get(sub.run_challenge_id) ?? [];
      arr.push(sub);
      map.set(sub.run_challenge_id, arr);
    }
    return map;
  }, [submissions]);

  const filteredGroups = useMemo(() => {
    const entries = [...grouped.entries()];
    return entries
      .filter(([cid]) => challengeFilter === 'all' || cid === challengeFilter)
      .map(([cid, subs]) => {
        const filtered = subs.filter((s) => {
          if (scoreFilter === 'scored') return scoredSet.has(s.id);
          if (scoreFilter === 'unscored') return !scoredSet.has(s.id);
          return true;
        });
        return [cid, filtered] as [string, Submission[]];
      })
      .filter(([, subs]) => subs.length > 0);
  }, [grouped, scoreFilter, challengeFilter, scoredSet]);

  const handleScored = (score: Score) => {
    setScores((prev) => [...prev, score]);
  };

  const handleReopen = async () => {
    if (!reopenTarget || !runId) return;
    setReopenLoading(true);
    setReopenError(null);
    try {
      await deleteScore(orgSlug, runId, reopenTarget.id);
      // Drop the score locally so the ScoringRow reappears for the submission.
      setScores((prev) => prev.filter((s) => s.id !== reopenTarget.id));
      setReopenTarget(null);
    } catch (err) {
      setReopenError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setReopenLoading(false);
    }
  };

  if (loading) return <LoadingSpinner />;
  if (error) return <ErrorAlert message={error} onRetry={load} />;

  return (
    <Box>
      <BackLink to={`/runs/${runId}`} label={t('submissions.backToRun')} />
      <Typography variant="h2" component="h1" gutterBottom>
        {t('submissions.title')}
      </Typography>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ mb: 3 }}>
        <ToggleButtonGroup
          value={scoreFilter}
          exclusive
          onChange={(_, next: ScoreFilter | null) => next && setScoreFilter(next)}
          size="small"
          aria-label={t('submissions.filter.stateLabel')}
        >
          <ToggleButton value="all">{t('submissions.filter.all')}</ToggleButton>
          <ToggleButton value="scored">{t('submissions.filter.scored')}</ToggleButton>
          <ToggleButton value="unscored">{t('submissions.filter.unscored')}</ToggleButton>
        </ToggleButtonGroup>

        <FormControl size="small" sx={{ minWidth: 180 }}>
          <InputLabel id="submission-challenge-filter-label">
            {t('submissions.filter.challengeLabel')}
          </InputLabel>
          <Select
            labelId="submission-challenge-filter-label"
            label={t('submissions.filter.challengeLabel')}
            value={challengeFilter}
            onChange={(e) => setChallengeFilter(e.target.value)}
          >
            <MenuItem value="all">{t('submissions.filter.all')}</MenuItem>
            {[...grouped.keys()].map((cid) => (
              <MenuItem key={cid} value={cid}>
                {challengeTitle(cid)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>
      {reopenError && <ErrorAlert message={reopenError} />}
      {filteredGroups.length === 0 && (
        <Typography sx={{
          color: "text.secondary"
        }}>
          {/* Under a filter, an empty list says that nothing matches, not
              that nothing was submitted. */}
          {submissions.length > 0 ? t('submissions.emptyFiltered') : t('submissions.empty')}
        </Typography>
      )}
      {filteredGroups.map(([challengeId, subs]) => {
        const groupChallenge = challengeMap.get(challengeId);
        const instructions = resolveLocalized(groupChallenge?.instructions, activeLang);
        // MUI renders the heading slot as an h3, which would skip a level under
        // the page's h1. Here it is an h2 with the `all: unset` MUI gives its
        // own, so the look stays. The title is a span in the subtitle1 look,
        // because Typography's subtitle1 element is an h6, which would put a
        // stray h6 under the h1. MUI wires the
        // region's aria-labelledby to the summary's id, so that id and
        // aria-controls name the accordion's region.
        return (
          <Accordion
            key={challengeId}
            defaultExpanded
            slots={{ heading: 'h2' }}
            sx={{ '& .MuiAccordion-heading': { all: 'unset' } }}
          >
            <AccordionSummary
              id={`submissions-challenge-${challengeId}`}
              aria-controls={`submissions-challenge-${challengeId}-region`}
              expandIcon={<ExpandMoreIcon />}
            >
              <Typography variant="subtitle1" component="span">{challengeTitle(challengeId)}</Typography>
            </AccordionSummary>
            <AccordionDetails>
              {instructions.text && (
                <Box
                  sx={{
                    mb: 2,
                    p: 1.5,
                    bgcolor: 'grey.50',
                    border: 1,
                    borderColor: 'divider',
                    borderRadius: 1,
                  }}
                >
                  <Typography
                    variant="caption"
                    sx={{ color: 'text.secondary', fontWeight: 600, display: 'block' }}
                  >
                    {t('submissions.taskLabel')}
                  </Typography>
                  <SafeHtml html={instructions.text} dir={dirAttr(instructions.locale)} />
                </Box>
              )}
              {groupChallenge?.suggested_solution && (
                <Box
                  sx={{
                    mb: 2,
                    p: 1.5,
                    bgcolor: 'grey.50',
                    border: 1,
                    borderColor: 'divider',
                    borderRadius: 1,
                  }}
                >
                  <Typography
                    variant="caption"
                    sx={{ color: 'text.secondary', fontWeight: 600, display: 'block' }}
                  >
                    {t('submissions.solutionReference')}
                  </Typography>
                  {/* The editor stores rich text. A legacy plain text keeps
                      its line breaks and gets its links. */}
                  <SafeHtml html={groupChallenge.suggested_solution} sx={{ typography: 'body2' }} />
                </Box>
              )}
              <Stack spacing={2} divider={<Box sx={{ borderTop: 1, borderColor: 'divider' }} />}>
                {subs.map((sub) => {
                  const team = teamMap.get(sub.team_id);
                  const challenge = challengeMap.get(sub.run_challenge_id);
                  const maxPts = challenge?.scoring_rules?.max_points ?? 10;
                  const isScored = scoredSet.has(sub.id);
                  const existingScore = scores.find((s) => s.submission_id === sub.id);

                  return (
                    <Box key={sub.id}>
                      <Stack
                        direction="row"
                        sx={{
                          justifyContent: "space-between",
                          alignItems: "center"
                        }}>
                        {/* subtitle2 defaults to an h6 element (MUI's mapping);
                            under the challenge group's h2 the row title is an
                            h3. The variant keeps the look. */}
                        <Typography variant="subtitle2" component="h3">
                          {team?.name ?? sub.team_id}
                        </Typography>
                        {isScored && (
                          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                            {/* "3 / 5" alone does not say "scored", so the
                                tick carries the full name and the digits are
                                hidden from assistive technology. */}
                            <AnswerStateChip
                              state="scored"
                              nameOnIcon
                              points={existingScore?.points ?? 0}
                              maxPoints={maxPts}
                              label={`${existingScore?.points ?? 0} / ${maxPts}`}
                            />
                            {/* MUI's `warning.main` (#ED6C02) misses 4.5:1 on
                                white, so the text action carries the darker
                                warning tone. */}
                            <Button
                              size="small"
                              color="warning"
                              sx={{ color: 'warning.dark' }}
                              onClick={() => existingScore && setReopenTarget(existingScore)}
                            >
                              {t('submissions.reopenButton')}
                            </Button>
                          </Stack>
                        )}
                        {/* An unscored row carries its state at the same
                            place. The word is the one of the filter above. */}
                        {!isScored && (
                          <AnswerStateChip
                            state="submitted"
                            label={t('submissions.filter.unscored')}
                          />
                        )}
                      </Stack>
                      {isScored && existingScore?.comment && (
                        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                          {t('submissions.commentLabel')}: {existingScore.comment}
                        </Typography>
                      )}
                      {/* Content rendering */}
                      {typeof sub.content.text === 'string' && (
                        <Box
                          sx={{
                            mt: 1,
                            p: 1,
                            bgcolor: 'grey.100',
                            borderRadius: 1,
                            whiteSpace: 'pre-wrap',
                          }}
                        >
                          <Typography variant="body2">{sub.content.text}</Typography>
                        </Box>
                      )}
                      {/* A purged medium: the object is gone and the
                          reference nulled, while the row and its score stay.
                          Say so instead of loading a medium. */}
                      {sub.content.media_removed === true && (
                        <Typography
                          variant="body2"
                          sx={{
                            mt: 1,
                            color: 'text.secondary',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 0.75,
                          }}
                        >
                          <AnswerStateIcon state="mediaRemoved" decorative fontSize="small" />
                          {t('submissions.mediaRemoved')}
                        </Typography>
                      )}
                      {/* Photo submissions. PhotoChallengeForm writes
                          `photo_url`, also for a creative task of an older run.
                          The list image loads the thumb variant and the new-tab
                          full view the display variant, so grading never pulls
                          the original. */}
                      {typeof sub.content.photo_url === 'string' && (
                        <Box sx={{ mt: 1 }}>
                          <a
                            href={getFileUrl(sub.content.photo_url, 'display')}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            <img
                              src={getFileUrl(sub.content.photo_url, 'thumb')}
                              alt={t('submissions.photoAlt')}
                              style={{ maxWidth: 200, maxHeight: 150, objectFit: 'cover' }}
                            />
                          </a>
                        </Box>
                      )}
                      {/* Creative submissions may carry an audio clip. */}
                      {typeof sub.content.audio_url === 'string' && (
                        <Box sx={{ mt: 1 }}>
                          <Box
                            component="audio"
                            controls
                            aria-label={t('submissions.audioLabel', {
                              team: team?.name ?? sub.team_id,
                              task: challengeTitle(sub.run_challenge_id),
                            })}
                            src={getFileUrl(sub.content.audio_url)}
                            sx={{ width: 280 }}
                          />
                        </Box>
                      )}
                      {typeof sub.content.video_url === 'string' && (
                        <Box sx={{ mt: 1 }}>
                          <Box
                            component="video"
                            controls
                            aria-label={t('submissions.videoLabel', {
                              team: team?.name ?? sub.team_id,
                              task: challengeTitle(sub.run_challenge_id),
                            })}
                            src={getFileUrl(sub.content.video_url)}
                            sx={{ maxWidth: 280, maxHeight: 200, borderRadius: 1 }}
                          />
                        </Box>
                      )}
                      {/* Quiz submissions store the chosen option text in
                          `selected_option`, QR submissions the payload in
                          `scanned_value`. There is no `option` key. */}
                      {typeof sub.content.selected_option === 'string' && (
                        <Typography variant="body2" sx={{ mt: 1 }}>
                          {t('submissions.optionLabel', { value: sub.content.selected_option })}
                        </Typography>
                      )}
                      {typeof sub.content.scanned_value === 'string' && (
                        <Typography variant="body2" sx={{ mt: 1, wordBreak: 'break-all' }}>
                          {t('submissions.scannedLabel', { value: sub.content.scanned_value })}
                        </Typography>
                      )}
                      {!isScored && (
                        <ScoringRow
                          orgSlug={orgSlug}
                          runId={runId ?? ''}
                          submission={sub}
                          maxPoints={maxPts}
                          onScored={handleScored}
                        />
                      )}
                    </Box>
                  );
                })}
              </Stack>
            </AccordionDetails>
          </Accordion>
        );
      })}
      <ConfirmDialog
        open={reopenTarget !== null}
        title={t('submissions.reopenTitle')}
        message={t('submissions.reopenMessage')}
        confirmLabel={t('submissions.reopenConfirm')}
        destructive
        loading={reopenLoading}
        onConfirm={() => void handleReopen()}
        onCancel={() => setReopenTarget(null)}
      />
    </Box>
  );
};

export default SubmissionReview;
