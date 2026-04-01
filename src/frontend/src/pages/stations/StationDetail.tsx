import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link as RouterLink, useLocation, useNavigate, useParams, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { localizedText } from '../../i18n/contentLocales';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  Stack,
  Typography,
  Snackbar,
} from '@mui/material';
import DeleteIcon from '@mui/icons-material/Delete';
import { deleteStation, duplicateStation, getStation, updateStation } from '../../api/content';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { MultilingualString, Station, StationTips } from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import { cleanTips, tipsProblem } from '../../utils/stationTips';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import MultilingualField, { type MultilingualFieldHandle } from '../../components/MultilingualField';
import StationImageField from '../../components/StationImageField';
import StationTipsField, { type StationTipsFieldHandle } from '../../components/StationTipsField';
import BackLink from '../../components/BackLink';
import MapPicker from '../../components/MapPicker';
import { apiPathUrl } from '../../api/files';
import { imageRefusalKeyForCode } from '../../api/media';

/** What the notice at the foot of the page can say, as keys of the content namespace. */
const NOTICE_KEYS = {
  saved: 'saved',
  created: 'station.detail.created',
  imageSaved: 'station.image.saved',
} as const;
type Notice = keyof typeof NOTICE_KEYS;

const StationDetail: React.FC = () => {
  const { stationId } = useParams<{ stationId: string }>();
  const { user } = useAuth();
  const { t, i18n } = useTranslation('content');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const location = useLocation();
  // Set by StationCreate: the station is new, or it was saved but its title
  // image was not.
  const createState = location.state as {
    created?: boolean;
    titleImageFailed?: boolean;
    titleImageRefusal?: unknown;
  } | null;
  const justCreated = createState?.created === true;
  const titleImageFailed = createState?.titleImageFailed === true;
  // Why the server refused the image, when it said so by code.
  const titleImageRefusalKey = imageRefusalKeyForCode(createState?.titleImageRefusal);
  // Present when this station was opened from a rallye (RallyeDetail's edit
  // icon, or the composition create flow). It carries the way back.
  const rallyeId = searchParams.get('rallyeId');
  const backTo = rallyeId ? `/rallyes/${rallyeId}` : '/stations';

  const [station, setStation] = useState<Station | null>(null);
  const [title, setTitle] = useState<MultilingualString>({});
  const [description, setDescription] = useState<MultilingualString>({});
  const [locationLat, setLocationLat] = useState<number | null>(null);
  const [locationLng, setLocationLng] = useState<number | null>(null);
  const [address, setAddress] = useState('');
  const [locationPending, setLocationPending] = useState(false);
  const [tips, setTips] = useState<StationTips | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Revealed on an incomplete save click. The field itself stays untouched
  // until then.
  const [showErrors, setShowErrors] = useState(false);
  const titleRef = useRef<MultilingualFieldHandle>(null);
  // Set by a create or save click that the tips refused.
  const [tipsRefused, setTipsRefused] = useState(false);
  const tipsRef = useRef<StationTipsFieldHandle>(null);

  const orgSlug = user?.organization_slug;
  // One notice for a save, a creation and a stored title image.
  const [notice, setNotice] = useState<Notice>(justCreated ? 'created' : 'saved');
  const [noticeOpen, setNoticeOpen] = useState(justCreated);
  const showNotice = (next: Notice) => {
    setNotice(next);
    setNoticeOpen(true);
  };

  // The notice is shown once. Without this a reload of the page would say
  // "created" again, the browser keeps the state of a history entry.
  useEffect(() => {
    if (!justCreated) return;
    navigate({ pathname: location.pathname, search: location.search }, { replace: true, state: null });
  }, [justCreated, navigate, location.pathname, location.search]);
  // The back link asks before it drops unsaved changes.
  const [leaveOpen, setLeaveOpen] = useState(false);
  const [duplicating, setDuplicating] = useState(false);
  // The copy button asks first, like the rallye. With the keyboard it is the
  // first stop after the heading, and one Enter too many would make three
  // copies of a real station.
  const [duplicateOpen, setDuplicateOpen] = useState(false);

  const handleDuplicate = async () => {
    // `duplicating` also guards a second Enter while the request is out.
    if (!orgSlug || !stationId || duplicating) return;
    setDuplicating(true);
    try {
      const copy = await duplicateStation(orgSlug, stationId);
      navigate(`/stations/${copy.id}`);
    } catch {
      setError(t('library.duplicateFailed'));
    } finally {
      setDuplicating(false);
      setDuplicateOpen(false);
    }
  };

  const activeLang = i18n.language.startsWith('de') ? 'de' : 'en';
  const titleOf = (ml: MultilingualString) => localizedText(ml, activeLang);

  const load = useCallback(async () => {
    if (!orgSlug || !stationId) return;
    setLoading(true);
    setError(null);
    try {
      const s = await getStation(orgSlug, stationId);
      setStation(s);
      setTitle(s.title);
      setDescription(s.description);
      setLocationLat(s.location_lat);
      setLocationLng(s.location_lng);
      setAddress(s.address ?? '');
      setTips(s.tips);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, stationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const usageCount = station?.usage?.rallye_ids.length ?? 0;
  const isDirty =
    !!station &&
    (JSON.stringify(title) !== JSON.stringify(station.title) ||
      JSON.stringify(description) !== JSON.stringify(station.description) ||
      (station.address ?? '') !== address ||
      station.location_lat !== locationLat ||
      station.location_lng !== locationLng ||
      JSON.stringify(station.tips) !== JSON.stringify(tips));
  const deTitleMissing = !title.de?.trim();
  // An address without a map position would put the station at the wrong house.
  const coordinatesMissing = !!address.trim() && (locationLat === null || locationLng === null);
  const locationBlocked = locationPending || coordinatesMissing;

  const handleSave = async () => {
    if (!orgSlug || !stationId || !station || locationBlocked) return;
    if (deTitleMissing) {
      setShowErrors(true);
      titleRef.current?.focus();
      return;
    }
    // Half-filled tips: the tips section says what is missing, once, and the
    // field it names takes the mark and the focus. The server only says
    // "Ungültige Eingabe".
    if (tipsProblem(tips)) {
      setError(null);
      setTipsRefused(true);
      tipsRef.current?.focusProblem();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await updateStation(orgSlug, stationId, {
        title,
        description,
        address: address || null,
        location_lat: locationLat,
        location_lng: locationLng,
        // Always send tips (even null) — the backend treats explicit null as
        // "clear", while omitting the field would leave it unchanged.
        tips: cleanTips(tips),
      });
      setStation(updated);
      setAddress(updated.address ?? '');
      setTips(updated.tips);
      // Without a notice, "Speichern" gives no sign that it worked.
      showNotice('saved');
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!orgSlug || !stationId) return;
    setDeleting(true);
    try {
      await deleteStation(orgSlug, stationId);
      navigate('/stations');
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setDeleting(false);
      setDeleteOpen(false);
    }
  };

  if (loading) return <LoadingSpinner />;
  if (!station) {
    return (
      <Box>
        <ErrorAlert message={error ?? 'Station not found'} onRetry={load} />
      </Box>
    );
  }

  const rallyeNames = (station.usage?.rallye_titles ?? []).map((rt) => titleOf(rt)).filter(Boolean);
  // Parallel arrays (usage.rallye_ids[i] ↔ usage.rallye_titles[i]) — zipped here
  // so each usage chip can link to its rallye.
  const rallyeUsage = (station.usage?.rallye_ids ?? []).map((id, i) => ({
    id,
    title: titleOf(station.usage?.rallye_titles?.[i] ?? {}) || id,
  }));

  return (
    <Box>
      <BackLink
        to={backTo}
        label={rallyeId ? t('station.detail.backToRallye') : t('station.detail.backToList')}
        onClick={(event) => {
          if (!isDirty) return;
          event.preventDefault();
          setLeaveOpen(true);
        }}
      />
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{ justifyContent: 'space-between', alignItems: { xs: 'stretch', sm: 'center' }, mb: 3 }}
      >
        <Typography variant="h2" component="h1">
          {titleOf(station.title) || t('station.detail.title')}
        </Typography>
        <Stack direction="row" spacing={1}>
          {/* A complete copy to adapt for another rallye. */}
          <Button
            startIcon={<ContentCopyIcon />}
            variant="outlined"
            disabled={duplicating}
            onClick={() => setDuplicateOpen(true)}
          >
            {t('library.duplicate')}
          </Button>
          <Button
            startIcon={<DeleteIcon />}
            color="error"
            variant="outlined"
            onClick={() => setDeleteOpen(true)}
          >
            {t('station.detail.archive')}
          </Button>
        </Stack>
      </Stack>

      {error && <ErrorAlert message={error} />}
      {titleImageFailed && !station.title_image_url && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {t('station.detail.titleImageFailed')}
          {titleImageRefusalKey && <Box sx={{ mt: 0.5 }}>{t(titleImageRefusalKey)}</Box>}
        </Alert>
      )}

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h5" component="h2" gutterBottom>
            {t('station.detail.editTitle')}
          </Typography>
          {usageCount > 0 && (
            <Alert severity="info" sx={{ mb: 2 }}>
              <Stack spacing={1}>
                <Typography variant="body2">
                  {t('station.detail.usedInTitle', { count: usageCount })}
                  {usageCount > 1 ? `. ${t('station.detail.propagationNote')}` : '.'}
                </Typography>
                {rallyeUsage.length > 0 && (
                  <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1 }}>
                    {rallyeUsage.map((r) => (
                      <Chip
                        key={r.id}
                        component={RouterLink}
                        to={`/rallyes/${r.id}`}
                        clickable
                        label={r.title}
                        size="small"
                      />
                    ))}
                  </Stack>
                )}
              </Stack>
            </Alert>
          )}
          <Stack spacing={3}>
            <MultilingualField
              ref={titleRef}
              value={title}
              onChange={setTitle}
              label={t('station.create.titleLabel')}
              required
              showErrors={showErrors}
            />
            <MultilingualField
              value={description}
              onChange={setDescription}
              label={t('station.create.descriptionLabel')}
              richText
            />
            <Box>
              <Typography variant="subtitle2" component="div" sx={{ mb: 0.5 }}>
                {t('station.create.locationLabel')}
              </Typography>
              <MapPicker
                lat={locationLat}
                lng={locationLng}
                address={address}
                onChange={({ lat, lng, address: nextAddress }) => {
                  setLocationLat(lat);
                  setLocationLng(lng);
                  setAddress(nextAddress);
                }}
                disabled={saving}
                searchLabel={t('station.create.addressLabel')}
                onPendingChange={setLocationPending}
              />
              {/* MapPicker shows its own hint for a text that is not picked yet. */}
          {!locationPending && coordinatesMissing && (
                <Typography variant="caption" color="error" sx={{ display: 'block', mt: 1 }} role="alert">
                  {t('station.location.coordinatesRequired')}
                </Typography>
              )}
            </Box>
            <StationImageField
              orgSlug={orgSlug!}
              stationId={stationId!}
              imageUrl={station.title_image_url ? apiPathUrl(station.title_image_url) : station.title_image_url}
              onChange={(next) => {
                setStation(next);
                // The image is stored at once, not with "Speichern". Say so.
                if (next.title_image_url) showNotice('imageSaved');
              }}
            />
            <StationTipsField
              ref={tipsRef}
              value={tips}
              onChange={setTips}
              disabled={saving}
              showErrors={tipsRefused}
            />
            <Divider />
            <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button
                variant="contained"
                onClick={handleSave}
                disabled={!isDirty || saving || locationBlocked}
              >
                {/* "In allen Rallyes aktualisieren" would be wrong for a station
                    that one rallye uses: there is no "all". */}
                {usageCount > 1
                  ? t('station.detail.saveAcrossAll')
                  : t('station.detail.save')}
              </Button>
            </Box>
          </Stack>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={leaveOpen}
        title={t('unsaved.title')}
        message={t('unsaved.message')}
        confirmLabel={t('unsaved.leave')}
        cancelLabel={t('unsaved.stay')}
        destructive
        onConfirm={() => navigate(backTo)}
        onCancel={() => setLeaveOpen(false)}
      />
      <Snackbar
        // A new notice starts its own four seconds.
        key={notice}
        open={noticeOpen}
        autoHideDuration={4000}
        onClose={(_, reason) => {
          if (reason !== 'clickaway') setNoticeOpen(false);
        }}
        message={t(NOTICE_KEYS[notice])}
      />

      <ConfirmDialog
        open={duplicateOpen}
        title={t('library.duplicate')}
        message={t('library.duplicateStationConfirm')}
        confirmLabel={t('library.duplicate')}
        loading={duplicating}
        onConfirm={() => void handleDuplicate()}
        onCancel={() => setDuplicateOpen(false)}
      />

      <ConfirmDialog
        open={deleteOpen}
        title={t('station.detail.archive')}
        message={
          rallyeNames.length > 0
            ? t('station.detail.archiveInUseConfirm', {
                count: rallyeNames.length,
                rallyes: rallyeNames.join(', '),
              })
            : t('station.detail.archiveConfirm')
        }
        destructive
        loading={deleting}
        confirmLabel={t('archive.archive')}
        onConfirm={handleDelete}
        onCancel={() => setDeleteOpen(false)}
      />
    </Box>
  );
};

export default StationDetail;
