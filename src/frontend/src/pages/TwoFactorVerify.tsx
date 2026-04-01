import React, { useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Alert, Box, Button, CircularProgress, Link, Typography } from '@mui/material';
import OnboardingShell from '../components/OnboardingShell';
import OnboardingBrandHeader from '../components/OnboardingBrandHeader';
import ClavisTextField from '../components/ClavisTextField';
import { verify2faCode } from '../api/auth';
import { ApiError } from '../api/client';
import { useAuth } from '../auth/useAuth';
import { loginErrorMessage } from '../auth/loginErrors';
import {
  authFormSx,
  authHeadingSx,
  authLabelSx,
  authLinkSx,
  authPrimaryButtonSx,
  authSubtitleSx,
} from '../styles/authChrome';

const PENDING_EMAIL_KEY = 'clavis_2fa_pending_email';

const TwoFactorVerify: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { checkAuth } = useAuth();
  const { t, i18n } = useTranslation('auth');

  const [email] = useState(() => sessionStorage.getItem(PENDING_EMAIL_KEY) ?? '');
  const [useRecovery, setUseRecovery] = useState(false);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email) {
      setError(t('login.unexpectedError'));
      return;
    }
    setError('');
    setIsSubmitting(true);
    try {
      await verify2faCode({ email, code: code.trim() });
      sessionStorage.removeItem(PENDING_EMAIL_KEY);
      await checkAuth();
      const from =
        (location.state as { from?: { pathname?: string } } | null)?.from?.pathname || '/dashboard';
      navigate(from, { replace: true });
    } catch (err) {
      // A wrong code is an uncoded 401. Every other refusal reads like the
      // sign-in's, never as the API's English text.
      if (err instanceof ApiError && err.status === 401 && !err.errorCode) {
        setError(t(useRecovery ? 'twoFactor.verify.errors.invalidRecovery' : 'twoFactor.verify.errors.invalidCode'));
      } else {
        setError(loginErrorMessage(err, t, i18n.language));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const sanitize = (value: string) => {
    if (useRecovery) {
      return value.replace(/[^A-Za-z0-9]/g, '').toUpperCase().slice(0, 8);
    }
    return value.replace(/\D/g, '').slice(0, 6);
  };

  const codeLabel = useRecovery
    ? t('twoFactor.verify.recoveryLabel')
    : t('twoFactor.verify.codeLabel');

  return (
    <OnboardingShell>
      <OnboardingBrandHeader />
      <Box component="form" onSubmit={handleSubmit} sx={authFormSx}>
        <Typography component="h1" sx={authHeadingSx}>
          {t('twoFactor.verify.title')}
        </Typography>
        <Typography sx={authSubtitleSx}>{t('twoFactor.verify.subtitle')}</Typography>

        <Typography component="label" htmlFor="twofa-verify-code" sx={authLabelSx}>
          {codeLabel}
        </Typography>
        <ClavisTextField
          id="twofa-verify-code"
          fullWidth
          value={code}
          onChange={(e) => setCode(sanitize(e.target.value))}
          required
          autoFocus
          autoComplete="one-time-code"
          size="small"
          placeholder={codeLabel}
          clearAriaLabel={t('login.clearField')}
          slotProps={{
            htmlInput: {
              inputMode: useRecovery ? 'text' : 'numeric',
              pattern: useRecovery ? undefined : '[0-9]{6}',
            },
          }}
          disabled={isSubmitting}
        />

        {error && <Alert severity="error">{error}</Alert>}

        <Button
          type="submit"
          variant="contained"
          disableElevation
          disabled={isSubmitting || (useRecovery ? code.length !== 8 : code.length !== 6)}
          sx={authPrimaryButtonSx}
        >
          {isSubmitting ? (
            <CircularProgress size={20} sx={{ color: '#FFFFFF' }} />
          ) : (
            t('twoFactor.verify.submitButton')
          )}
        </Button>

        <Link
          component="button"
          type="button"
          underline="none"
          onClick={() => {
            setUseRecovery((v) => !v);
            setCode('');
          }}
          sx={{ ...authLinkSx, marginBlockStart: '16px', textAlign: 'start', cursor: 'pointer' }}
        >
          {useRecovery ? t('twoFactor.verify.codeToggle') : t('twoFactor.verify.recoveryToggle')}
        </Link>
      </Box>
    </OnboardingShell>
  );
};

export default TwoFactorVerify;
