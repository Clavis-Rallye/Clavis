import React, { useRef, useState } from 'react';
import { Box, Button, Stack, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

import { removeStationTitleImage, setStationTitleImage } from '../api/content';
import { ApiError } from '../api/client';
import { imageRefusalKey } from '../api/media';
import type { Station } from '../api/types';
import ErrorAlert from './ErrorAlert';

interface StationImageFieldProps {
  orgSlug: string;
  stationId: string;
  imageUrl: string | null;
  /** Called with the updated station after a successful upload/remove. */
  onChange: (station: Station) => void;
}

const ACCEPT = 'image/png,image/jpeg,image/webp';

/**
 * Creator control for a station's title image. Uploads/replaces/removes
 * via the title-image endpoints; the image is persisted immediately (it is not
 * tied to the surrounding form's Save).
 */
const StationImageField: React.FC<StationImageFieldProps> = ({
  orgSlug,
  stationId,
  imageUrl,
  onChange,
}) => {
  const { t } = useTranslation('content');
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // Reset so picking the same file again still fires onChange.
    if (inputRef.current) inputRef.current.value = '';
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      onChange(await setStationTitleImage(orgSlug, stationId, file));
    } catch (err) {
      // A coded refusal (no readable picture, too large) gets its translated
      // reason. The server's own text is an English literal.
      const refusal = imageRefusalKey(err);
      if (refusal) setError(t(refusal));
      else setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async () => {
    setBusy(true);
    setError(null);
    try {
      onChange(await removeStationTitleImage(orgSlug, stationId));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box>
      <Typography variant="subtitle2" component="div" sx={{ mb: 0.5 }}>
        {t('station.image.label')}
      </Typography>
      {imageUrl ? (
        <Box
          component="img"
          src={imageUrl}
          alt={t('station.image.previewAlt')}
          sx={{
            display: 'block',
            width: '100%',
            maxWidth: 280,
            maxHeight: 160,
            objectFit: 'cover',
            borderRadius: 1,
            mb: 1,
          }}
        />
      ) : (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {t('station.image.none')}
        </Typography>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={handleFile}
        data-testid="station-image-input"
      />
      <Stack direction="row" spacing={1}>
        <Button
          variant="outlined"
          size="small"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          {imageUrl ? t('station.image.replace') : t('station.image.upload')}
        </Button>
        {imageUrl && (
          <Button color="error" size="small" disabled={busy} onClick={handleRemove}>
            {t('station.image.remove')}
          </Button>
        )}
      </Stack>
      {error && <ErrorAlert message={error} />}
    </Box>
  );
};

export default StationImageField;
