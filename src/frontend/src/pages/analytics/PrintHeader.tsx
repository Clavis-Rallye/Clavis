import React from 'react';
import { Box, Divider, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../../auth/useAuth';
import { useOrg } from '../../contexts/OrgContext';

interface PrintHeaderProps {
  title: string;
}

const PrintHeader: React.FC<PrintHeaderProps> = ({ title }) => {
  const { t, i18n } = useTranslation('runs');
  const { user } = useAuth();
  const { name: organizationDisplayName } = useOrg();
  const organizationName = organizationDisplayName ?? user?.organization_slug ?? '';
  const generatedDate = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date());

  return (
    <Box className="print-only" component="header" style={{ display: 'none' }} sx={{ mb: 3 }}>
      <Typography variant="overline" component="p" sx={{ color: 'text.secondary' }}>
        {organizationName}
      </Typography>
      {/* A paragraph in the h4 look: as an h1 it would be a second level-1
          heading with height 0 on screen, next to the page's own h1. The block
          is print-only, so in print the report still opens with this title. */}
      <Typography variant="h4" component="p" sx={{ mb: 0.5 }}>
        {title}
      </Typography>
      <Typography variant="body2" color="text.secondary">
        {t('analytics.print.generated', { date: generatedDate })}
      </Typography>
      <Divider sx={{ mt: 2 }} />
    </Box>
  );
};

export default PrintHeader;
