import React, { useCallback, useEffect, useState } from 'react';
import { Link as RouterLink, useNavigate, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Alert, Box, Button, CircularProgress, Link, Typography } from '@mui/material';
import OnboardingShell from '../components/OnboardingShell';
import ClavisTextField from '../components/ClavisTextField';
import { acceptInvitation, previewInvitation } from '../api/auth';
import { ApiError } from '../api/client';
import type { InvitationPreviewResponse } from '../api/types';
import {
  authFormSx,
  authHeadingSx,
  authLabelSx,
  authLinkSx,
  authPrimaryButtonSx,
  authSubtitleSx,
} from '../styles/authChrome';
import { validatePasswordPolicy } from '../utils/passwordPolicy';

/**
 * The invitee's landing page.
 *
 * The person arriving here has no account, so everything is unauthenticated:
 * the token in the URL is the only credential. The page first asks the backend
 * what the invitation offers, then asks for a name and a password.
 *
 * A dead link must say *why* it is dead. "Invitation not found" and "this was
 * already used" send the recipient down completely different paths, and a
 * single generic error turns both into a phone call to the administrator.
 */
const AcceptInvite: React.FC = () => {
  const { t, i18n } = useTranslation(['auth', 'common']);
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';

  const [invitation, setInvitation] = useState<InvitationPreviewResponse | null>(null);
  const [fetchError, setFetchError] = useState<string>('');
  // A link without a token never needs a request — derive that, don't fetch it.
  const [loading, setLoading] = useState(Boolean(token));
  const loadError = token ? fetchError : t('auth:acceptInvite.errors.missingToken');

  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [accepted, setAccepted] = useState(false);

  const describeLoadFailure = useCallback(
    (err: unknown): string => {
      if (err instanceof ApiError) {
        // An account made for the address in the meantime: the invitation can
        // never be accepted, and the page says what to do instead.
        if (err.errorCode === 'EMAIL_HAS_ACCOUNT') {
          return t('auth:acceptInvite.errors.emailHasAccount');
        }
        if (err.status === 400) return t('auth:register.errors.passwordPolicy');
        // The backend distinguishes the three end states on purpose.
        switch (err.detail) {
          case 'invitation_already_accepted':
            return t('auth:acceptInvite.errors.alreadyAccepted');
          case 'invitation_revoked':
            return t('auth:acceptInvite.errors.revoked');
          case 'invitation_expired':
            return t('auth:acceptInvite.errors.expired');
          case 'invitation_not_found':
            // Also a link whose invitation was sent again: renewal replaces
            // the token, so the old one is unknown.
            return t('auth:acceptInvite.errors.notFound');
          default:
            // Never the API's English text.
            return t('auth:login.unexpectedError');
        }
      }
      return t('auth:login.unexpectedError');
    },
    [t],
  );

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await previewInvitation(token);
        if (cancelled) return;
        setInvitation(data);
        setDisplayName(data.display_name_suggestion ?? '');
        // The invitation carries the language its mail was written in. Following
        // a German invite into an English UI is a jarring, avoidable handover.
        if (data.language && data.language !== i18n.resolvedLanguage) {
          void i18n.changeLanguage(data.language);
        }
      } catch (err) {
        if (!cancelled) setFetchError(describeLoadFailure(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, t, i18n, describeLoadFailure]);

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!displayName.trim()) errors.displayName = t('auth:register.errors.required');
    if (!validatePasswordPolicy(password)) errors.password = t('auth:register.errors.passwordPolicy');
    if (password !== confirmPassword) {
      errors.confirmPassword = t('auth:register.errors.passwordMismatch');
    }
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (!validate()) return;
    setSubmitting(true);
    try {
      await acceptInvitation(token, { display_name: displayName.trim(), password });
      setAccepted(true);
      // The sign-in page greets a new person and already holds the address.
      // Both travel in the navigation state, so the address never shows in
      // the URL, the history or a server log.
      const email = invitation?.email;
      setTimeout(
        () => navigate('/login', { replace: true, state: { newAccount: true, email } }),
        2500,
      );
    } catch (err) {
      setError(err instanceof ApiError ? describeLoadFailure(err) : t('auth:login.unexpectedError'));
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <OnboardingShell>
        <Box sx={{ ...authFormSx, alignItems: 'center' }}>
          <CircularProgress aria-label={t('common:loading')} />
        </Box>
      </OnboardingShell>
    );
  }

  return (
    <OnboardingShell>
      <Box component="form" onSubmit={handleSubmit} noValidate sx={authFormSx}>
        <Typography component="h1" sx={authHeadingSx}>
          {t('auth:acceptInvite.title')}
        </Typography>

        {loadError ? (
          <>
            <Alert severity="error">{loadError}</Alert>
            <Link component={RouterLink} to="/login" sx={authLinkSx}>
              {t('auth:forgotPassword.backToLogin')}
            </Link>
          </>
        ) : accepted ? (
          <Alert severity="success">{t('auth:acceptInvite.successMessage')}</Alert>
        ) : (
          <>
            <Typography sx={authSubtitleSx}>
              {t('auth:acceptInvite.subtitle', {
                organization: invitation?.organization_name ?? '',
                role: t(`auth:acceptInvite.roles.${invitation?.role ?? 'participant'}`),
              })}
            </Typography>

            <Alert severity="info" sx={{ marginBlockEnd: '8px' }}>
              {t('auth:acceptInvite.emailNotice', { email: invitation?.email ?? '' })}
            </Alert>

            <Typography component="label" htmlFor="invite-name" sx={authLabelSx}>
              {t('auth:register.displayNameLabel')}
            </Typography>
            <ClavisTextField
              id="invite-name"
              fullWidth
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              required
              autoComplete="name"
              disabled={submitting}
              error={!!fieldErrors.displayName}
              helperText={fieldErrors.displayName}
              size="small"
              placeholder={t('auth:register.displayNameLabel')}
              slotProps={{ formHelperText: { role: 'alert' } }}
            />

            <Typography
              component="label"
              htmlFor="invite-password"
              sx={{ ...authLabelSx, marginBlockStart: '8px' }}
            >
              {t('auth:register.passwordLabel')}
            </Typography>
            <ClavisTextField
              id="invite-password"
              fullWidth
              type="password"
              passwordToggle
              showPasswordAriaLabel={t('auth:login.showPassword')}
              hidePasswordAriaLabel={t('auth:login.hidePassword')}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
              disabled={submitting}
              error={!!fieldErrors.password}
              helperText={fieldErrors.password}
              size="small"
              placeholder={t('auth:register.passwordLabel')}
              slotProps={{ formHelperText: { role: 'alert' } }}
            />

            <Typography
              component="label"
              htmlFor="invite-confirm"
              sx={{ ...authLabelSx, marginBlockStart: '8px' }}
            >
              {t('auth:register.confirmPasswordLabel')}
            </Typography>
            <ClavisTextField
              id="invite-confirm"
              fullWidth
              type="password"
              passwordToggle
              showPasswordAriaLabel={t('auth:login.showPassword')}
              hidePasswordAriaLabel={t('auth:login.hidePassword')}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              autoComplete="new-password"
              disabled={submitting}
              error={!!fieldErrors.confirmPassword}
              helperText={fieldErrors.confirmPassword}
              size="small"
              placeholder={t('auth:register.confirmPasswordLabel')}
              slotProps={{ formHelperText: { role: 'alert' } }}
            />

            {error && <Alert severity="error">{error}</Alert>}

            <Button
              type="submit"
              variant="contained"
              disableElevation
              disabled={submitting}
              sx={authPrimaryButtonSx}
            >
              {submitting ? (
                <CircularProgress size={20} sx={{ color: '#FFFFFF' }} />
              ) : (
                t('auth:acceptInvite.submitButton')
              )}
            </Button>
          </>
        )}
      </Box>
    </OnboardingShell>
  );
};

export default AcceptInvite;
