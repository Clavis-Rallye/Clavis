import React, { useState } from 'react';
import { useSearchParams, useNavigate, Link as RouterLink } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Alert, Box, Button, CircularProgress, Link, Typography } from '@mui/material';
import OnboardingShell from '../components/OnboardingShell';
import OnboardingBrandHeader from '../components/OnboardingBrandHeader';
import ClavisTextField from '../components/ClavisTextField';
import { resetPassword } from '../api/auth';
import { ApiError } from '../api/client';
import {
  authFormSx,
  authHeadingSx,
  authLabelSx,
  authLinkSx,
  authPrimaryButtonSx,
  authSubtitleSx,
} from '../styles/authChrome';
import { validatePasswordPolicy } from '../utils/passwordPolicy';

const ResetPassword: React.FC = () => {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';

  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!validatePasswordPolicy(password)) errors.password = t('register.errors.passwordPolicy');
    if (password !== confirmPassword) errors.confirmPassword = t('register.errors.passwordMismatch');
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!token) {
      setError(t('resetPassword.missingToken'));
      return;
    }
    if (!validate()) return;
    setIsSubmitting(true);
    try {
      await resetPassword({ token, new_password: password });
      setSubmitted(true);
      setTimeout(() => navigate('/login', { replace: true }), 2000);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : t('login.unexpectedError'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <OnboardingShell>
      <OnboardingBrandHeader />
      <Box component="form" onSubmit={handleSubmit} noValidate sx={authFormSx}>
        <Typography component="h1" sx={authHeadingSx}>
          {t('resetPassword.title')}
        </Typography>
        <Typography sx={authSubtitleSx}>{t('resetPassword.subtitle')}</Typography>

        {!token && <Alert severity="error">{t('resetPassword.missingToken')}</Alert>}

        {submitted ? (
          <Alert severity="success">{t('resetPassword.successMessage')}</Alert>
        ) : (
          <>
            <Typography component="label" htmlFor="reset-password" sx={authLabelSx}>
              {t('resetPassword.newPasswordLabel')}
            </Typography>
            <ClavisTextField
              id="reset-password"
              fullWidth
              type="password"
              passwordToggle
              showPasswordAriaLabel={t('login.showPassword')}
              hidePasswordAriaLabel={t('login.hidePassword')}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
              disabled={isSubmitting || !token}
              error={!!fieldErrors.password}
              helperText={fieldErrors.password}
              size="small"
              placeholder={t('resetPassword.newPasswordLabel')}
              slotProps={{ formHelperText: { role: 'alert' } }}
            />

            <Typography
              component="label"
              htmlFor="reset-confirm"
              sx={{ ...authLabelSx, marginBlockStart: '8px' }}
            >
              {t('resetPassword.confirmPasswordLabel')}
            </Typography>
            <ClavisTextField
              id="reset-confirm"
              fullWidth
              type="password"
              passwordToggle
              showPasswordAriaLabel={t('login.showPassword')}
              hidePasswordAriaLabel={t('login.hidePassword')}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              autoComplete="new-password"
              disabled={isSubmitting || !token}
              error={!!fieldErrors.confirmPassword}
              helperText={fieldErrors.confirmPassword}
              size="small"
              placeholder={t('resetPassword.confirmPasswordLabel')}
              slotProps={{ formHelperText: { role: 'alert' } }}
            />

            {error && <Alert severity="error">{error}</Alert>}

            <Button
              type="submit"
              variant="contained"
              disableElevation
              disabled={isSubmitting || !token}
              sx={authPrimaryButtonSx}
            >
              {isSubmitting ? (
                <CircularProgress size={20} sx={{ color: '#FFFFFF' }} />
              ) : (
                t('resetPassword.submitButton')
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

export default ResetPassword;
