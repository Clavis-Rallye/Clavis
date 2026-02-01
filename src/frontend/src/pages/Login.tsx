/**
 * Sign-in page with the 2FA branches and the join-code handoff into the wizard.
 */
import React, { useState } from 'react';
import {
  useNavigate,
  useLocation,
  useSearchParams,
  Link as RouterLink,
} from 'react-router';
import { useTranslation, Trans } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Link,
  Typography,
} from '@mui/material';
import OnboardingShell from '../components/OnboardingShell';
import ProgressIndicator from '../components/ProgressIndicator';
import ClavisTextField from '../components/ClavisTextField';
import LanguageSwitcher from '../components/LanguageSwitcher';
import OnboardingBrandHeader from '../components/OnboardingBrandHeader';
import { useAuth } from '../auth/useAuth';
import { getStepInfo } from '../wizard/wizardSteps';
import { getNextAfterAuth } from '../routes/ProfileSetupRedirect';
import { pillButtonFocusRing } from '../styles/onboardingButtons';

const Login: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const { login } = useAuth();
  const { t } = useTranslation('auth');

  const joinCode = searchParams.get('join');
  const wizardStep = joinCode ? getStepInfo('entry-choice', 'with-code') : null;
  // Set by the API client's interceptor when a password
  // change ended this session; explains why the person is back on this page.
  const sessionRevoked = searchParams.get('reason') === 'session_revoked';
  // Set by the API client when the operator archived the person's
  // organization while they were signed in.
  const orgArchived = searchParams.get('reason') === 'org_archived';
  // Set by the API client when an admin deactivated the account.
  const accountDisabled = searchParams.get('reason') === 'account_disabled';

  // Set by the invitation page after it created the account: this person has
  // never signed in, so "welcome back" would be wrong, and the address of the
  // invitation is the one they sign in with.
  const arrival = location.state as { newAccount?: boolean; email?: string } | null;
  const newAccount = arrival?.newAccount === true;

  const [email, setEmail] = useState(
    newAccount && typeof arrival?.email === 'string' ? arrival.email : '',
  );
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsLoading(true);

    try {
      const result = await login(email, password);

      if (result.error) {
        setError(result.error);
        setIsLoading(false);
        return;
      }

      if (result.status === '2fa_required') {
        sessionStorage.setItem('clavis_2fa_pending_email', email);
        navigate('/2fa-verify', { replace: true, state: location.state });
        return;
      }

      if (result.status === '2fa_setup_required') {
        navigate('/2fa-setup', { replace: true, state: location.state });
        return;
      }

      const from =
        (location.state as { from?: { pathname?: string } })?.from?.pathname ||
        (joinCode ? getNextAfterAuth(joinCode) : '/dashboard');
      navigate(from, { replace: true });
    } catch {
      setError(t('login.unexpectedError'));
      setIsLoading(false);
    }
  };

  return (
    <OnboardingShell
      contentSx={{ paddingInline: 0 }}
      progress={
        wizardStep ? (
          <ProgressIndicator
            current={wizardStep.current}
            total={wizardStep.total}
          />
        ) : undefined
      }
    >
      <OnboardingBrandHeader />
      <Box
        component="form"
        id="login-form"
        onSubmit={handleSubmit}
        noValidate
        sx={{
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
          paddingInline: '40px',
          paddingBlockStart: '24px',
        }}
      >
        <Typography
          component="h1"
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '24px',
            fontWeight: 600,
            lineHeight: '32px',
            color: 'text.primary',
            margin: 0,
          }}
        >
          <Trans
            t={t}
            i18nKey={newAccount ? 'login.welcomeNew' : 'login.welcome'}
            components={{
              brand: <Box component="span" sx={{ color: 'primary.main' }} />,
            }}
          />
        </Typography>
        <Typography
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 500,
            lineHeight: '24px',
            letterSpacing: '0.15px',
            color: '#322F35',
            marginBlockEnd: '16px',
          }}
        >
          {t(newAccount ? 'login.subtitleNew' : 'login.subtitle')}
        </Typography>

        {sessionRevoked && <Alert severity="info">{t('login.sessionRevokedNotice')}</Alert>}
        {orgArchived && <Alert severity="warning">{t('login.errors.orgArchived')}</Alert>}
        {accountDisabled && <Alert severity="warning">{t('login.errors.accountDisabled')}</Alert>}

        <Typography
          component="label"
          htmlFor="login-email"
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 500,
            lineHeight: '24px',
            letterSpacing: '0.15px',
            color: 'text.primary',
          }}
        >
          {t('login.emailLabel')}
        </Typography>
        <ClavisTextField
          id="login-email"
          fullWidth
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
          disabled={isLoading}
          size="small"
          placeholder={t('login.emailPlaceholder')}
          clearAriaLabel={t('login.clearField')}
          error={Boolean(error)}
          helperText={error || ' '}
        />

        <Typography
          component="label"
          htmlFor="login-password"
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 500,
            lineHeight: '24px',
            letterSpacing: '0.15px',
            color: 'text.primary',
            marginBlockStart: '8px',
          }}
        >
          {t('login.passwordLabel')}
        </Typography>
        <ClavisTextField
          id="login-password"
          fullWidth
          type="password"
          passwordToggle
          showPasswordAriaLabel={t('login.showPassword')}
          hidePasswordAriaLabel={t('login.hidePassword')}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoComplete="current-password"
          disabled={isLoading}
          size="small"
          placeholder={t('login.passwordPlaceholder')}
          clearAriaLabel={t('login.clearField')}
        />

        {/* The button comes before the two links in the DOM
            so Tab reaches sign-in, then forgot-password, then register. */}
        <Button
          type="submit"
          disabled={isLoading}
          variant="contained"
          disableElevation
          sx={{
            marginBlockStart: '16px',
            alignSelf: 'flex-start',
            minHeight: '40px',
            paddingInline: '24px',
            borderRadius: '100px',
            backgroundColor: '#4D69B9',
            color: '#FFFFFF',
            textTransform: 'none',
            fontFamily: "'Roboto', sans-serif",
            fontSize: '14px',
            fontWeight: 500,
            lineHeight: '20px',
            letterSpacing: '0.1px',
            '&:hover': { backgroundColor: '#3D54A0' },
            // MUI sets pointer-events:none on disabled buttons (which is the
            // fix — a click can no longer slip through to a disabled control).
            // Restore the design's disabled fill + label; the doubled `&&`
            // out-specifies MUI's own contained-disabled background rule.
            '&&.Mui-disabled': {
              backgroundColor: '#9AA6CF',
              color: '#FFFFFF',
            },
            ...pillButtonFocusRing,
          }}
        >
          {isLoading ? (
            <CircularProgress size={20} sx={{ color: '#FFFFFF' }} />
          ) : (
            t('login.submitButton')
          )}
        </Button>

        <Link
          component={RouterLink}
          to="/forgot-password"
          underline="none"
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 500,
            lineHeight: '24px',
            letterSpacing: '0.15px',
            color: 'primary.main',
            marginBlockStart: '16px',
          }}
        >
          {t('login.forgotPassword')}
        </Link>

        <Link
          component={RouterLink}
          to="/register"
          underline="none"
          sx={{
            fontFamily: "'Noto Sans', sans-serif",
            fontSize: '16px',
            fontWeight: 500,
            lineHeight: '24px',
            letterSpacing: '0.15px',
            color: 'primary.main',
          }}
        >
          {t('login.registerLink')}
        </Link>
      </Box>
      {/* Without the switch, a new browser profile has no way to change the
          language of this page. The switch sits after the
          form, so the first Tab still lands on the e-mail field and the
          order sign-in, forgot password, register stays as it is. */}
      <Box sx={{ display: 'flex', justifyContent: 'center', px: 2, pt: 2 }}>
        <LanguageSwitcher />
      </Box>
    </OnboardingShell>
  );
};

export default Login;
