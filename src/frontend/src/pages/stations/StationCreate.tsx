import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Box, Button, Stack, Typography } from '@mui/material';
import { createRallyeStation, createStation, setStationTitleImage } from '../../api/content';
import { ApiError } from '../../api/client';
import { imageRefusalCode } from '../../api/media';
import { useAuth } from '../../auth/useAuth';
import type { MultilingualString, StationTips } from '../../api/types';
import { cleanTips, tipsProblem } from '../../utils/stationTips';
import ErrorAlert from '../../components/ErrorAlert';
import MultilingualField, { type MultilingualFieldHandle } from '../../components/MultilingualField';
import MapPicker from '../../components/MapPicker';
import { getGeocodeConfig, offersAddressSearch, type GeocodeConfig } from '../../utils/geocode';
import StationImagePicker from '../../components/StationImagePicker';
import StationTipsField, { type StationTipsFieldHandle } from '../../components/StationTipsField';
import BackLink from '../../components/BackLink';

const StationCreate: React.FC = () => {
  const { user } = useAuth();
  const { t } = useTranslation('content');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // When launched from the rallye composition flow, the new station is created
  // AND linked into the rallye in one call (createRallyeStation).
  const rallyeId = searchParams.get('rallyeId');

  const [title, setTitle] = useState<MultilingualString>({});
  const [description, setDescription] = useState<MultilingualString>({});
  const [locationLat, setLocationLat] = useState<number | null>(null);
  const [locationLng, setLocationLng] = useState<number | null>(null);
  const [address, setAddress] = useState('');
  const [tips, setTips] = useState<StationTips | null>(null);
  const [titleImage, setTitleImage] = useState<File | null>(null);
  const [locationPending, setLocationPending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Revealed on an incomplete create click. The field itself stays untouched
  // until then.
  const [showErrors, setShowErrors] = useState(false);
  const titleRef = useRef<MultilingualFieldHandle>(null);
  // The same answer MapPicker reads (asked once per page load): whether the
  // installation has an address search decides which help line fits.
  const [geocodeConfig, setGeocodeConfig] = useState<GeocodeConfig | null>(null);
  useEffect(() => {
    let alive = true;
    void getGeocodeConfig().then((next) => {
      if (alive) setGeocodeConfig(next);
    });
    return () => {
      alive = false;
    };
  }, []);

  // Set by a create or save click that the tips refused.
  const [tipsRefused, setTipsRefused] = useState(false);
  const tipsRef = useRef<StationTipsFieldHandle>(null);

  const orgSlug = user?.organization_slug;
  const deTitleMissing = !title.de?.trim();
  // An address without a map position would put the station at the wrong house.
  const coordinatesMissing = !!address.trim() && (locationLat === null || locationLng === null);
  const locationBlocked = locationPending || coordinatesMissing;
  const canSubmit = !!orgSlug && !locationBlocked && !submitting;

  const backTo = rallyeId ? `/rallyes/${rallyeId}` : '/stations';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!orgSlug || locationBlocked) return;
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
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        title,
        description,
        address: address || null,
        location_lat: locationLat,
        location_lng: locationLng,
        tips: cleanTips(tips),
      };
      const link = rallyeId ? await createRallyeStation(orgSlug, rallyeId, body) : null;
      const station = link ? link.station : await createStation(orgSlug, body);
      const stationPath = `/stations/${station.id}${rallyeId ? `?rallyeId=${rallyeId}` : ''}`;
      // The title-image endpoint needs a station id, so the image goes up in a
      // second step. From here on the station exists: a failed upload must not
      // leave the user on this form, where a second click would create a second
      // station. The station page has the image field and says what happened.
      if (titleImage) {
        try {
          await setStationTitleImage(orgSlug, station.id, titleImage);
        } catch (err) {
          // A coded refusal travels along, so the station page can say why.
          const titleImageRefusal = imageRefusalCode(err);
          navigate(stationPath, {
            state: { titleImageFailed: true, ...(titleImageRefusal && { titleImageRefusal }) },
          });
          return;
        }
      }
      if (link) {
        // Back into the rallye with the new station open, like a new task.
        navigate(`/rallyes/${rallyeId}?open=${link.link_id}`);
      } else {
        // The station page says that the station was created.
        navigate(stationPath, { state: { created: true } });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
      setSubmitting(false);
    }
  };

  return (
    <Box component="form" onSubmit={handleSubmit} noValidate>
      {/* The label names where the link goes. */}
      <BackLink
        to={backTo}
        label={rallyeId ? t('station.detail.backToRallye') : t('station.detail.backToList')}
      />
      <Typography variant="h2" component="h1" gutterBottom>
        {t('station.create.title')}
      </Typography>

      {error && <ErrorAlert message={error} />}

      <Stack spacing={3} sx={{ mt: 3 }}>
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
        <StationImagePicker file={titleImage} onChange={setTitleImage} disabled={submitting} />
        <Box>
          <Typography variant="subtitle2" component="div" sx={{ mb: 0.5 }}>
            {t('station.create.locationLabel')}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
            {t(
              offersAddressSearch(geocodeConfig)
                ? 'station.create.locationHint'
                : 'station.create.locationHintNoSearch',
            )}
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
            disabled={submitting}
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
        <StationTipsField
          ref={tipsRef}
          value={tips}
          onChange={setTips}
          disabled={submitting}
          showErrors={tipsRefused}
        />
        <Stack direction="row" spacing={2} sx={{ justifyContent: 'flex-end' }}>
          <Button variant="outlined" onClick={() => navigate(backTo)} disabled={submitting}>
            {t('actions.cancel', { ns: 'common' })}
          </Button>
          <Button type="submit" variant="contained" disabled={!canSubmit}>
            {t('station.create.submit')}
          </Button>
        </Stack>
      </Stack>
    </Box>
  );
};

export default StationCreate;
