import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { excerpt } from '../../utils/richtext';
import { Link as RouterLink, useNavigate, useParams, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { dirAttr, resolveLocalized } from '../../i18n/contentLocales';
import {
  Avatar,
  Box,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  LinearProgress,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import ListIcon from '@mui/icons-material/List';
import MapIcon from '@mui/icons-material/Map';
import { MapContainer, TileLayer } from '../../components/Leaflet';
import 'leaflet/dist/leaflet.css';
import {
  getParticipantOverview,
} from '../../api/runs';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import { REMOVED_FROM_RUN_PATH, isNotTeamMember, isPlayingRole } from './removedFromRun';
import type { MultilingualString, RunChallenge, RunStation } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import StationAddress from '../../components/StationAddress';
import { useAutoRefresh } from '../../hooks/useAutoRefresh';
import { useAnswerStateName } from '../../hooks/useAnswerStateName';
import { AnswerStateChip, AnswerStateIcon } from '../../components/AnswerState';
import StationMarkers from './StationMarkers';
import MapControls from '../../components/MapControls';
import { getGeocodeConfig, type GeocodeConfig } from '../../utils/geocode';
import { tilesFromConfig } from '../../utils/mapTiles';

type ChallengesByStation = Map<string, RunChallenge[]>;
type SubmittedChallengeIds = Set<string>;

// One progress row per page, so a fixed id is enough to name the bar.
const PROGRESS_LABEL_ID = 'participant-stations-progress-label';

const StationList: React.FC = () => {
  const { runId } = useParams<{ runId: string }>();
  const { user } = useAuth();
  const { t, i18n } = useTranslation('runs');
  const { t: tCommon } = useTranslation('common');
  const navigate = useNavigate();
  const stateName = useAnswerStateName();
  const [searchParams, setSearchParams] = useSearchParams();

  const [stations, setStations] = useState<RunStation[]>([]);
  const [challengesByStation, setChallengesByStation] = useState<ChallengesByStation>(new Map());
  const [submittedIds, setSubmittedIds] = useState<SubmittedChallengeIds>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // The tiles of the map follow the `MAP_TILE_URL` of the installation.
  const [tileConfig, setTileConfig] = useState<GeocodeConfig | null>(null);
  // The mode lives in the URL and not only in local state. Navigating to a
  // station and back then restores the map or the list instead of always
  // resetting to the list.
  const mode: 'list' | 'map' = searchParams.get('view') === 'map' ? 'map' : 'list';
  const setMode = (next: 'list' | 'map') => {
    setSearchParams(next === 'map' ? { view: 'map' } : {}, { replace: true });
  };

  const orgSlug = user?.organization_slug;
  const load = useCallback(async ({ quiet = false }: { quiet?: boolean } = {}) => {
    if (!orgSlug || !runId) return;
    if (!quiet) setLoading(true);
    setError(null);
    try {
      // One request per refresh. Fetching the stations, then the tasks of each
      // station one by one, then the submissions saturated the server when
      // several phones refreshed together. The overview carries all three.
      const overview = await getParticipantOverview(orgSlug, runId);
      if (overview.is_team_member === false && isPlayingRole(user?.role)) {
        sessionStorage.removeItem(`clavis_team_for_run_${runId}`);
        navigate(REMOVED_FROM_RUN_PATH, { replace: true });
        return;
      }
      // The stations are sorted by sort_order, so the array index is the route number.
      const sorted = [...overview.stations].sort((a, b) => a.sort_order - b.sort_order);
      setStations(sorted);
      setSubmittedIds(new Set(overview.answered_challenge_ids));
      setChallengesByStation(
        new Map(
          sorted.map((s) => [s.id, [...s.challenges].sort((a, b) => a.sort_order - b.sort_order)] as const),
        ),
      );
    } catch (err) {
      if (isNotTeamMember(err)) {
        navigate(REMOVED_FROM_RUN_PATH, { replace: true });
        return;
      }
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, runId, user?.role, navigate]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let alive = true;
    void getGeocodeConfig().then((next) => {
      if (alive) setTileConfig(next);
    });
    return () => {
      alive = false;
    };
  }, []);

  useAutoRefresh(() => load({ quiet: true }));

  // The full UI language, not collapsed to de or en. The content locales of an
  // organization are free, such as ar or fa. A participant with an Arabic
  // browser gets Arabic content, right to left.
  const activeLang = i18n.language;
  const titleOf = (ml: MultilingualString) => resolveLocalized(ml, activeLang).text;
  const dirOf = (ml: MultilingualString) => dirAttr(resolveLocalized(ml, activeLang).locale);

  const progress = useMemo(() => {
    const perStation = stations.map((s) => {
      const chs = challengesByStation.get(s.id) ?? [];
      const done = chs.filter((c) => submittedIds.has(c.id)).length;
      const points = chs.reduce(
        (n, c) => n + (typeof c.scoring_rules?.max_points === 'number' ? c.scoring_rules.max_points : 0),
        0,
      );
      return { station: s, done, total: chs.length, points };
    });
    const totalDone = perStation.reduce((n, p) => n + p.done, 0);
    const totalTotal = perStation.reduce((n, p) => n + p.total, 0);
    return {
      perStation,
      totalDone,
      totalTotal,
      percent: totalTotal > 0 ? Math.round((totalDone / totalTotal) * 100) : 0,
    };
  }, [stations, challengesByStation, submittedIds]);

  const tiles = useMemo(() => tilesFromConfig(tileConfig), [tileConfig]);

  if (loading) return <LoadingSpinner />;

  const stationsWithCoords = progress.perStation.filter(
    ({ station }) =>
      typeof station.location_lat === 'number' && typeof station.location_lng === 'number',
  );
  // The map shows only with a station that has a position. It starts there,
  // and StationMarkers then fits it to all stations.
  const mapStart: [number, number] | null =
    stationsWithCoords.length > 0
      ? [stationsWithCoords[0].station.location_lat as number, stationsWithCoords[0].station.location_lng as number]
      : null;

  return (
    <Stack spacing={2}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
        <Typography variant="h2" component="h1">
          {t('participant.stations.title')}
        </Typography>
        {stations.length > 0 && (
          <Typography variant="body2" color="text.secondary">
            {t('participant.stations.stationCount', { count: stations.length })}
          </Typography>
        )}
      </Stack>

      {stations.length > 0 && (
        <ToggleButtonGroup
          value={mode}
          exclusive
          size="small"
          sx={{ '& .MuiToggleButton-root': { minHeight: 44 } }}
          onChange={(_, next) => next && setMode(next)}
          aria-label={t('participant.stations.viewToggle', { defaultValue: 'Ansicht' })}
        >
          {/* The visible text is the name. An English aria-label would
              override it with "list" and "map". */}
          <ToggleButton value="list">
            <ListIcon fontSize="small" sx={{ mr: 0.5 }} />
            {t('participant.stations.listView', { defaultValue: 'Liste' })}
          </ToggleButton>
          <ToggleButton value="map">
            <MapIcon fontSize="small" sx={{ mr: 0.5 }} />
            {t('participant.stations.mapView', { defaultValue: 'Karte' })}
          </ToggleButton>
        </ToggleButtonGroup>
      )}

      {error && <ErrorAlert message={error} onRetry={load} />}

      {stations.length > 0 && (
        <Box>
          {/* "1 / 12" and "8%" on their own say nothing about what is being
              counted. The row names it, and the bar borrows that label. */}
          <Stack
            direction="row"
            sx={{ justifyContent: 'space-between', alignItems: 'baseline', mb: 0.5, gap: 1 }}
          >
            <Typography id={PROGRESS_LABEL_ID} variant="body2" color="text.secondary">
              {t('participant.stations.progressLabel')}
            </Typography>
            <Stack direction="row" spacing={1}>
              <Typography variant="body2" color="text.secondary">
                {progress.totalDone} / {progress.totalTotal}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {progress.percent}%
              </Typography>
            </Stack>
          </Stack>
          <LinearProgress
            variant="determinate"
            value={progress.percent}
            aria-labelledby={PROGRESS_LABEL_ID}
            sx={{ height: 8, borderRadius: 4 }}
          />
        </Box>
      )}

      {stations.length === 0 && !error && (
        <Card sx={{ textAlign: 'center', py: 4 }}>
          <CardContent>
            <Typography variant="body1" color="text.secondary">
              {t('participant.stations.noStationsYet')}
            </Typography>
          </CardContent>
        </Card>
      )}

      {mode === 'map' && stations.length > 0 && (
        <Box
          sx={{
            height: 420,
            borderRadius: 2,
            overflow: 'hidden',
            border: 1,
            borderColor: 'divider',
          }}
        >
          {mapStart === null ? (
            <Box sx={{ p: 3, textAlign: 'center' }}>
              <Typography variant="body2" color="text.secondary">
                {t('participant.stations.noCoords', {
                  defaultValue: 'Für diese Rallye sind noch keine Standorte hinterlegt.',
                })}
              </Typography>
            </Box>
          ) : (
            <MapContainer
              center={mapStart}
              zoom={14}
              zoomControl={false}
              style={{ height: '100%', width: '100%' }}
              scrollWheelZoom
              name={tCommon('aria.stationMap')}
            >
              {/* The tiles follow the config of the installation. Until it has
                  answered, no tile is requested, and when it failed none is. */}
              {tiles && <TileLayer url={tiles.url} attribution={tiles.attribution} />}
              <MapControls />
              <StationMarkers
                doneName={stateName('stationDone')}
                onOpen={(id) => navigate(`/runs/${runId}/participant/stations/${id}?from=map`)}
                items={stationsWithCoords.map(({ station, done, total }) => {
                  const number = stations.findIndex((s) => s.id === station.id) + 1;
                  const title =
                    titleOf(station.title) || t('participant.stations.untitled', { defaultValue: 'Station' });
                  return {
                    id: station.id,
                    number,
                    lat: station.location_lat as number,
                    lng: station.location_lng as number,
                    allDone: total > 0 && done === total,
                    name: t('participant.stations.markerName', { number, title }),
                    popup: (
                      <>
                        <Typography variant="subtitle2" sx={{ mb: 0.5 }} dir={dirOf(station.title)}>
                          {number}. {title}
                        </Typography>
                        {total > 0 && (
                          <Typography variant="caption" color="text.secondary" component="div">
                            {t('participant.stations.tasksDone', { done, count: total })}
                          </Typography>
                        )}
                        {station.address && (
                          <StationAddress
                            address={station.address}
                            lat={station.location_lat}
                            lng={station.location_lng}
                            label={titleOf(station.title)}
                            dense
                          />
                        )}
                      </>
                    ),
                  };
                })}
              />
            </MapContainer>
          )}
        </Box>
      )}

      {mode === 'map' && stations.length > 0 && (
        <Stack direction="row" spacing={2} sx={{ alignItems: 'baseline', flexWrap: 'wrap' }}>
          {/* The terms of the tile server (FOSSGIS) ask for an operator contact
              within one click. The imprint link stands directly under the map,
              without opening a menu first. */}
          <Link component={RouterLink} to="/legal/impressum" variant="body2" color="text.secondary" underline="always">
            {tCommon('legalLinks.impressum')}
          </Link>
          {tileConfig?.unknown && (
            <Typography variant="body2" color="text.secondary">
              {t('participant.stations.tilesUnavailable', {
                defaultValue: 'Die Karte ist gerade nicht verfügbar.',
              })}
            </Typography>
          )}
        </Stack>
      )}

      {mode === 'map' && stationsWithCoords.length > 0 && stationsWithCoords.length < stations.length && (
        <Typography variant="body2" color="text.secondary">
          {t('participant.stations.withoutPosition', { count: stations.length - stationsWithCoords.length })}
        </Typography>
      )}

      {mode === 'list' && progress.perStation.map(({ station, done, total, points }, idx) => {
        const allDone = total > 0 && done === total;
        const description = excerpt(titleOf(station.description));
        return (
          <Card key={station.id} variant="outlined">
            <CardActionArea
              onClick={() => navigate(`/runs/${runId}/participant/stations/${station.id}`)}
              /* The card has no aria-label. Its name is the text it shows, in
                 the order it shows it: number, title, description, count of
                 tasks, points. A name that left out a visible word would hide
                 the card from a voice command. */
            >
              <CardContent sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                {/* A finished station keeps its number, like its marker on the
                    map, and carries the double tick as a badge. */}
                <Box sx={{ position: 'relative', flexShrink: 0 }}>
                  <Avatar
                    sx={{
                      width: 40,
                      height: 40,
                      // The same green as the chip and the map marker.
                      bgcolor: allDone ? 'success.main' : 'primary.main',
                      color: allDone ? 'success.contrastText' : 'primary.contrastText',
                      fontWeight: 700,
                    }}
                  >
                    {idx + 1}
                  </Avatar>
                  {allDone && (
                    <Box
                      data-testid="station-done-avatar-badge"
                      sx={{
                        position: 'absolute',
                        top: -6,
                        right: -8,
                        width: 22,
                        height: 22,
                        borderRadius: '50%',
                        bgcolor: 'success.main',
                        color: 'success.contrastText',
                        border: '2px solid',
                        borderColor: 'background.paper',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                      }}
                    >
                      <AnswerStateIcon state="stationDone" decorative inheritColor sx={{ fontSize: 14 }} />
                    </Box>
                  )}
                </Box>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="subtitle1" component="div" sx={{ fontWeight: 600 }} dir={dirOf(station.title)}>
                    {titleOf(station.title) || t('participant.stations.untitled', { defaultValue: `Station ${idx + 1}` })}
                  </Typography>
                  {description && (
                    <Typography
                      variant="body2"
                      color="text.secondary"
                      sx={{
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                        // Break between words only. `anywhere` and `break-word`
                        // split a long German compound mid-word, and the clamp
                        // then cuts it into an unreadable fragment.
                        overflowWrap: 'normal',
                        wordBreak: 'normal',
                      }}
                      dir={dirOf(station.description)}
                    >
                      {description}
                    </Typography>
                  )}
                  <Stack direction="row" spacing={1} sx={{ mt: 0.75, flexWrap: 'wrap', rowGap: 0.5 }}>
                    {total > 0 && allDone && (
                      <AnswerStateChip
                        state="stationDone"
                        label={t('participant.stations.tasksDone', { done, count: total })}
                      />
                    )}
                    {total > 0 && !allDone && (
                      <Chip
                        label={t('participant.stations.tasksDone', { done, count: total })}
                        size="small"
                        variant="outlined"
                      />
                    )}
                    {points > 0 && (
                      <Chip
                        label={t('participant.leaderboard.points', { points })}
                        size="small"
                        variant="outlined"
                      />
                    )}
                  </Stack>
                </Box>
                <ChevronRightIcon color="action" sx={{ flexShrink: 0 }} />
              </CardContent>
            </CardActionArea>
          </Card>
        );
      })}
    </Stack>
  );
};

export default StationList;
