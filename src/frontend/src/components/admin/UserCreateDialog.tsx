import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  TextField,
  useMediaQuery,
} from '@mui/material';
import { createUser } from '../../api/admin';
import { userErrorMessage } from './userErrors';
import type { UserCreateRequest } from '../../api/types';
import ErrorAlert from '../ErrorAlert';
import RoleCapabilityPanel from './RoleCapabilityPanel';
import type { RoleName } from './roleCapabilities';
import { useRolePreview } from './useRolePreview';
import { validatePasswordPolicy } from '../../utils/passwordPolicy';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface UserCreateDialogProps {
  open: boolean;
  orgSlug: string;
  onSuccess: () => void;
  onClose: () => void;
}

const UserCreateDialog: React.FC<UserCreateDialogProps> = ({
  open,
  orgSlug,
  onSuccess,
  onClose,
}) => {
  const { t } = useTranslation(['admin', 'auth', 'common']);
  const fullScreen = useMediaQuery('(max-width:599px)');
  const floatPanel = useMediaQuery('(min-width:900px)');

  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<UserCreateRequest['role']>('participant');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const { displayRole, clearPreview, previewProps } = useRolePreview((role ?? 'participant') as RoleName);

  const resetForm = () => {
    setEmail('');
    setDisplayName('');
    setPassword('');
    setRole('participant');
    setFieldErrors({});
    setError(null);
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!email.trim()) {
      errors.email = t('auth:register.errors.required');
    } else if (!EMAIL_RE.test(email)) {
      errors.email = t('auth:register.errors.emailInvalid');
    }
    if (!displayName.trim()) {
      errors.displayName = t('auth:register.errors.required');
    }
    if (!password) {
      errors.password = t('auth:register.errors.required');
    } else if (!validatePasswordPolicy(password)) {
      errors.password = t('auth:register.errors.passwordPolicy');
    }
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;
    setSubmitting(true);
    setError(null);
    try {
      await createUser(orgSlug, { email, display_name: displayName, password, role });
      resetForm();
      onSuccess();
    } catch (err) {
      // Never the API's English text.
      setError(userErrorMessage(err, t));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={submitting ? undefined : handleClose}
      maxWidth="xs"
      fullWidth
      fullScreen={fullScreen}
      aria-labelledby="user-create-dialog-title"
      slotProps={{ paper: { sx: { overflow: 'visible', position: 'relative' } } }}
    >
      <form onSubmit={(e) => void handleSubmit(e)}>
        <DialogTitle id="user-create-dialog-title">{t('admin:users.create.title')}</DialogTitle>
        <DialogContent>
          {error && <ErrorAlert message={error} />}
          <TextField
            fullWidth
            label={t('admin:users.create.emailLabel')}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            margin="normal"
            required
            disabled={submitting}
            error={!!fieldErrors.email}
            helperText={fieldErrors.email}
            slotProps={{ formHelperText: { role: 'alert' } }}
          />
          <TextField
            fullWidth
            label={t('admin:users.create.displayNameLabel')}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            margin="normal"
            required
            disabled={submitting}
            error={!!fieldErrors.displayName}
            helperText={fieldErrors.displayName}
            slotProps={{ formHelperText: { role: 'alert' } }}
          />
          <TextField
            fullWidth
            label={t('admin:users.create.passwordLabel')}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            margin="normal"
            required
            autoComplete="new-password"
            disabled={submitting}
            error={!!fieldErrors.password}
            helperText={fieldErrors.password}
            slotProps={{ formHelperText: { role: 'alert' } }}
          />
          <FormControl fullWidth margin="normal">
            <InputLabel id="user-create-role-label">{t('admin:users.create.roleLabel')}</InputLabel>
            <Select
              labelId="user-create-role-label"
              id="user-create-role"
              value={role}
              label={t('admin:users.create.roleLabel')}
              onChange={(e) => setRole(e.target.value as UserCreateRequest['role'])}
              onClose={clearPreview}
              MenuProps={{ slotProps: { list: { onMouseLeave: clearPreview } } }}
              disabled={submitting}
            >
              <MenuItem value="participant" {...previewProps('participant')}>
                {t('admin:users.roles.participant')}
              </MenuItem>
              <MenuItem value="coordinator" {...previewProps('coordinator')}>
                {t('admin:users.roles.coordinator')}
              </MenuItem>
              <MenuItem value="creator" {...previewProps('creator')}>
                {t('admin:users.roles.creator')}
              </MenuItem>
              <MenuItem value="org_admin" {...previewProps('org_admin')}>
                {t('admin:users.roles.org_admin')}
              </MenuItem>
            </Select>
          </FormControl>
          {!floatPanel && <RoleCapabilityPanel role={displayRole} variant="inline" />}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose} disabled={submitting}>
            {t('common:actions.cancel')}
          </Button>
          <Button type="submit" variant="contained" disabled={submitting}>
            {submitting ? (
              <CircularProgress size={22} />
            ) : (
              t('admin:users.create.submitButton')
            )}
          </Button>
        </DialogActions>
        {floatPanel && <RoleCapabilityPanel role={displayRole} variant="floating" />}
      </form>
    </Dialog>
  );
};

export default UserCreateDialog;
