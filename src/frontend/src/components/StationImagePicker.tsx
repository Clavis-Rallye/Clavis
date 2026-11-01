import React, { useEffect, useMemo, useRef } from 'react';
import { Box, Button, Stack, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

interface StationImagePickerProps {
  file: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
}

const ACCEPT = 'image/png,image/jpeg,image/webp';

/**
 * Title image control for a station that does not exist yet.
 * It only holds the picked file. The create page uploads it after the station
 * is saved, because the title-image endpoint needs a station id.
 * StationImageField is the control for an existing station.
 */
const StationImagePicker: React.FC<StationImagePickerProps> = ({ file, onChange, disabled }) => {
  const { t } = useTranslation('content');
  const inputRef = useRef<HTMLInputElement>(null);
  const previewUrl = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);
  useEffect(
    () => () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    },
    [previewUrl],
  );

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0];
    // Reset so picking the same file again still fires onChange.
    if (inputRef.current) inputRef.current.value = '';
    if (picked) onChange(picked);
  };

  return (
    <Box>
      <Typography variant="subtitle2" component="div" sx={{ mb: 0.5 }}>
        {t('station.image.label')}
      </Typography>
      {previewUrl ? (
        <Box
          component="img"
          src={previewUrl}
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
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
        >
          {file ? t('station.image.replace') : t('station.image.upload')}
        </Button>
        {file && (
          <Button color="error" size="small" disabled={disabled} onClick={() => onChange(null)}>
            {t('station.image.remove')}
          </Button>
        )}
      </Stack>
    </Box>
  );
};

export default StationImagePicker;
