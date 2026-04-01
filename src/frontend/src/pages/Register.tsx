import React, { useState } from 'react';
import { useNavigate, Link as RouterLink, Navigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Alert, Box, Button, CircularProgress, Link, Typography } from '@mui/material';
import OnboardingShell from '../components/OnboardingShell';
import OnboardingBrandHeader from '../components/OnboardingBrandHeader';
import ClavisTextField from '../components/ClavisTextField';
import { register as registerApi } from '../api/auth';
import { useAuth } from '../auth/useAuth';
import { useInstanceConfig } from '../config/instanceConfig';
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

const SLUG_RE = /^[a-z0-9-]+$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// The sessionStorage key TwoFactorSetup and AuthContext read for the
// restricted 2FA-setup bearer token (each defines its own copy of it).
const SETUP_TOKEN_KEY = 'clavis_2fa_setup_token';

const Register: React.FC = () => {
  const navigate = useNavigate();
  const { checkAuth } = useAuth();
  const { t } = useTranslation('auth');

  const [orgName, setOrgName] = useState('');
  const [orgSlug, setOrgSlug] = useState('');
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(false);

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!orgName.trim()) errors.orgName = t('register.errors.required');
    if (!orgSlug.trim()) errors.orgSlug = t('register.errors.required');
    else if (!SLUG_RE.test(orgSlug)) errors.orgSlug = t('register.errors.slugInvalid');
    if (!email.trim()) errors.email = t('register.errors.required');
    else if (!EMAIL_RE.test(email)) errors.email = t('register.errors.emailInvalid');
    if (!displayName.trim()) errors.displayName = t('register.errors.required');
    if (!password) errors.password = t('register.errors.required');
    else if (!validatePasswordPolicy(password)) errors.password = t('register.errors.passwordPolicy');
    if (password !== confirmPassword) errors.confirmPassword = t('register.errors.passwordMismatch');
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!validate()) return;
    setIsLoading(true);
    try {
      const result = await registerApi({
        organization_name: orgName,
        organization_slug: orgSlug,
        email,
        display_name: displayName,
        password,
      });
      if (result.requires_2fa && result.setup_token) {
        // Bootstrap with TOTP enforcement on: the API issued no session, only
        // the restricted setup token. Park it where TwoFactorSetup reads it
        // and go straight to enrollment — checkAuth would only hit /auth/me,
        // which refuses this token (2FA_SETUP_REQUIRED).
        sessionStorage.setItem(SETUP_TOKEN_KEY, result.setup_token);
        navigate('/2fa-setup', { replace: true });
        return;
      }
      await checkAuth();
      navigate('/dashboard', { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError(t('register.errors.unexpectedError'));
      }
      setIsLoading(false);
    }
  };

  return (
    <OnboardingShell>
      <OnboardingBrandHeader />
      <Box component="form" onSubmit={handleSubmit} noValidate sx={authFormSx}>
        <Typography component="h1" sx={authHeadingSx}>
          {t('register.title')}
        </Typography>
        <Typography sx={authSubtitleSx}>{t('register.subtitle')}</Typography>

        <Typography component="label" htmlFor="register-org-name" sx={authLabelSx}>
          {t('register.orgNameLabel')}
        </Typography>
        <ClavisTextField
          id="register-org-name"
          fullWidth
          value={orgName}
          onChange={(e) => setOrgName(e.target.value)}
          required
          disabled={isLoading}
          size="small"
          placeholder={t('register.orgNameLabel')}
          clearAriaLabel={t('login.clearField')}
          error={!!fieldErrors.orgName}
          helperText={fieldErrors.orgName}
          slotProps={{ formHelperText: { role: 'alert' } }}
        />

        <Typography
          component="label"
          htmlFor="register-org-slug"
          sx={{ ...authLabelSx, marginBlockStart: '8px' }}
        >
          {t('register.orgSlugLabel')}
        </Typography>
        <ClavisTextField
          id="register-org-slug"
          fullWidth
          value={orgSlug}
          onChange={(e) => setOrgSlug(e.target.value.toLowerCase())}
          required
          disabled={isLoading}
          size="small"
          placeholder={t('register.orgSlugLabel')}
          clearAriaLabel={t('login.clearField')}
          error={!!fieldErrors.orgSlug}
          helperText={fieldErrors.orgSlug ?? t('register.orgSlugHelper')}
          slotProps={{ formHelperText: { role: fieldErrors.orgSlug ? 'alert' : undefined } }}
        />

        <Typography
          component="label"
          htmlFor="register-email"
          sx={{ ...authLabelSx, marginBlockStart: '8px' }}
        >
          {t('register.emailLabel')}
        </Typography>
        <ClavisTextField
          id="register-email"
          fullWidth
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
          disabled={isLoading}
          size="small"
          placeholder={t('register.emailLabel')}
          clearAriaLabel={t('login.clearField')}
          error={!!fieldErrors.email}
          helperText={fieldErrors.email}
          slotProps={{ formHelperText: { role: 'alert' } }}
        />

        <Typography
          component="label"
          htmlFor="register-display-name"
          sx={{ ...authLabelSx, marginBlockStart: '8px' }}
        >
          {t('register.displayNameLabel')}
        </Typography>
        <ClavisTextField
          id="register-display-name"
          fullWidth
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          required
          autoComplete="name"
          disabled={isLoading}
          size="small"
          placeholder={t('register.displayNameLabel')}
          clearAriaLabel={t('login.clearField')}
          error={!!fieldErrors.displayName}
          helperText={fieldErrors.displayName}
          slotProps={{ formHelperText: { role: 'alert' } }}
        />

        <Typography
          component="label"
          htmlFor="register-password"
          sx={{ ...authLabelSx, marginBlockStart: '8px' }}
        >
          {t('register.passwordLabel')}
        </Typography>
        <ClavisTextField
          id="register-password"
          fullWidth
          type="password"
          passwordToggle
          showPasswordAriaLabel={t('login.showPassword')}
          hidePasswordAriaLabel={t('login.hidePassword')}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete="new-password"
          disabled={isLoading}
          size="small"
          placeholder={t('register.passwordLabel')}
          error={!!fieldErrors.password}
          helperText={fieldErrors.password}
          slotProps={{ formHelperText: { role: 'alert' } }}
        />

        <Typography
          component="label"
          htmlFor="register-confirm-password"
          sx={{ ...authLabelSx, marginBlockStart: '8px' }}
        >
          {t('register.confirmPasswordLabel')}
        </Typography>
        <ClavisTextField
          id="register-confirm-password"
          fullWidth
          type="password"
          passwordToggle
          showPasswordAriaLabel={t('login.showPassword')}
          hidePasswordAriaLabel={t('login.hidePassword')}
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          required
          autoComplete="new-password"
          disabled={isLoading}
          size="small"
          placeholder={t('register.confirmPasswordLabel')}
          error={!!fieldErrors.confirmPassword}
          helperText={fieldErrors.confirmPassword}
          slotProps={{ formHelperText: { role: 'alert' } }}
        />

        {error && <Alert severity="error">{error}</Alert>}

        <Button
          type="submit"
          variant="contained"
          disableElevation
          disabled={isLoading}
          sx={authPrimaryButtonSx}
        >
          {isLoading ? <CircularProgress size={20} sx={{ color: '#FFFFFF' }} /> : t('register.submitButton')}
        </Button>

        <Link
          component={RouterLink}
          to="/login"
          underline="none"
          sx={{ ...authLinkSx, marginBlockStart: '16px' }}
        >
          {t('register.loginLink')}
        </Link>
      </Box>
    </OnboardingShell>
  );
};

export default Register;

/**
 * `/register/organization` is the one-time bootstrap of
 * the first organization. Once an organization exists (and the operator
 * has not opted into open signups) the route behaves as if it did not
 * exist: the login page instead of a dead form. The API keeps answering
 * 403 ORG_BOOTSTRAP_CLOSED; this gate only avoids rendering the form. The
 * boot config fails closed (`bootstrap_open: false` on any error), so an
 * unreachable config endpoint never re-opens the bootstrap.
 *
 * The gate is the route element, not part of `Register` itself, so the
 * form component stays renderable in isolation (a11y and keyboard suites
 * exercise the bare form).
 */
export const OrgBootstrapRoute: React.FC = () => {
  const { bootstrap_open } = useInstanceConfig();
  if (!bootstrap_open) {
    return <Navigate to="/login" replace />;
  }
  return <Register />;
};
