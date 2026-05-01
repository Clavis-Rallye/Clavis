import React from 'react';
import { Box, Typography } from '@mui/material';
import WifiOffIcon from '@mui/icons-material/WifiOff';
import { useTranslation } from 'react-i18next';
import { useConnectivity } from '../hooks/useConnectivity';

const OfflineBanner: React.FC = () => {
  const { t } = useTranslation('runs');
  // Asks the server itself, not only `navigator.onLine`: a dead connection
  // at full signal showed no banner.
  const online = useConnectivity();

  if (online) return null;

  return (
    <Box
      role="status"
      aria-live="polite"
      sx={{
        bgcolor: 'secondary.light',
        color: 'text.primary',
        display: 'flex',
        alignItems: 'center',
        gap: 1.5,
        px: 2,
        py: 1.25,
        borderBottom: 1,
        borderColor: 'secondary.main',
      }}
    >
      <WifiOffIcon fontSize="small" />
      <Typography variant="body2" sx={{ fontWeight: 500 }}>
        {t('participant.offline.banner')}
      </Typography>
    </Box>
  );
};

export default OfflineBanner;
