import React from 'react';
import { Box, CircularProgress, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

interface LoadingSpinnerProps {
  message?: string;
  minHeight?: number | string;
}

// The progress bar has a name, so a screen reader hears "Wird geladen …"
// instead of an unnamed progress bar on every library, station page and
// analytics view.
const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({ message, minHeight = 200 }) => {
  const { t } = useTranslation('common');
  return (
  <Box
    role="status"
    aria-live="polite"
    sx={{
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      gap: 2,
      minHeight: minHeight
    }}>
    <CircularProgress aria-label={message ?? t('loading')} />
    {message && (
      <Typography variant="body2" sx={{
        color: "text.secondary"
      }}>
        {message}
      </Typography>
    )}
  </Box>
  );
};

export default LoadingSpinner;
