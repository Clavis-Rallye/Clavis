import React from 'react';
import { useTranslation } from 'react-i18next';
import { Typography } from '@mui/material';
import LegalShell from './LegalShell';

const Impressum: React.FC = () => {
  const { t } = useTranslation('legal');
  return (
    <LegalShell title={t('impressum.title')} slug="imprint">
      <Typography variant="body1">{t('impressum.intro')}</Typography>
      <Typography variant="h5" component="h2">
        {t('impressum.contactHeading')}
      </Typography>
      <Typography variant="body1" component="p" sx={{ whiteSpace: 'pre-line' }}>
        {t('impressum.contactBody')}
      </Typography>
      <Typography variant="h5" component="h2">
        {t('impressum.responsibleHeading')}
      </Typography>
      <Typography variant="body1" component="p" sx={{ whiteSpace: 'pre-line' }}>
        {t('impressum.responsibleBody')}
      </Typography>
    </LegalShell>
  );
};

export default Impressum;
