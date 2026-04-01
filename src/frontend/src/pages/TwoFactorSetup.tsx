import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Divider,
  FormControlLabel,
  List,
  ListItem,
  ListItemText,
  Typography,
} from '@mui/material';
import { QRCodeSVG } from 'qrcode.react';
import OnboardingShell from '../components/OnboardingShell';
import OnboardingBrandHeader from '../components/OnboardingBrandHeader';
import ClavisTextField from '../components/ClavisTextField';
import apiClient, { ApiError } from '../api/client';
import type { TotpSetupResponse, TotpVerifyResponse } from '../api/types';
import { useAuth } from '../auth/useAuth';
import {
  authFormSx,
  authHeadingSx,
  authLabelSx,
  authPrimaryButtonSx,
  authSubtitleSx,
} from '../styles/authChrome';

type Step = 'loading' | 'scan' | 'recovery' | 'done';

const SETUP_TOKEN_KEY = 'clavis_2fa_setup_token';

function getSetupToken(): string | null {
  return sessionStorage.getItem(SETUP_TOKEN_KEY);
}

function clearSetupToken(): void {
  sessionStorage.removeItem(SETUP_TOKEN_KEY);
}

const TwoFactorSetup: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  // `from` may arrive as a string (legacy callers) or as a Location-like object
  // `{ pathname }` (Login → /2fa-setup forwards location.state from EntryChoice).
  const returnTo = (() => {
    const from = (location.state as { from?: string | { pathname?: string } } | null)?.from;
    if (typeof from === 'string') return from;
    if (from && typeof from === 'object' && typeof from.pathname === 'string') return from.pathname;
    return '/dashboard';
  })();
  const { checkAuth } = useAuth();
  const { t } = useTranslation('auth');
  const [searchParams] = useSearchParams();
  // Set when the API client bounced a restricted-token 403 back here.
  // The user needs to know WHY they landed on the setup screen again.
  const redirectedHere = searchParams.get('reason') === 'required';

  const [step, setStep] = useState<Step>('loading');
  const [setup, setSetup] = useState<TotpSetupResponse | null>(null);
  const [code, setCode] = useState('');
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const bearerConfig = () => {
    const token = getSetupToken();
    return token ? { headers: { Authorization: `Bearer ${token}` } } : {};
  };

  useEffect(() => {
    let cancelled = false;
    const start = async () => {
      try {
        const { data } = await apiClient.post<TotpSetupResponse>(
          '/api/auth/2fa/totp/setup',
          undefined,
          bearerConfig(),
        );
        if (!cancelled) {
          setSetup(data);
          setStep('scan');
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError) {
          setError(err.detail);
        } else {
          setError(t('login.unexpectedError'));
        }
        setStep('scan');
      }
    };
    void start();
    return () => {
      cancelled = true;
    };
  }, [t]);

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!/^\d{6}$/.test(code)) {
      setError(t('twoFactor.verify.codeLabel'));
      return;
    }
    setError('');
    setIsSubmitting(true);
    try {
      const { data } = await apiClient.post<TotpVerifyResponse>(
        '/api/auth/2fa/totp/verify',
        { code },
        bearerConfig(),
      );
      clearSetupToken();
      if (data.status === 'success') {
        await checkAuth();
      }
      setStep('recovery');
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : t('login.unexpectedError'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleFinish = async () => {
    await checkAuth();
    navigate(returnTo, { replace: true });
  };

  return (
    <OnboardingShell>
      <OnboardingBrandHeader />
      <Box sx={authFormSx}>
        <Typography component="h1" sx={authHeadingSx}>
          {t('twoFactor.setup.title')}
        </Typography>

        {redirectedHere && (
          <Alert severity="info" sx={{ mb: 1 }}>
            {t('twoFactor.setup.requiredNotice')}
          </Alert>
        )}

        {step === 'loading' && (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress />
          </Box>
        )}

        {step === 'scan' && setup && (
          <Box component="form" onSubmit={handleVerify} sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <Typography sx={authSubtitleSx}>{t('twoFactor.setup.scanInstruction')}</Typography>
            <Box sx={{ display: 'flex', justifyContent: 'center', my: 2 }}>
              <QRCodeSVG value={setup.provisioning_uri} size={200} title={t('twoFactor.setup.qrTitle')} />
            </Box>
            <Typography
              variant="caption"
              align="center"
              sx={{ display: 'block', color: 'text.secondary', mb: 2 }}
            >
              {t('twoFactor.setup.manualSecret', { secret: setup.secret })}
            </Typography>

            <Typography component="label" htmlFor="twofa-setup-code" sx={authLabelSx}>
              {t('twoFactor.setup.codeLabel')}
            </Typography>
            <ClavisTextField
              id="twofa-setup-code"
              fullWidth
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              required
              autoComplete="one-time-code"
              size="small"
              placeholder={t('twoFactor.setup.codeLabel')}
              clearAriaLabel={t('login.clearField')}
              slotProps={{ htmlInput: { inputMode: 'numeric', pattern: '[0-9]{6}' } }}
              disabled={isSubmitting}
            />

            {error && <Alert severity="error">{error}</Alert>}

            <Button
              type="submit"
              variant="contained"
              disableElevation
              disabled={isSubmitting || code.length !== 6}
              sx={authPrimaryButtonSx}
            >
              {isSubmitting ? (
                <CircularProgress size={20} sx={{ color: '#FFFFFF' }} />
              ) : (
                t('twoFactor.setup.verifyButton')
              )}
            </Button>
          </Box>
        )}

        {step === 'recovery' && setup && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <Typography variant="h6" component="h2">
              {t('twoFactor.setup.recoveryTitle')}
            </Typography>
            <Alert severity="warning" sx={{ mb: 1 }}>
              {t('twoFactor.setup.recoveryInstruction')}
            </Alert>
            <List dense sx={{ bgcolor: 'background.paper', border: 1, borderColor: 'divider', borderRadius: 1 }}>
              {setup.recovery_codes.map((c) => (
                <ListItem key={c}>
                  <ListItemText
                    primary={c}
                    slotProps={{ primary: { sx: { fontFamily: 'monospace', textAlign: 'center' } } }}
                  />
                </ListItem>
              ))}
            </List>
            <Divider sx={{ my: 1 }} />
            <FormControlLabel
              control={<Checkbox checked={saved} onChange={(e) => setSaved(e.target.checked)} />}
              label={t('twoFactor.setup.recoveryConfirm')}
            />
            <Button
              variant="contained"
              disableElevation
              disabled={!saved}
              onClick={handleFinish}
              sx={authPrimaryButtonSx}
            >
              {t('twoFactor.setup.finishButton')}
            </Button>
          </Box>
        )}
      </Box>
    </OnboardingShell>
  );
};

export default TwoFactorSetup;
