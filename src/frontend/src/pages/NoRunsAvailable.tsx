import React, { useState } from 'react';
import { Link as RouterLink, useNavigate, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Container,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import { useInstanceConfig } from '../config/instanceConfig';
import LegalLinks from '../components/LegalLinks';
import OperatorNotice from '../components/OperatorNotice';
import logoSmall from '../assets/logos/logo-small.svg';

/**
 * Landing for a participant who registered on a single-organization instance
 * without a join code. Onboarding skipped the organization picker, and a
 * codeless registration has no run to land in. Runs are entered by the join
 * code or link a coordinator hands out. Participants cannot list runs (the run
 * list answers them 403), so this page must NOT claim that no run exists. It
 * explains how to join and offers the code field, which navigates to
 * /join/<code> in upper case, because the lookup compares codes exactly. The
 * frame follows LegalShell (brand wordmark, one card).
 */
const NoRunsAvailable: React.FC = () => {
  const { t } = useTranslation('onboarding');
  const { brand_name: brand } = useInstanceConfig();
  const navigate = useNavigate();
  // Set by the participant pages when a coordinator removed this person from
  // their team.
  const [searchParams] = useSearchParams();
  const removed = searchParams.get('reason') === 'removed';
  const [code, setCode] = useState('');

  const joinCode = code.trim().toUpperCase();
  const join = () => {
    if (joinCode) navigate(`/join/${joinCode}`);
  };

  return (
    <Box sx={{ bgcolor: 'background.default', minHeight: '100dvh', py: 4 }}>
      <Container maxWidth="md">
        {/* The header keeps the logo link inside a landmark, as in PublicResults.
            The margin sits on the header, so the landmark does not change the
            spacing. */}
        <Box component="header" sx={{ mb: 3 }}>
          <Stack
            direction="row"
            spacing={1}
            component={RouterLink}
            to="/"
            sx={{ alignItems: 'center', textDecoration: 'none', color: 'inherit' }}
          >
            {/* Decorative. The wordmark next to it carries the name, and an alt
                text that repeats it fails axe's image-redundant-alt rule. */}
            <Box component="img" src={logoSmall} alt="" sx={{ width: 32, height: 28 }} />
            <Typography variant="h6" component="span">
              {brand}
            </Typography>
          </Stack>
        </Box>

        {/* main lets the route focus land on the heading. */}
        <Card component="main">
          <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
            <Typography variant="h2" component="h1" gutterBottom>
              {t('noRuns.title')}
            </Typography>
            {removed && (
              <Alert severity="info" sx={{ mb: 2 }}>
                {t('noRuns.removed')}
              </Alert>
            )}
            <Typography variant="body1" color="text.secondary" sx={{ mb: 4 }}>
              {t('noRuns.body')}
            </Typography>
            <TextField
              label={t('noRuns.codeLabel')}
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter') join();
              }}
              fullWidth
              sx={{ mb: 2, maxWidth: 360 }}
            />
            <Box sx={{ display: 'flex', gap: 2, alignItems: 'center', flexWrap: 'wrap' }}>
              <Button
                variant="contained"
                onClick={join}
                disabled={!joinCode}
              >
                {t('noRuns.submit')}
              </Button>
              <Button variant="text" component={RouterLink} to="/">
                {t('noRuns.back')}
              </Button>
            </Box>
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

export default NoRunsAvailable;
