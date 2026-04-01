import React, { useState } from 'react';
import { Link as RouterLink } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Alert, Box, Button, CircularProgress, Link, Typography } from '@mui/material';
import OnboardingShell from '../components/OnboardingShell';
import OnboardingBrandHeader from '../components/OnboardingBrandHeader';
import ClavisTextField from '../components/ClavisTextField';
import { requestPasswordReset } from '../api/auth';
import { ApiError } from '../api/client';
import {
  authFormSx,
  authHeadingSx,
  authLabelSx,
  authLinkSx,
  authPrimaryButtonSx,
  authSubtitleSx,
} from '../styles/authChrome';

const ForgotPassword: React.FC = () => {
  const { t } = useTranslation('auth');
  const [email, setEmail] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsSubmitting(true);
    try {
      await requestPasswordReset({ email });
      setSubmitted(true);
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setSubmitted(true);
      } else {
        setError(t('login.unexpectedError'));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <OnboardingShell>
      <OnboardingBrandHeader />
      <Box component="form" onSubmit={handleSubmit} noValidate sx={authFormSx}>
        <Typography component="h1" sx={authHeadingSx}>
          {t('forgotPassword.title')}
        </Typography>
        <Typography sx={authSubtitleSx}>{t('forgotPassword.subtitle')}</Typography>

        {submitted ? (
          <Alert severity="success">{t('forgotPassword.successMessage')}</Alert>
        ) : (
          <>
            <Typography component="label" htmlFor="forgot-email" sx={authLabelSx}>
              {t('forgotPassword.emailLabel')}
            </Typography>
            <ClavisTextField
              id="forgot-email"
              fullWidth
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              disabled={isSubmitting}
              size="small"
              placeholder={t('forgotPassword.emailLabel')}
              clearAriaLabel={t('login.clearField')}
              error={Boolean(error)}
              helperText={error || ' '}
            />
            <Button
              type="submit"
              variant="contained"
              disableElevation
              disabled={isSubmitting}
              sx={authPrimaryButtonSx}
            >
              {isSubmitting ? (
                <CircularProgress size={20} sx={{ color: '#FFFFFF' }} />
              ) : (
                t('forgotPassword.submitButton')
              )}
            </Button>
          </>
        )}

        <Link
          component={RouterLink}
          to="/login"
          underline="none"
          sx={{ ...authLinkSx, marginBlockStart: '16px' }}
        >
          {t('forgotPassword.backToLogin')}
        </Link>
      </Box>
    </OnboardingShell>
  );
};

export default ForgotPassword;
