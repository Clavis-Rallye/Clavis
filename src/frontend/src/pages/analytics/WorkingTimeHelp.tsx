import React from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, AlertTitle, Typography } from '@mui/material';

/**
 * The short definition of the working time, next to every view that shows it
 * It prints with the report, so a printed figure carries its meaning.
 */
const WorkingTimeHelp: React.FC = () => {
  const { t } = useTranslation('runs');
  return (
    <Alert severity="info" variant="outlined" className="print-card" data-testid="working-time-help">
      <AlertTitle>{t('analytics.workingTime.helpTitle')}</AlertTitle>
      <Typography variant="body2" sx={{ mb: 0.5 }}>
        {t('analytics.workingTime.helpTeam')}
      </Typography>
      <Typography variant="body2">{t('analytics.workingTime.helpTask')}</Typography>
    </Alert>
  );
};

export default WorkingTimeHelp;
