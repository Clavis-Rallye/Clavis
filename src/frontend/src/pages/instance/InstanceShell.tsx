import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Card, CardContent, Container, Stack, Typography } from '@mui/material';
import type { ContainerProps } from '@mui/material';
import { Link as RouterLink, useLocation } from 'react-router';
import { useInstanceConfig } from '../../config/instanceConfig';
import LanguageSwitcher from '../../components/LanguageSwitcher';
import LegalLinks from '../../components/LegalLinks';
import OperatorNotice from '../../components/OperatorNotice';
import logoSmall from '../../assets/logos/logo-small.svg';

interface InstanceShellProps {
  title: string;
  /** Present only once an operator session exists — omitted on the login page. */
  onSignOut?: () => void;
  maxWidth?: ContainerProps['maxWidth'];
  children: React.ReactNode;
}

// The operator console's own navigation. Shown only once `onSignOut` proves a
// session exists (never on the login page). No organization navigation
// belongs here: the operator stands above every tenant, not inside one.
const NAV_ITEMS: { to: string; labelKey: string }[] = [
  { to: '/instance', labelKey: 'shell.nav.organizations' },
  { to: '/instance/legal', labelKey: 'shell.nav.legal' },
  { to: '/instance/audit', labelKey: 'shell.nav.audit' },
  { to: '/instance/account', labelKey: 'shell.nav.account' },
];

/** The frame's width once signed in: the widest page's, the organization list. */
const SHELL_WIDTH = 'xl' as const;

/**
 * Frame for the operator console: brand mark from the instance config, a
 * language switch and, once signed in, a sign-out button. Follows
 * `LegalShell.tsx`'s layout (a centred card under the brand mark) without its
 * per-organization legal-text lookup.
 */
const InstanceShell: React.FC<InstanceShellProps> = ({ title, onSignOut, maxWidth = 'md', children }) => {
  const { t } = useTranslation('instance');
  const { brand_name: brand } = useInstanceConfig();
  const location = useLocation();

  return (
    <Box sx={{ bgcolor: 'background.default', minHeight: '100dvh', py: 4 }}>
      {/* Signed in, the frame is one width on every page and only the card
          takes the page's width. Otherwise the header and "Abmelden" would
          move by about 280 px between "Organisationen" and "Konto". */}
      <Container maxWidth={onSignOut ? SHELL_WIDTH : maxWidth}>
        <Stack
          component="header"
          direction="row"
          spacing={2}
          sx={{ alignItems: 'center', justifyContent: 'space-between', mb: 3 }}
        >
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <Box component="img" src={logoSmall} alt="" sx={{ width: 32, height: 28 }} />
            <Typography variant="h6" component="span">
              {brand}
            </Typography>
          </Stack>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <LanguageSwitcher />
            {onSignOut && (
              <Button variant="outlined" size="small" onClick={onSignOut}>
                {t('shell.signOut')}
              </Button>
            )}
          </Stack>
        </Stack>

        {onSignOut && (
          <Box component="nav" aria-label={t('shell.navLabel')} sx={{ mb: 3 }}>
            <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
              {NAV_ITEMS.map((item) => {
                const active = location.pathname === item.to;
                return (
                  <Button
                    key={item.to}
                    component={RouterLink}
                    to={item.to}
                    variant={active ? 'contained' : 'text'}
                    size="small"
                    disableElevation
                    aria-current={active ? 'page' : undefined}
                  >
                    {t(item.labelKey)}
                  </Button>
                );
              })}
            </Stack>
          </Box>
        )}

        {/* main lets the route focus land on the heading. */}
        <Card
          component="main"
          sx={onSignOut && maxWidth ? { maxWidth: (theme) => theme.breakpoints.values[maxWidth] } : undefined}
        >
          <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
            <Typography variant="h2" component="h1" gutterBottom>
              {title}
            </Typography>
            {children}
          </CardContent>
        </Card>
        {/* The operator's notice, outside main like the legal links. The row
            of links has no side padding here, so the notice has none. */}
        <OperatorNotice />
        <LegalLinks />
      </Container>
    </Box>
  );
};

export default InstanceShell;
