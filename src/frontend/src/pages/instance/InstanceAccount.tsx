import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Box, Button, CircularProgress, Link as MuiLink, Stack, TextField, Typography } from '@mui/material';
import { Link as RouterLink } from 'react-router';
import InstanceShell from './InstanceShell';
import { useInstanceAuth } from './useInstanceAuth';
import { instanceApiErrorMessage, instancePasswordChange } from '../../api/instance';
import { ApiError } from '../../api/client';
import LoadingSpinner from '../../components/LoadingSpinner';
import { validatePasswordPolicy } from '../../utils/passwordPolicy';

const InstanceAccount: React.FC = () => {
  const { t } = useTranslation('instance');
  const { status, admin, signOut } = useInstanceAuth();

  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  const handleSignOut = async () => {
    setSignOutError(null);
    try {
      await signOut();
    } catch (err) {
      setSignOutError(instanceApiErrorMessage(err));
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const errors: Record<string, string> = {};
    if (!oldPassword) errors.oldPassword = t('account.errors.required');
    if (!validatePasswordPolicy(newPassword)) errors.newPassword = t('account.errors.passwordPolicy');
    if (newPassword !== confirmPassword) errors.confirmPassword = t('account.errors.passwordMismatch');
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setSaving(true);
    setMessage(null);
    try {
      await instancePasswordChange({ old_password: oldPassword, new_password: newPassword });
      setMessage({ type: 'success', text: t('account.saveSuccess') });
      setOldPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setFieldErrors((prev) => ({ ...prev, oldPassword: t('account.errors.oldPasswordIncorrect') }));
      } else if (err instanceof ApiError && err.errorCode === 'INSTANCE_PASSWORD_REUSE_NOT_ALLOWED') {
        setFieldErrors((prev) => ({
          ...prev,
          newPassword: t('account.errors.INSTANCE_PASSWORD_REUSE_NOT_ALLOWED'),
        }));
      } else {
        setMessage({ type: 'error', text: instanceApiErrorMessage(err) });
      }
    } finally {
      setSaving(false);
    }
  };

  if (status === 'loading' || status === 'unauthenticated') {
    return (
      <InstanceShell title={t('account.title')}>
        <LoadingSpinner />
      </InstanceShell>
    );
  }

  if (status === 'forbidden') {
    return (
      <InstanceShell title={t('login.forbiddenTitle')}>
        <Typography color="text.secondary" sx={{ mb: 2 }}>
          {t('login.forbiddenBody')}
        </Typography>
        <MuiLink component={RouterLink} to="/login" underline="hover">
          {t('login.forbiddenLink')}
        </MuiLink>
      </InstanceShell>
    );
  }

  return (
    <InstanceShell title={t('account.title')} onSignOut={() => void handleSignOut()} maxWidth="sm">
      {signOutError && (
        <Alert severity="error" role="alert" onClose={() => setSignOutError(null)} sx={{ mb: 2 }}>
          {signOutError}
        </Alert>
      )}
      {admin && (
        <Typography color="text.secondary" sx={{ mb: 3 }}>
          {admin.email}
        </Typography>
      )}
      <Typography variant="h6" component="h2" gutterBottom>
        {t('account.changePasswordTitle')}
      </Typography>
      <Box component="form" onSubmit={(e) => void handleSubmit(e)} noValidate>
        <Stack spacing={2}>
          <TextField
            fullWidth
            label={t('account.oldPasswordLabel')}
            type="password"
            value={oldPassword}
            onChange={(e) => setOldPassword(e.target.value)}
            required
            autoComplete="current-password"
            disabled={saving}
            error={!!fieldErrors.oldPassword}
            helperText={fieldErrors.oldPassword}
            slotProps={{ formHelperText: { role: 'alert' } }}
          />
          <TextField
            fullWidth
            label={t('account.newPasswordLabel')}
            type="password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
            autoComplete="new-password"
            disabled={saving}
            error={!!fieldErrors.newPassword}
            helperText={fieldErrors.newPassword}
            slotProps={{ formHelperText: { role: 'alert' } }}
          />
          <TextField
            fullWidth
            label={t('account.confirmPasswordLabel')}
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            autoComplete="new-password"
            disabled={saving}
            error={!!fieldErrors.confirmPassword}
            helperText={fieldErrors.confirmPassword}
            slotProps={{ formHelperText: { role: 'alert' } }}
          />

          {message && (
            <Alert severity={message.type} role={message.type === 'error' ? 'alert' : undefined}>
              {message.text}
            </Alert>
          )}

          <Button type="submit" variant="contained" disabled={saving} sx={{ alignSelf: 'flex-start' }}>
            {saving ? <CircularProgress size={22} /> : t('account.saveButton')}
          </Button>
        </Stack>
      </Box>
    </InstanceShell>
  );
};

export default InstanceAccount;
