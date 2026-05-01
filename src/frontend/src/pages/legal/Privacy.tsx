import React from 'react';
import { useTranslation } from 'react-i18next';
import { Typography } from '@mui/material';
import LegalShell from './LegalShell';

const Privacy: React.FC = () => {
  const { t } = useTranslation('legal');
  return (
    <LegalShell title={t('privacy.title')} slug="privacy">
      <Typography variant="body1">{t('privacy.intro')}</Typography>
      <Typography variant="h5" component="h2">
        {t('privacy.dataHeading')}
      </Typography>
      <Typography variant="body1" component="p" sx={{ whiteSpace: 'pre-line' }}>
        {t('privacy.dataBody')}
      </Typography>
      <Typography variant="h5" component="h2">
        {t('privacy.rightsHeading')}
      </Typography>
      <Typography variant="body1" component="p" sx={{ whiteSpace: 'pre-line' }}>
        {t('privacy.rightsBody')}
      </Typography>
    </LegalShell>
  );
};

export default Privacy;
