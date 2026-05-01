import React from 'react';
import { useTranslation } from 'react-i18next';
import { Typography } from '@mui/material';
import LegalShell from './LegalShell';

const Accessibility: React.FC = () => {
  const { t } = useTranslation('legal');
  return (
    <LegalShell title={t('accessibility.title')} slug="accessibility">
      <Typography variant="body1">{t('accessibility.intro')}</Typography>
      <Typography variant="h5" component="h2">
        {t('accessibility.statusHeading')}
      </Typography>
      <Typography variant="body1" component="p" sx={{ whiteSpace: 'pre-line' }}>
        {t('accessibility.statusBody')}
      </Typography>
      <Typography variant="h5" component="h2">
        {t('accessibility.contactHeading')}
      </Typography>
      <Typography variant="body1" component="p" sx={{ whiteSpace: 'pre-line' }}>
        {t('accessibility.contactBody')}
      </Typography>
    </LegalShell>
  );
};

export default Accessibility;
