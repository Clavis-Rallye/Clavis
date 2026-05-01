/**
 * Profile step of the onboarding wizard. The guest switch toggles between the
 * e-mail and password fields and the guest info card. It
 * validates, registers, maps the register errors, syncs displayName at submit
 * and initialises pathKind. A direct /register visit goes through the
 * OrganizationPicker first.
 */
import React, { useEffect, useState } from 'react';
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from 'react-router';
import { useTranslation } from 'react-i18next';
import Box from '@mui/material/Box';
import Collapse from '@mui/material/Collapse';
import Typography from '@mui/material/Typography';

import OnboardingShell from '../components/OnboardingShell';
import OnboardingBrandHeader from '../components/OnboardingBrandHeader';
import ProgressIndicator from '../components/ProgressIndicator';
import heroProfile from '../assets/onboarding/hero-profile.svg';
import toggleOnIcon from '../assets/icons/toggle_on.svg';
import toggleOffIcon from '../assets/icons/toggle_off.svg';
import infoIcon from '../assets/icons/info.svg';
import ClavisTextField from '../components/ClavisTextField';
import OrganizationPicker from './OrganizationPicker';
import { useWizardState } from '../wizard/useWizardState';
import { getStepInfo } from '../wizard/wizardSteps';
import { register as registerApi } from '../api/auth';
import { useAuth } from '../auth/useAuth';
import { useInstanceConfig } from '../config/instanceConfig';
import { ApiError } from '../api/client';
import { pillButtonFocusRing } from '../styles/onboardingButtons';
import { validatePasswordPolicy } from '../utils/passwordPolicy';

interface ProfileSetupStepProps {
  guestModeDefault?: boolean;
}

const RESERVED_NAMES = new Set(['admin', 'system', 'null', 'undefined']);
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function nextStepAfterProfile(runCode: string | null): string {
  if (!runCode) return '/dashboard';
  return `/join/${runCode}/team/find`;
}

// The design gives the Username label a box of 28 px and the E-Mail and
// Password labels one of 20 px, at the same font and line height. The
// `bbox28` prop adds 4 px of padding-block-end for the 28 px box. It is false
// by default.
const FieldLabel: React.FC<{
  children: React.ReactNode;
  htmlFor?: string;
  bbox28?: boolean;
}> = ({ children, htmlFor, bbox28 }) => (
  <Typography
    component="label"
    htmlFor={htmlFor}
    sx={{
      fontFamily: "'Noto Sans', sans-serif",
      fontSize: '16px',
      fontWeight: 500,
      lineHeight: '24px',
      letterSpacing: '0.15px',
      color: 'text.primary',
      // The body/large line box (16 px on 24 px) has 3 px of leading above the
      // glyph. The design's text box has none. Pull the element up by 3 px.
      marginBlockStart: '-3px',
      // Only the Username label has a 28 px box in the design (the line height is
      // 24 px), so only it gets the 4 px of padding. The E-Mail and Password labels
      // have 20 px and need none.
      ...(bbox28 ? { paddingBlockEnd: '4px' } : null),
    }}
  >
    {children}
  </Typography>
);

