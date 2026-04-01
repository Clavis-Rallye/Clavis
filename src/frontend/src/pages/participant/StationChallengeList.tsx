import React, { useCallback, useEffect, useState } from 'react';
import SafeHtml from '../../components/SafeHtml';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { dirAttr, resolveLocalized } from '../../i18n/contentLocales';
import {
  Box,
  Button,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  Stack,
  Typography,
} from '@mui/material';
import LightbulbOutlinedIcon from '@mui/icons-material/LightbulbOutlined';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import { listRunChallenges, listRunStations, listSubmissions } from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import { REMOVED_FROM_RUN_PATH, isNotTeamMember } from './removedFromRun';
import type { MultilingualString, RunChallenge, RunStation, Submission } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import StationTipsDialog from '../../components/StationTipsDialog';
import BackLink from '../../components/BackLink';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import StationAddress from '../../components/StationAddress';
import { apiPathUrl } from '../../api/files';
import { AnswerStateIcon } from '../../components/AnswerState';
import { WAITING_TONAL_BG, WAITING_TONAL_TEXT, deriveAnswerState } from '../../utils/answerState';

/**
 * The address link as a touch target. As a block of its own it takes the whole
 * line height of its text, 24 px. An inline link is only as high as its glyphs
 * (WCAG 2.5.8).
 */
const ADDRESS_LINK_TARGET_SX = { '& a': { display: 'inline-block', minHeight: 24 } } as const;

/**
 * How often an open page re-reads the team's answers. A grading that the
 * coordinator gives or takes back sends no live signal. The standing follows
 * it by asking every ten seconds. This page asks at the same pace, so the two
 * never show a different state for longer than one round.
 */
const ANSWERS_REFRESH_MS = 10_000;

