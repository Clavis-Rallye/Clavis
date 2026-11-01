import React from 'react';
import { Link as RouterLink } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Box, Link } from '@mui/material';

const LINKS = [
  { to: '/legal/impressum', key: 'legalLinks.impressum' },
  { to: '/legal/privacy', key: 'legalLinks.privacy' },
  { to: '/legal/accessibility', key: 'legalLinks.accessibility' },
] as const;

interface LegalLinksProps {
  sx?: object;
}

/**
 * Imprint, privacy and accessibility statement as one quiet row of links.
 *
 * A public body must keep these three pages reachable from every entry page,
 * not only from the participant menu inside a run. The row sits outside
 * `main`, so the route focus still lands on the page heading.
 */
const LegalLinks: React.FC<LegalLinksProps> = ({ sx }) => {
  const { t } = useTranslation('common');
  return (
    <Box
      component="nav"
      aria-label={t('legalLinks.label')}
      sx={{
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'center',
        columnGap: 2,
        rowGap: 0.5,
        py: 2,
        ...sx,
      }}
    >
      {LINKS.map((link) => (
        <Link
          key={link.to}
          component={RouterLink}
          to={link.to}
          variant="body2"
          color="text.secondary"
          underline="always"
          // 44 px tall hit area on phones without changing the visual size.
          sx={{ display: 'inline-flex', alignItems: 'center', minHeight: 44 }}
        >
          {t(link.key)}
        </Link>
      ))}
      {/* The licence notices are a text file the build emits, not a route.
          A plain anchor opens the file itself. */}
      <Link
        href="/third-party-licenses.txt"
        variant="body2"
        color="text.secondary"
        underline="always"
        sx={{ display: 'inline-flex', alignItems: 'center', minHeight: 44 }}
      >
        {t('legalLinks.licenses')}
      </Link>
    </Box>
  );
};

export default LegalLinks;
