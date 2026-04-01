import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Card,
  CardContent,
  Typography,
  TextField,
  Button,
  Alert,
  CircularProgress,
  Chip,
  Stack,
} from '@mui/material';
import {
  getProfile,
  updateProfile,
  changePassword,
  disableTotp,
} from '../api/auth';
import { ApiError } from '../api/client';
import { exportMyData } from '../api/me';
import type { UserResponse } from '../api/types';
import { useAuth } from '../auth/useAuth';
import LanguageSwitcher from '../components/LanguageSwitcher';
import { validatePasswordPolicy } from '../utils/passwordPolicy';

const Profile: React.FC = () => {
  const { t, i18n } = useTranslation(['auth', 'admin']);
  const navigate = useNavigate();
  const { checkAuth } = useAuth();

  const [profile, setProfile] = useState<UserResponse | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [isSavingProfile, setIsSavingProfile] = useState(false);
  const [profileMessage, setProfileMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordErrors, setPasswordErrors] = useState<Record<string, string>>({});
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [passwordMessage, setPasswordMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const [disablePassword, setDisablePassword] = useState('');
  const [isDisabling2fa, setIsDisabling2fa] = useState(false);
  const [twoFactorMessage, setTwoFactorMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const [isExporting, setIsExporting] = useState(false);
  const [exportMessage, setExportMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await getProfile();
        if (!cancelled) {
          setProfile(data);
          setDisplayName(data.display_name ?? '');
        }
      } catch (err) {
        if (!cancelled) {
          setProfileMessage({
            type: 'error',
            text: err instanceof ApiError ? err.detail : t('login.unexpectedError'),
          });
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [t]);

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!profile || displayName === profile.display_name) return;
    setIsSavingProfile(true);
    setProfileMessage(null);
    try {
      await updateProfile({ display_name: displayName });
      setProfile({ ...profile, display_name: displayName });
      setProfileMessage({ type: 'success', text: t('profile.saved') });
      await checkAuth();
    } catch (err) {
      setProfileMessage({
        type: 'error',
        text: err instanceof ApiError ? err.detail : t('login.unexpectedError'),
      });
    } finally {
      setIsSavingProfile(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (!oldPassword) errs.oldPassword = t('register.errors.required');
    if (!validatePasswordPolicy(newPassword)) errs.newPassword = t('register.errors.passwordPolicy');
    if (newPassword !== confirmPassword) errs.confirmPassword = t('register.errors.passwordMismatch');
    setPasswordErrors(errs);
    if (Object.keys(errs).length > 0) return;

    setIsChangingPassword(true);
    setPasswordMessage(null);
    try {
      await changePassword({ old_password: oldPassword, new_password: newPassword });
      setPasswordMessage({ type: 'success', text: t('profile.passwordChanged') });
      setOldPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err) {
      setPasswordMessage({
        type: 'error',
        text: err instanceof ApiError ? err.detail : t('login.unexpectedError'),
      });
    } finally {
      setIsChangingPassword(false);
    }
  };

  const handleExportData = async () => {
    setIsExporting(true);
    setExportMessage(null);
    try {
      const blob = await exportMyData();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'clavis-data-export.json';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setExportMessage({ type: 'success', text: t('profile.dataExportSuccess', { defaultValue: 'Export gestartet.' }) });
    } catch (err) {
      setExportMessage({
        type: 'error',
        text: err instanceof ApiError ? err.detail : String(err),
      });
    } finally {
      setIsExporting(false);
    }
  };

  const handleDisable2fa = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!profile) return;
    setIsDisabling2fa(true);
    setTwoFactorMessage(null);
    try {
      await disableTotp({ password: disablePassword });
      setProfile({ ...profile, totp_enabled: false });
      setDisablePassword('');
      setTwoFactorMessage({ type: 'success', text: t('profile.twoFactorDisabled') });
    } catch (err) {
      setTwoFactorMessage({
        type: 'error',
        text: err instanceof ApiError ? err.detail : t('login.unexpectedError'),
      });
    } finally {
      setIsDisabling2fa(false);
    }
  };

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          py: 6
        }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!profile) {
    return (
      <Box sx={{
        p: 2
      }}>
        <Alert severity="error">{profileMessage?.text ?? t('login.unexpectedError')}</Alert>
      </Box>
    );
  }

  const lastLogin = profile.last_login
    ? new Date(profile.last_login).toLocaleString(i18n.language)
    : t('profile.lastLoginNever');

  return (
    <Box>
      <Typography variant="h4" component="h1" gutterBottom>
        {t('profile.title')}
      </Typography>
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <form onSubmit={handleSaveProfile}>
            <TextField
              fullWidth
              label={t('profile.emailLabel')}
              value={profile.email}
              margin="normal"
              disabled
            />
            <TextField
              fullWidth
              label={t('profile.displayNameLabel')}
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              margin="normal"
              required
              disabled={isSavingProfile}
            />
            <Stack
              direction="row"
              spacing={2}
              sx={{
                alignItems: "center",
                mt: 2
              }}>
              <Typography variant="body2" sx={{
                color: "text.secondary"
              }}>
                {t('profile.roleLabel')}:
              </Typography>
              <Chip label={t(`admin:users.roles.${profile.role}`)} size="small" />
            </Stack>
            <Typography
              variant="body2"
              sx={{
                color: "text.secondary",
                mt: 1
              }}>
              {t('profile.lastLoginLabel')}: {lastLogin}
            </Typography>

            {profileMessage && (
              <Alert severity={profileMessage.type} sx={{ mt: 2 }}>
                {profileMessage.text}
              </Alert>
            )}

            <Button
              type="submit"
              variant="contained"
              sx={{ mt: 2 }}
              disabled={isSavingProfile || displayName === profile.display_name}
            >
              {isSavingProfile ? <CircularProgress size={22} /> : t('profile.saveButton')}
            </Button>
          </form>
        </CardContent>
      </Card>
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" component="h2" gutterBottom>
            {t('profile.changePasswordTitle')}
          </Typography>
          <form onSubmit={handleChangePassword}>
            <TextField
              fullWidth
              label={t('profile.oldPasswordLabel')}
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              margin="normal"
              required
              autoComplete="current-password"
              disabled={isChangingPassword}
              error={!!passwordErrors.oldPassword}
              helperText={passwordErrors.oldPassword}
              slotProps={{ formHelperText: { role: 'alert' } }}
            />
            <TextField
              fullWidth
              label={t('profile.newPasswordLabel')}
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              margin="normal"
              required
              autoComplete="new-password"
              disabled={isChangingPassword}
              error={!!passwordErrors.newPassword}
              helperText={passwordErrors.newPassword}
              slotProps={{ formHelperText: { role: 'alert' } }}
            />
            <TextField
              fullWidth
              label={t('profile.confirmPasswordLabel')}
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              margin="normal"
              required
              autoComplete="new-password"
              disabled={isChangingPassword}
              error={!!passwordErrors.confirmPassword}
              helperText={passwordErrors.confirmPassword}
              slotProps={{ formHelperText: { role: 'alert' } }}
            />

            {passwordMessage && (
              <Alert severity={passwordMessage.type} sx={{ mt: 2 }}>
                {passwordMessage.text}
              </Alert>
            )}

            <Button
              type="submit"
              variant="contained"
              sx={{ mt: 2 }}
              disabled={isChangingPassword}
            >
              {isChangingPassword ? <CircularProgress size={22} /> : t('profile.changePasswordButton')}
            </Button>
          </form>
        </CardContent>
      </Card>
      <Card>
        <CardContent>
          <Typography variant="h6" component="h2" gutterBottom>
            {t('profile.twoFactorTitle')}
          </Typography>
          <Stack
            direction="row"
            spacing={2}
            sx={{
              alignItems: "center",
              mb: 2
            }}>
            <Chip
              label={
                profile.totp_enabled
                  ? t('profile.twoFactorEnabled')
                  : t('profile.twoFactorDisabled')
              }
              color={profile.totp_enabled ? 'success' : 'default'}
              size="small"
            />
          </Stack>

          {profile.totp_enabled ? (
            <form onSubmit={handleDisable2fa}>
              <TextField
                fullWidth
                label={t('profile.disableConfirmPasswordLabel')}
                type="password"
                value={disablePassword}
                onChange={(e) => setDisablePassword(e.target.value)}
                margin="normal"
                required
                autoComplete="current-password"
                disabled={isDisabling2fa}
              />
              {twoFactorMessage && (
                <Alert severity={twoFactorMessage.type} sx={{ mt: 2 }}>
                  {twoFactorMessage.text}
                </Alert>
              )}
              <Button
                type="submit"
                variant="outlined"
                color="warning"
                sx={{ mt: 2 }}
                disabled={isDisabling2fa || !disablePassword}
              >
                {isDisabling2fa ? <CircularProgress size={22} /> : t('profile.disableTwoFactor')}
              </Button>
            </form>
          ) : (
            <>
              {twoFactorMessage && (
                <Alert severity={twoFactorMessage.type} sx={{ mb: 2 }}>
                  {twoFactorMessage.text}
                </Alert>
              )}
              <Button variant="contained" onClick={() => navigate('/2fa-setup', { state: { from: '/profile' } })}>
                {t('profile.enableTwoFactor')}
              </Button>
            </>
          )}
        </CardContent>
      </Card>
      <Card sx={{ mt: 3 }}>
        <CardContent>
          <Typography variant="h6" component="h2" gutterBottom>
            {t('profile.languageTitle')}
          </Typography>
          <LanguageSwitcher />
        </CardContent>
      </Card>
      <Card sx={{ mt: 3 }}>
        <CardContent>
          <Typography variant="h6" component="h2" gutterBottom>
            {t('profile.dataExportTitle', { defaultValue: 'Meine Daten exportieren' })}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {t('profile.dataExportDescription', {
              defaultValue:
                'Lade deine persönlichen Daten (Profil, Teilnahmen, Einreichungen) als JSON-Datei herunter (DSGVO Art. 20).',
            })}
          </Typography>
          {exportMessage && (
            <Alert severity={exportMessage.type} sx={{ mb: 2 }}>
              {exportMessage.text}
            </Alert>
          )}
          <Button variant="outlined" onClick={() => void handleExportData()} disabled={isExporting}>
            {isExporting ? (
              <CircularProgress size={22} />
            ) : (
              t('profile.dataExportButton', { defaultValue: 'Als JSON exportieren' })
            )}
          </Button>
        </CardContent>
      </Card>
    </Box>
  );
};

export default Profile;