const StationChallengeList: React.FC = () => {
  const { runId, stationId } = useParams<{ runId: string; stationId: string }>();
  const { user } = useAuth();
  const { t, i18n } = useTranslation('runs');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // Restore the view (map or list) the participant came from. Without it,
  // "back" would always drop them onto the list, even from the map.
  const fromMap = searchParams.get('from') === 'map';
  const stationsBackTo = fromMap
    ? `/runs/${runId}/participant/stations?view=map`
    : `/runs/${runId}/participant/stations`;

  const [challenges, setChallenges] = useState<RunChallenge[]>([]);
  const [station, setStation] = useState<RunStation | null>(null);
  const [stationNumber, setStationNumber] = useState<number | null>(null);
  // Keyed by run_challenge_id. A bare set of ids would lose `is_scored` and the
  // points, and a graded answer would look like a merely submitted one.
  const [submissionsByChallenge, setSubmissionsByChallenge] = useState<Map<string, Submission>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tipsOpen, setTipsOpen] = useState(false);

  const orgSlug = user?.organization_slug;

  const applySubmissions = useCallback(
    (items: Submission[]) =>
      setSubmissionsByChallenge(new Map(items.map((s: Submission) => [s.run_challenge_id, s]))),
    [],
  );

  const load = useCallback(async () => {
    if (!orgSlug || !runId || !stationId) return;
    setLoading(true);
    setError(null);
    try {
      const [challengesResp, stationsResp, submissionsResp] = await Promise.all([
        listRunChallenges(orgSlug, runId, stationId),
        listRunStations(orgSlug, runId),
        listSubmissions(orgSlug, runId),
      ]);
      const sorted = [...challengesResp.items].sort((a, b) => a.sort_order - b.sort_order);
      setChallenges(sorted);
      const orderedStations = [...stationsResp.items].sort((a, b) => a.sort_order - b.sort_order);
      const idx = orderedStations.findIndex((s) => s.id === stationId);
      setStation(idx >= 0 ? orderedStations[idx] : null);
      setStationNumber(idx >= 0 ? idx + 1 : null);
      applySubmissions(submissionsResp.items);
    } catch (err) {
      if (isNotTeamMember(err)) {
        navigate(REMOVED_FROM_RUN_PATH, { replace: true });
        return;
      }
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, runId, stationId, applySubmissions]);

  // The station and its tasks are a frozen snapshot from the run's start, so
  // a refresh reads the answers only: one request instead of three.
  const refreshSubmissions = useCallback(async () => {
    if (!orgSlug || !runId) return;
    try {
      const submissionsResp = await listSubmissions(orgSlug, runId);
      setError(null);
      applySubmissions(submissionsResp.items);
    } catch (err) {
      if (isNotTeamMember(err)) {
        navigate(REMOVED_FROM_RUN_PATH, { replace: true });
        return;
      }
      setError(err instanceof ApiError ? err.detail : String(err));
    }
  }, [orgSlug, runId, applySubmissions]);

  useEffect(() => {
    void load();
  }, [load]);

  // A teammate's answer and a coordinator's grade both land here without this
  // screen being touched. Re-read them instead of showing the state of the
  // moment the station was opened.
  useAutoRefresh(refreshSubmissions, ANSWERS_REFRESH_MS);

  // The full UI language, not collapsed to de or en. The content locales of an
  // organization are free, such as ar or fa. A participant with an Arabic
  // browser gets Arabic content, right to left.
  const activeLang = i18n.language;
  const titleOf = (ml: MultilingualString) => resolveLocalized(ml, activeLang).text;
  const dirOf = (ml: MultilingualString) => dirAttr(resolveLocalized(ml, activeLang).locale);

  if (loading) return <LoadingSpinner />;

  return (
    <Stack spacing={2}>
      <BackLink
        to={stationsBackTo}
        label={t('participant.stationChallenges.backToStations')}
      />
      {station?.title_image_url && (
        <Box
          component="img"
          src={apiPathUrl(station.title_image_url)}
          alt={titleOf(station.title) || t('participant.stationChallenges.title')}
          sx={{
            width: '100%',
            maxHeight: 220,
            objectFit: 'cover',
            borderRadius: 2,
            display: 'block',
          }}
        />
      )}
      <Typography variant="h5" component="h1" dir={station ? dirOf(station.title) : undefined}>
        {station
          ? `${stationNumber != null ? `${stationNumber}. ` : ''}${
              titleOf(station.title) || t('participant.stationChallenges.title')
            }`
          : t('participant.stationChallenges.title')}
      </Typography>

      {station && titleOf(station.description) && (
        <SafeHtml html={titleOf(station.description)} dir={dirOf(station.description)} />
      )}

      {station?.address && (
        // An inline link is as high as its glyphs, about 18 px. As a block of
        // its own it takes the full line of 24 px, the least a touch target
        // may have (WCAG 2.5.8). The text keeps its look.
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

      {/* Shown only when the station has tips. The sentence says what the
          button holds. */}
      {station?.tips && (
        <Stack spacing={1} sx={{ alignItems: 'flex-start' }}>
          <Typography variant="body2" color="text.secondary">
            {t('participant.stationChallenges.tipsIntro')}
          </Typography>
          <Button
            variant="outlined"
            size="small"
            startIcon={<LightbulbOutlinedIcon />}
            onClick={() => setTipsOpen(true)}
            sx={{ minHeight: 44 }}
          >
            {t('participant.stationChallenges.tipsButton')}
          </Button>
        </Stack>
      )}

      {station?.tips && (
        <StationTipsDialog tips={station.tips} open={tipsOpen} onClose={() => setTipsOpen(false)} />
      )}

      {error && <ErrorAlert message={error} onRetry={load} />}

      {challenges.length === 0 && !error && (
        <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', mt: 4 }}>
          {t('participant.stationChallenges.empty')}
        </Typography>
      )}

      {challenges.map((challenge, idx) => {
        const submission = submissionsByChallenge.get(challenge.id);
        const state = deriveAnswerState(submission);
        const done = state !== 'open';
        const scored = state === 'scored';
        return (
          <Card key={challenge.id} variant={done ? 'outlined' : 'elevation'}>
            {/* The challenge view needs `?from=map` to lead a finished station
                back to the map. Without it here, the chain map, station,
                challenge would break one hop too early. */}
            <CardActionArea
              onClick={() =>
                navigate(
                  `/runs/${runId}/participant/stations/${stationId}/challenges/${challenge.id}${
                    fromMap ? '?from=map' : ''
                  }`,
                )
              }
            >
              <CardContent>
                <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
                  {/* One sign per state: the empty circle for an open task, the
                      blue hourglass for a submitted answer, the green tick for
                      a scored one. The chip names the submitted and the scored
                      state, so only the open circle carries a name of its own. */}
                  <AnswerStateIcon state={state} decorative={done} />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    {/* An h2 under the h1 of the station, so the cards are
                        navigable by heading without changing the look. */}
                    <Typography variant="subtitle1" component="h2" noWrap dir={dirOf(challenge.title)}>
                      {idx + 1}. {titleOf(challenge.title) || t('participant.challenge.untitled', { defaultValue: `Aufgabe ${idx + 1}` })}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" noWrap>
                      {t(`participant.challenge.types.${challenge.challenge_type}`, {
                        defaultValue: challenge.challenge_type,
                      })}
                    </Typography>
                    {/* The whole card is the button. The word says so, for
                        teams that read the cards as a list of facts. */}
                    <Stack
                      direction="row"
                      spacing={0.25}
                      sx={{ alignItems: 'center', color: 'primary.main', mt: 0.5 }}
                    >
                      <Typography variant="button" component="span" sx={{ lineHeight: 1.5 }}>
                        {t('participant.stationChallenges.openTask')}
                      </Typography>
                      <ChevronRightIcon fontSize="small" aria-hidden />
                    </Stack>
                  </Box>
                  {scored ? (
                    <Chip
                      label={t('participant.challenge.scoredWithPoints', {
                        points: submission?.points ?? 0,
                        maxPoints:
                          submission?.max_points ?? challenge.scoring_rules.max_points ?? 0,
                      })}
                      size="small"
                      color="success"
                    />
                  ) : done ? (
                    <Chip
                      label={t('participant.challenge.submitted')}
                      size="small"
                      sx={{ bgcolor: WAITING_TONAL_BG, color: WAITING_TONAL_TEXT }}
                    />
                  ) : (
                    <Chip
                      label={t('participant.challenge.points', {
                        points: challenge.scoring_rules.max_points ?? 0,
                      })}
                      size="small"
                      variant="outlined"
                    />
                  )}
                </Stack>
              </CardContent>
            </CardActionArea>
          </Card>
        );
      })}
    </Stack>
  );
};

export default StationChallengeList;
