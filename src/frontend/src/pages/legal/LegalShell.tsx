import React, { useContext, useEffect, useMemo, useState } from 'react';
import { Link as RouterLink, useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Alert, Box, Button, Card, CardContent, Container, Stack, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import { useAuth } from '../../auth/useAuth';
import { WizardStateContext } from '../../wizard/WizardStateContext';
import { fetchLegalText, type LegalKind } from '../../services/publicLegal';
import { useInstanceConfig } from '../../config/instanceConfig';
import SafeHtml from '../../components/SafeHtml';
import LegalLinks from '../../components/LegalLinks';
import ParticipantBottomNav from '../../components/ParticipantBottomNav';
import { BOTTOM_NAV_HEIGHT, getLastRunId } from '../../components/participantNav';
import logoSmall from '../../assets/logos/logo-small.svg';

interface LegalShellProps {
  title: string;
  /** Which legal kind to look the best-known text up for. */
  slug?: LegalKind;
  children: React.ReactNode;
}

/**
 * The frame of the static legal pages: a centred card with the brand mark on
 * top and a prominent "Back" button. Reachable from the participant overflow
 * menu and the admin sidebar footer.
 *
 * Asks `GET /api/public/legal/{slug}` for the best-known organization context,
 * in this order: the signed-in user's organization, the join flow's wizard
 * state, then the instance's single organization. The returned text (the
 * organization's own, else the instance's own) replaces the bundled `children`
 * copy. The page shows the bundled copy without a notice while the lookup is
 * pending. Three outcomes stay apart:
 * - A text was found. It shows without a notice.
 * - The lookup settled with no text configured. The bundled copy shows with
 *   the placeholder notice.
 * - The request failed. The bundled copy shows with a load-failed notice,
 *   because a failed request never proves "nothing published".
 */
const LegalShell: React.FC<LegalShellProps> = ({ title, slug, children }) => {
  const { t } = useTranslation(['common', 'legal']);
  const navigate = useNavigate();
  const { user, isLoading: authLoading } = useAuth();
  const wizardOrgSlug = useContext(WizardStateContext)?.organizationSlug ?? null;
  const { brand_name: brand, single_org_slug: singleOrgSlug } = useInstanceConfig();
  const orgSlug = user?.organization_slug ?? wizardOrgSlug ?? singleOrgSlug ?? null;
  const [text, setText] = useState<string | null>(null);
  const [notice, setNotice] = useState<'placeholder' | 'loadFailed' | null>(null);

  useEffect(() => {
    // Wait for the auth lookup first: while it's pending we don't yet know
    // the signed-in user's organization, the highest-priority context.
    if (authLoading || !slug) return;
    let cancelled = false;
    setText(null);
    setNotice(null);
    void fetchLegalText(slug, orgSlug).then((result) => {
      if (cancelled) return;
      if (result === null) {
        // The request itself failed (network error, timeout). That differs
        // from a settled "no text configured" answer.
        setNotice('loadFailed');
        return;
      }
      setText(result.text);
      setNotice(result.source === 'none' ? 'placeholder' : null);
    });
    return () => {
      cancelled = true;
    };
  }, [slug, authLoading, orgSlug]);

  // A notice shows only once the lookup has settled.
  const showPlaceholder = useMemo(
    () => !authLoading && !!slug && notice === 'placeholder',
    [authLoading, slug, notice],
  );
  const showLoadFailed = useMemo(
    () => !authLoading && !!slug && notice === 'loadFailed',
    [authLoading, slug, notice],
  );

  // The participant navigation belongs to someone who plays: a guest or a
  // participant with a run to go back to. Without this condition it would
  // stand under every legal page, for a visitor with three dead buttons and for
  // staff with "Mitteilungen, Rallye, Team".
  const lastRunId = getLastRunId();
  const showParticipantNav =
    !!lastRunId && (user?.role === 'guest' || user?.role === 'participant');

  // A visitor without a session who opened the page from a link has no
  // history and no dashboard, so the way back is the start page.
  const goBack = () => {
    if (window.history.length > 1) navigate(-1);
    else navigate(user ? '/dashboard' : '/');
  };

  return (
    <Box
      sx={{
        bgcolor: 'background.default',
        minHeight: '100dvh',
        pt: 4,
        pb: showParticipantNav ? `${BOTTOM_NAV_HEIGHT + 32}px` : 4,
      }}
    >
      <Container maxWidth="md">
        <Box component="header" sx={{ mb: 3 }}>
          <Stack
            direction="row"
            spacing={1}
            sx={{ alignItems: 'center' }}
            component={RouterLink}
            to="/"
            style={{ textDecoration: 'none', color: 'inherit' }}
          >
            <Box component="img" src={logoSmall} alt={brand} sx={{ width: 32, height: 28 }} />
            <Typography variant="h6" component="span">
              {brand}
            </Typography>
          </Stack>
        </Box>

        {/* main lets the route focus land on the heading. */}
        <Card component="main">
          <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
            <Typography variant="h2" component="h1" gutterBottom>
              {title}
            </Typography>
            {showPlaceholder && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                {t('legal:placeholderNotice')}
              </Alert>
            )}
            {showLoadFailed && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                {t('legal:loadFailedNotice')}
              </Alert>
            )}
            <Box sx={{ '& p': { mb: 2 }, '& h2': { mt: 3, mb: 1 } }}>
              {text ? <SafeHtml html={text} /> : children}
            </Box>
            <Box sx={{ mt: 4 }}>
              <Button variant="contained" startIcon={<ArrowBackIcon />} onClick={goBack}>
                {t('actions.back')}
              </Button>
            </Box>
          </CardContent>
        </Card>
        <LegalLinks />
      </Container>
      {showParticipantNav && <ParticipantBottomNav runId={lastRunId} active={false} />}
    </Box>
  );
};

export default LegalShell;
