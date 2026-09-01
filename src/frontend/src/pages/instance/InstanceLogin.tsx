import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
  TextField,
} from '@mui/material';
import { QRCodeSVG } from 'qrcode.react';
import InstanceShell from './InstanceShell';
import ClavisTextField from '../../components/ClavisTextField';
import { instanceLogin, instanceTotpConfirm } from '../../api/instance';
import { ApiError } from '../../api/client';

type Step = 'password' | 'confirm';
type Field = 'email' | 'password' | 'code';

const ERROR_ID = 'instance-login-error';
const CODE_ID = 'instance-login-code';
// MUI names the helper text of a field `<id>-helper-text`.
const CODE_HINT_ID = `${CODE_ID}-helper-text`;

// A refusal is announced once, by the focus move: the first step focuses the
// container of this text, the code step focuses the field that points to it. So the
// text sits in no live region. Alert's default role is `alert`; `presentation`
// replaces it and leaves look, colour and icon of severity "error" as they were.
const RefusalText: React.FC<{ message: string }> = ({ message }) => (
  <Alert severity="error" role="presentation">
    {message}
  </Alert>
);

const InstanceLogin: React.FC = () => {
  const { t } = useTranslation('instance');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  // Set by the API client's interceptor when a password change ended this
  // session. It tells the operator why they are back here.
  const sessionRevoked = searchParams.get('reason') === 'session_revoked';
  const sessionExpired = searchParams.get('reason') === 'session_expired';

  const [step, setStep] = useState<Step>('password');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  // Handed out once, on the first login only (see instance_auth.py). It is
  // null on a later unconfirmed login, which still needs /totp/confirm but has
  // no secret left to show as a QR code.
  const [provisioningUri, setProvisioningUri] = useState<string | null>(null);
  const [uriCopied, setUriCopied] = useState(false);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  // The fields the shown error names. A field leaves the list as soon as its
  // value changes.
  const [invalidFields, setInvalidFields] = useState<Field[]>([]);
  const errorRef = useRef<HTMLDivElement>(null);
  const codeInputRef = useRef<HTMLInputElement>(null);
  // Set when a step was refused; the effect below spends it once the request has ended.
  // 'code' is the code field (a refusal that marks it), 'error' the error text.
  const focusAfterRefusal = useRef<'code' | 'error' | null>(null);
  // Set before a request starts and cleared when it ends. Nothing is natively disabled while
  // the request runs (Chrome drops the focus of a disabled element to BODY), so this ref is
  // what refuses a second press or a second Enter. State alone is not enough: two submits in
  // one tick both read the old state.
  const submitting = useRef(false);

  // The first step unmounts the button or field that had the focus. The focus goes to the
  // page heading, as RouteFocus does on a route change, so the set-up text and
  // the QR code stay in reading order above the code field. Without a heading the code field
  // takes it. Either way it is there before the browser paints.
  useLayoutEffect(() => {
    if (step !== 'confirm') return;
    const heading = document.querySelector('main')?.querySelector<HTMLElement>('h1');
    if (heading) {
      if (!heading.hasAttribute('tabindex')) heading.tabIndex = -1;
      heading.focus();
    } else {
      codeInputRef.current?.focus();
    }
  }, [step]);

  useEffect(() => {
    if (!focusAfterRefusal.current || isSubmitting || !error) return;
    const target = focusAfterRefusal.current;
    focusAfterRefusal.current = null;
    if (target === 'code') {
      codeInputRef.current?.focus();
      codeInputRef.current?.select();
    } else {
      errorRef.current?.focus();
    }
  }, [error, isSubmitting, step]);

  const fieldProps = (field: Field, describedBy?: string) => {
    const invalid = !!error && invalidFields.includes(field);
    return {
      'aria-invalid': invalid ? (true as const) : undefined,
      'aria-describedby': invalid ? [describedBy, ERROR_ID].filter(Boolean).join(' ') : describedBy,
    };
  };
  const clearInvalid = (field: Field) => setInvalidFields((prev) => prev.filter((f) => f !== field));

  // One mapping for both steps. `names` says whether the refusal marks the fields the
  // generic text names; the states that do not (the account, the server, the set-up) leave
  // every field unmarked, so their focus goes to the error text.
  const describeRefusal = (err: unknown, forStep: Step): { message: string; names: boolean } => {
    const generic = t(forStep === 'password' ? 'login.invalidCredentials' : 'login.invalidCode');
    if (!(err instanceof ApiError)) return { message: generic, names: true };
    if (err.status === 429) return { message: t('login.rateLimited'), names: true };
    if (err.status === 403 && err.errorCode === 'INSTANCE_ADMIN_DISABLED') {
      return { message: t('login.accountDisabled'), names: false };
    }
    if (err.status >= 500) return { message: t('login.serverError'), names: false };
    if (err.status === 0) return { message: t('login.noAnswer'), names: false };
    if (forStep === 'confirm' && (err.status === 400 || err.status === 409)) {
      return { message: t('login.setupCannotFinish'), names: false };
    }
    return { message: generic, names: true };
  };

  const handlePasswordSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // A render that shows the busy state still has this handler until the result renders;
    // `isSubmitting` of that render refuses a press in that gap, the ref refuses all others.
    if (submitting.current || isSubmitting) return;
    submitting.current = true;
    setError('');
    setInvalidFields([]);
    setIsSubmitting(true);
    try {
      const result = await instanceLogin({
        email,
        password,
        totp_code: code || undefined,
      });
      if (result.status === 'success') {
        navigate('/instance', { replace: true });
        return;
      }
      setProvisioningUri(result.provisioning_uri);
      setCode('');
      setStep('confirm');
    } catch (err) {
      const refusal = describeRefusal(err, 'password');
      setError(refusal.message);
      setInvalidFields(
        refusal.names ? (code ? ['email', 'password', 'code'] : ['email', 'password']) : [],
      );
      focusAfterRefusal.current = 'error';
    } finally {
      submitting.current = false;
      setIsSubmitting(false);
    }
  };

  const handleConfirmSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting.current || isSubmitting) return;
    submitting.current = true;
    setError('');
    setInvalidFields([]);
    setIsSubmitting(true);
    try {
      const result = await instanceTotpConfirm({ email, password, totp_code: code });
      if (result.status === 'success') {
        navigate('/instance', { replace: true });
      }
    } catch (err) {
      // Address and password passed the step before, so only the code can be
      // wrong here.
      const refusal = describeRefusal(err, 'confirm');
      setError(refusal.message);
      setInvalidFields(refusal.names ? ['code'] : []);
      focusAfterRefusal.current = refusal.names ? 'code' : 'error';
    } finally {
      submitting.current = false;
      setIsSubmitting(false);
    }
  };

  if (step === 'confirm') {
    return (
      <InstanceShell title={provisioningUri ? t('login.totpSetupTitle') : t('login.codeTitle')}>
        <Box component="form" onSubmit={(e) => void handleConfirmSubmit(e)} noValidate>
          <Stack spacing={2}>
            <Typography color="text.secondary">
              {provisioningUri ? t('login.totpSetupBody') : t('login.codeBody')}
            </Typography>
            {provisioningUri && (
              <>
                <Box sx={{ display: 'flex', justifyContent: 'center' }}>
                  <QRCodeSVG value={provisioningUri} size={200} title={t('login.qrTitle')} />
                </Box>
                {/* A plain TextField, because the clear button of
                    ClavisTextField makes no sense on a read-only field. The
                    address wraps anywhere, so the secret is never cut off. */}
                <TextField
                  label={t('login.provisioningUriLabel')}
                  value={provisioningUri}
                  size="small"
                  fullWidth
                  multiline
                  slotProps={{ htmlInput: { readOnly: true, style: { wordBreak: 'break-all' } } }}
                />
                {/* No clipboard without a secure origin: then the field alone stays, and its text can be selected. */}
                {typeof navigator.clipboard?.writeText === 'function' && (
                  <Button
                    variant="outlined"
                    size="small"
                    sx={{ alignSelf: 'flex-start', minHeight: 44 }}
                    onClick={() => {
                      navigator.clipboard.writeText(provisioningUri).then(
                        () => setUriCopied(true),
                        () => setUriCopied(false),
                      );
                    }}
                  >
                    {t(uriCopied ? 'login.provisioningUriCopied' : 'login.provisioningUriCopy')}
                  </Button>
                )}
              </>
            )}
            <ClavisTextField
              label={t('login.codeLabel')}
              value={code}
              onChange={(e) => {
                setCode(e.target.value.replace(/\D/g, '').slice(0, 6));
                clearInvalid('code');
              }}
              required
              autoComplete="one-time-code"
              size="small"
              fullWidth
              clearAriaLabel={t('login.clearField')}
              inputRef={codeInputRef}
              error={!!error && invalidFields.includes('code')}
              slotProps={{
                htmlInput: {
                  inputMode: 'numeric',
                  pattern: '[0-9]{6}',
                  ...fieldProps('code'),
                  readOnly: isSubmitting,
                },
              }}
            />
            {error && (
              <Box id={ERROR_ID} ref={errorRef} tabIndex={-1}>
                <RefusalText message={error} />
              </Box>
            )}
            <Button
              type="submit"
              variant="contained"
              disableElevation
              disabled={!isSubmitting && code.length !== 6}
              aria-disabled={isSubmitting}
              sx={{ alignSelf: 'flex-start' }}
            >
              {t('login.confirmButton')}
              {isSubmitting && <CircularProgress size={16} aria-hidden sx={{ ml: 1 }} />}
            </Button>
          </Stack>
        </Box>
      </InstanceShell>
    );
  }

  return (
    <InstanceShell title={t('login.title')}>
      <Typography color="text.secondary" sx={{ mb: 3 }}>
        {t('login.subtitle')}
      </Typography>
      {sessionRevoked && <Alert severity="info">{t('login.sessionRevokedNotice')}</Alert>}
      {sessionExpired && <Alert severity="info">{t('login.sessionExpiredNotice')}</Alert>}
      <Box component="form" onSubmit={(e) => void handlePasswordSubmit(e)} noValidate>
        <Stack spacing={2}>
          <ClavisTextField
            label={t('login.emailLabel')}
            type="email"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              clearInvalid('email');
            }}
            required
            autoComplete="email"
            size="small"
            fullWidth
            clearAriaLabel={t('login.clearField')}
            error={!!error && invalidFields.includes('email')}
            slotProps={{ htmlInput: { ...fieldProps('email'), readOnly: isSubmitting } }}
          />
          <ClavisTextField
            label={t('login.passwordLabel')}
            type="password"
            passwordToggle
            showPasswordAriaLabel={t('login.showPassword')}
            hidePasswordAriaLabel={t('login.hidePassword')}
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              clearInvalid('password');
            }}
            required
            autoComplete="current-password"
            size="small"
            fullWidth
            error={!!error && invalidFields.includes('password')}
            slotProps={{ htmlInput: { ...fieldProps('password'), readOnly: isSubmitting } }}
          />
          <ClavisTextField
            label={t('login.codeLabel')}
            id={CODE_ID}
            helperText={t('login.codeHint')}
            value={code}
            onChange={(e) => {
              setCode(e.target.value.replace(/\D/g, '').slice(0, 6));
              clearInvalid('code');
            }}
            autoComplete="one-time-code"
            size="small"
            fullWidth
            clearAriaLabel={t('login.clearField')}
            // No `error` here: ClavisTextField would make the hint under the
            // field a second live region. The red outline is drawn by hand.
            sx={
              !!error && invalidFields.includes('code')
                ? {
                    '& .MuiOutlinedInput-root .MuiOutlinedInput-notchedOutline': {
                      borderColor: 'error.main',
                    },
                  }
                : undefined
            }
            slotProps={{
              htmlInput: {
                inputMode: 'numeric',
                pattern: '[0-9]{6}',
                ...fieldProps('code', CODE_HINT_ID),
                readOnly: isSubmitting,
              },
            }}
          />
          {error && (
            <Box id={ERROR_ID} ref={errorRef} tabIndex={-1}>
              <RefusalText message={error} />
            </Box>
          )}
          <Button
            type="submit"
            variant="contained"
            disableElevation
            disabled={!isSubmitting && (!email || !password)}
            aria-disabled={isSubmitting}
            sx={{ alignSelf: 'flex-start' }}
          >
            {t('login.submit')}
            {isSubmitting && <CircularProgress size={16} aria-hidden sx={{ ml: 1 }} />}
          </Button>
        </Stack>
      </Box>
    </InstanceShell>
  );
};

export default InstanceLogin;