const ProfileSetupStep: React.FC<ProfileSetupStepProps> = ({
  guestModeDefault,
}) => {
  const { t } = useTranslation('profileSetup');
  const { t: tc } = useTranslation('common');
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { code: routeCode } = useParams<{ code?: string }>();

  // The org selection is bound to a `?org=<slug>` URL param, not to
  // sticky wizard/sessionStorage state. Selecting an org pushes a history
  // entry, so the browser back button returns to the picker instead of
  // skipping it. Guests never reach the picker (they carry a routeCode and the
  // org comes from the run), so this only affects the direct /register flow.
  const orgFromUrl = searchParams.get('org');
  // On a single-organization instance the organization
  // step of onboarding is skipped: the one organization is preselected
  // without asking. `null` on zero-org and multi-org instances (and until
  // the boot config resolved), in which case the picker behaves as before.
  const { single_org_slug: singleOrgSlug } = useInstanceConfig();
  const wizard = useWizardState();
  const { checkAuth } = useAuth();

  // `account=1` comes from "Neues Konto erstellen" on the entry step: the
  // person chose an account there, so the guest switch starts off.
  const initialGuest = searchParams.get('account') === '1' ? false : (guestModeDefault ?? Boolean(routeCode));
  const [guestMode, setGuestMode] = useState<boolean>(initialGuest);
  const [displayName, setDisplayName] = useState<string>(wizard.displayName);
  const [email, setEmail] = useState<string>('');
  const [password, setPassword] = useState<string>('');
  const [passwordConfirm, setPasswordConfirm] = useState<string>('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (routeCode && wizard.pathKind !== 'with-code') {
      wizard.setPathKind('with-code');
      if (!wizard.runCode) wizard.setRunCode(routeCode);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeCode]);

  const validate = (): boolean => {
    const next: Record<string, string> = {};
    const trimmed = displayName.trim();
    if (trimmed.length < 2 || trimmed.length > 32) {
      next.displayName = t('usernameError.tooShort');
    } else if (RESERVED_NAMES.has(trimmed.toLowerCase())) {
      next.displayName = t('usernameError.reserved');
    }
    if (!guestMode) {
      if (!EMAIL_RE.test(email)) next.email = t('emailError.invalid');
      if (!validatePasswordPolicy(password)) {
        next.password = t('passwordError.tooWeak');
      }
      if (password !== passwordConfirm) {
        next.passwordConfirm = t('passwordConfirmError.mismatch');
      }
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const onContinue = async () => {
    if (!validate()) return;
    setSubmitting(true);
    try {
      if (guestMode) {
        wizard.setDisplayName(displayName.trim());
        navigate({
          pathname: nextStepAfterProfile(wizard.runCode ?? routeCode ?? null),
          search: location.search,
        });
        return;
      }

      const slug = orgFromUrl ?? singleOrgSlug ?? wizard.organizationSlug;
      if (!slug) {
        setErrors({ form: t('orgPicker.orgUnknown') });
        return;
      }
      try {
        const res = await registerApi({
          organization_slug: slug,
          email,
          password,
          display_name: displayName.trim(),
        });
        await checkAuth();
        wizard.setDisplayName(displayName.trim());
        // A codeless registration on a single-organization instance lands on the
        // no-runs page, which explains how to join a run with the code or link from
        // the coordination and offers the code field. It does not land on the staff
        // dashboard.
        const landing =
          !wizard.runCode && !routeCode && singleOrgSlug
            ? '/no-runs'
            : nextStepAfterProfile(wizard.runCode ?? routeCode ?? null);
        if (res.requires_2fa) {
          // The join code must survive 2FA: hand the computed landing
          // (the join-flow path, not just /dashboard) to TwoFactorSetup via
          // state.from, so it returns here instead of the fallback.
          navigate(
            { pathname: '/2fa-setup', search: location.search },
            { state: { from: landing } },
          );
          return;
        }
        navigate({ pathname: landing, search: location.search });
      } catch (err) {
        if (err instanceof ApiError) {
          const detail = typeof err.detail === 'string' ? err.detail : '';
          if (err.status === 409 && /email/i.test(detail)) {
            setErrors({ email: t('emailError.taken') });
            return;
          }
          if (err.status === 409) {
            setErrors({ displayName: t('usernameError.taken') });
            return;
          }
          if (err.status === 400) {
            setErrors({ password: t('passwordError.tooWeak') });
            return;
          }
          if (err.status === 404) {
            setErrors({ form: t('orgPicker.orgNotFound') });
            return;
          }
        }
        setErrors({ form: t('orgPicker.registrationFailed') });
      }
    } finally {
      setSubmitting(false);
    }
  };

  const needsOrgPicker = !guestMode && !routeCode && !orgFromUrl && !singleOrgSlug;
  if (needsOrgPicker) {
    return (
      <OrganizationPicker
        onSelect={(org) => {
          // Mirror into wizard state (kept for consistency) and push the slug
          // into the URL so back-navigation returns to the picker.
          wizard.setOrganizationSlug(org.slug);
          setSearchParams((prev) => {
            const next = new URLSearchParams(prev);
            next.set('org', org.slug);
            return next;
          });
        }}
      />
    );
  }

  const showGuestToggle = Boolean(routeCode);
  const step = getStepInfo('profile-setup', wizard.pathKind);

  return (
    <OnboardingShell
      progress={
        <ProgressIndicator
          current={step.current}
          total={step.total}
        />
      }
    >
      {/* The profile mark, distinct from the OrganizationPicker's fox
          logo but rendered through the shared header so it's the SAME size and
          Y as every other /register step. Same position, different icon. */}
      <OnboardingBrandHeader src={heroProfile} />
      <Box
        component="form"
        noValidate
        // Password fields must live inside a <form> so password
        // managers detect and offer to save the credentials. Submit is
        // intercepted; onContinue keeps handling navigation/registration.
        onSubmit={(e: React.FormEvent) => {
          e.preventDefault();
          void onContinue();
        }}
        sx={{
          display: 'flex',
          flexDirection: 'column',
          gap: '24px',
        }}
      >
        <Typography
          component="h1"
          sx={{
            fontFamily: "'Roboto', sans-serif",
            fontSize: '24px',
            fontWeight: 600,
            lineHeight: '32px',
            letterSpacing: 0,
            color: '#322F35',
            margin: 0,
            // The headline/small line box (24 px on 32 px) has 2 px of leading above the
            // glyph. The design's text box has none. Pull the element up by 2 px.
            marginBlockStart: '-2px',
          }}
        >
          {t('headline')}
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <FieldLabel htmlFor="profile-username" bbox28>{t('usernameLabel')}</FieldLabel>
          <ClavisTextField
            id="profile-username"
            autoComplete="nickname"
            placeholder={t('usernamePlaceholder')}
            helperText={errors.displayName ?? t('usernameHelper')}
            error={Boolean(errors.displayName)}
            value={displayName}
            onChange={(e) => {
              setDisplayName(e.target.value);
              // Kept in the wizard as it is typed: a name that was not yet
              // confirmed with "Weiter" was gone after back and forward in
              // the browser.
              wizard.setDisplayName(e.target.value);
            }}
            slotProps={{ htmlInput: { maxLength: 32, 'aria-invalid': Boolean(errors.displayName), 'aria-required': true } }}
            fullWidth
          />
        </Box>

        {showGuestToggle && (
          // The row is 298×40 with a gap of 8 px. The switch is not a Switch component.
          // It is a `toggle_on`/`toggle_off` icon (40×40 outer, 24×24 viewBox) with a
          // 37×20 track vector in #1D47A4 (on) or #605D64 (off). It renders via CSS mask,
          // so the track color can change per state.
          <Box
            component="button"
            type="button"
            role="switch"
            aria-checked={guestMode}
            aria-label={t('guestToggleLabel')}
            onClick={() => setGuestMode((g) => !g)}
            sx={{
              all: 'unset',
              boxSizing: 'border-box',
              cursor: 'pointer',
              display: 'flex',
              flexDirection: 'row',
              alignItems: 'center',
              gap: '8px',
              alignSelf: 'flex-start',
              // The design puts the supporting text "Your username is visible…" outside
              // the field, in the 24 px gap above. MUI keeps helper text inside the field,
              // so pull this up by 11 px. That leaves a visible gap of 13 px to the
              // supporting text.
              marginTop: '-11px',
              ...pillButtonFocusRing,
            }}
          >
            <Box
              component="img"
              src={guestMode ? toggleOnIcon : toggleOffIcon}
              alt=""
              aria-hidden
              sx={{
                width: '40px',
                height: '40px',
                flexShrink: 0,
                display: 'block',
              }}
            />
            <Box
              component="span"
              sx={{
                fontFamily: "'Noto Sans', sans-serif",
                fontSize: '16px',
                fontWeight: 500,
                lineHeight: '24px',
                letterSpacing: '0.15px',
                color: '#322F35',
              }}
            >
              {t('guestToggleLabel')}
            </Box>
          </Box>
        )}

        <Collapse in={!guestMode} unmountOnExit>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <FieldLabel htmlFor="profile-email">{t('emailLabel')}</FieldLabel>
              <ClavisTextField
                id="profile-email"
                placeholder={t('emailPlaceholder')}
                type="email"
                autoComplete="email"
                helperText={errors.email}
                error={Boolean(errors.email)}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                fullWidth
              />
            </Box>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <FieldLabel htmlFor="profile-password">{t('passwordLabel')}</FieldLabel>
              <ClavisTextField
                id="profile-password"
                placeholder={t('passwordPlaceholder')}
                type="password"
                passwordToggle
                showPasswordAriaLabel={tc('aria.showPassword')}
                hidePasswordAriaLabel={tc('aria.hidePassword')}
                autoComplete="new-password"
                helperText={errors.password}
                error={Boolean(errors.password)}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                fullWidth
              />
            </Box>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <FieldLabel htmlFor="profile-password-confirm">{t('passwordConfirmLabel')}</FieldLabel>
              <ClavisTextField
                id="profile-password-confirm"
                type="password"
                passwordToggle
                showPasswordAriaLabel={tc('aria.showPassword')}
                hidePasswordAriaLabel={tc('aria.hidePassword')}
                autoComplete="new-password"
                helperText={errors.passwordConfirm}
                error={Boolean(errors.passwordConfirm)}
                value={passwordConfirm}
                onChange={(e) => setPasswordConfirm(e.target.value)}
                fullWidth
              />
            </Box>
          </Box>
        </Collapse>

        <Collapse in={guestMode} unmountOnExit>
          <Box
            sx={{
              // In the design the body overflows the padding area by 5 px, so its effective
              // right margin is about 9 px, not 14 px. CSS has no such overflow, so the right
              // padding is 9 px. The body then wraps to 3 lines as in the design, not 4.
              backgroundColor: '#E6E9F4',
              border: '1px solid #FFFFFF',
              borderRadius: '22px',
              paddingBlock: '13px',
              paddingLeft: '14px',
              paddingRight: '9px',
              display: 'flex',
              flexDirection: 'column',
              gap: '12px',
            }}
          >
            <Box
              sx={{
                alignSelf: 'flex-start',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                backgroundColor: '#4D69B9',
                color: '#FFFFFF',
                borderRadius: '8px',
                paddingInline: '8px',
                paddingBlock: '1px',
                fontFamily: "'Noto Sans', sans-serif",
                fontSize: '11px',
                fontWeight: 500,
                lineHeight: '16px',
                letterSpacing: '0.5px',
              }}
            >
              <Box
                component="img"
                src={infoIcon}
                alt=""
                aria-hidden
                sx={{
                  width: '12px',
                  height: '12px',
                  flexShrink: 0,
                  display: 'block',
                }}
              />
              {tc('infoBadge')}
            </Box>
            <Typography
              sx={{
                fontFamily: "'Noto Sans', sans-serif",
                fontSize: '14px',
                fontWeight: 400,
                lineHeight: '20px',
                letterSpacing: '0.25px',
                color: '#00348D',
              }}
            >
              {t('guestInfoBox.body')}
            </Typography>
          </Box>
        </Collapse>

        {errors.form && (
          <Typography
            role="alert"
            sx={{
              fontFamily: "'Noto Sans', sans-serif",
              fontSize: '14px',
              color: 'error.main',
            }}
          >
            {errors.form}
          </Typography>
        )}

        <Box
          sx={{
            display: 'flex',
            flexDirection: 'row',
            gap: '16px',
            // The design's e-mail and password fields render no helper text, so nothing
            // overflows. Without an offset the row's top sits where the design's row box
            // starts. The visible pill is 4 px lower in the design, so account mode adds
            // 4 px. In guest mode the last sibling is the info box, which has no overflow
            // either, and the box height delta is absorbed upstream, so the offset is 0.
            marginBlockStart: guestMode ? '0px' : '4px',
          }}
        >
          <Box
            component="button"
            type="button"
            onClick={() => navigate(-1)}
            disabled={submitting}
            sx={{
              all: 'unset',
              boxSizing: 'border-box',
              cursor: submitting ? 'not-allowed' : 'pointer',
              minHeight: '40px',
              paddingInline: '16px',
              borderRadius: '100px',
              // Draw the 1 px stroke inside with an inset box-shadow. A CSS `border` would
              // inflate the pill from 40 to 42 px in height and 2 px in width.
              boxShadow: 'inset 0 0 0 1px var(--color-outline-variant, #CAC4D0)',
              color: 'text.secondary',
              fontFamily: "'Roboto', sans-serif",
              fontSize: '14px',
              fontWeight: 500,
              lineHeight: '20px',
              letterSpacing: '0.1px',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              '&:hover': { backgroundColor: 'rgba(73, 69, 79, 0.04)' },
              ...pillButtonFocusRing,
            }}
          >
            {tc('actions.back', { defaultValue: 'Back' })}
          </Box>
          <Box
            component="button"
            type="submit"
            disabled={submitting}
            sx={{
              all: 'unset',
              boxSizing: 'border-box',
              cursor: submitting ? 'not-allowed' : 'pointer',
              opacity: submitting ? 0.6 : 1,
              minHeight: '40px',
              paddingInline: '16px',
              borderRadius: '100px',
              backgroundColor: '#4D69B9',
              color: '#FFFFFF',
              fontFamily: "'Roboto', sans-serif",
              fontSize: '14px',
              fontWeight: 500,
              lineHeight: '20px',
              letterSpacing: '0.1px',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              '&:hover': { backgroundColor: '#3D54A0' },
              ...pillButtonFocusRing,
            }}
          >
            {submitting ? t('submitting') : tc('actions.continue', { defaultValue: 'Continue' })}
          </Box>
        </Box>
      </Box>
    </OnboardingShell>
  );
};

export default ProfileSetupStep;
