import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  FormControlLabel,
  FormHelperText,
  InputLabel,
  MenuItem,
  Select,
  Switch,
  TextField,
  useMediaQuery,
} from '@mui/material';
import { updateUser } from '../../api/admin';
import { userErrorMessage } from './userErrors';
import type { UserResponse, UserUpdateRequest } from '../../api/types';
import ConfirmDialog from '../ConfirmDialog';
import ErrorAlert from '../ErrorAlert';
import RoleCapabilityPanel from './RoleCapabilityPanel';
import type { RoleName } from './roleCapabilities';
import { useRolePreview } from './useRolePreview';

interface UserEditDialogProps {
  open: boolean;
  user: UserResponse | null;
  orgSlug: string;
  onSuccess: () => void;
  onClose: () => void;
}

const UserEditDialog: React.FC<UserEditDialogProps> = ({ open, user, orgSlug, onSuccess, onClose }) => {
  const { t } = useTranslation(['admin', 'common']);
  const fullScreen = useMediaQuery('(max-width:599px)');
  // Float the capability panel outside the dialog only when there's room; below
  // that it stacks inside the dialog (keeps the dialog itself xs-sized).
  const floatPanel = useMediaQuery('(min-width:900px)');

  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<NonNullable<UserUpdateRequest['role']>>('participant');
  // A role change is a privilege change, so picking one only *stages* it — the
  // select keeps showing the old role until the confirm is answered. The
  // capability panel explains what a role grants; this is the deliberate gate
  // in front of the change. The two are not interchangeable, and removing this
  // one while adding that one makes the gate disappear silently.
  const [pendingRole, setPendingRole] = useState<NonNullable<UserUpdateRequest['role']> | null>(
    null,
  );
  const [isActive, setIsActive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const isGuest = user?.role === 'guest';

  const { displayRole, clearPreview, previewProps } = useRolePreview(role);

  useEffect(() => {
    if (user) {
      setDisplayName(user.display_name ?? '');
      setEmail(user.email ?? '');
      // Guests have no assignable role and their control is disabled; every
      // other role — org_admin included — loads as itself, so the dropdown never
      // claims a role the user does not hold.
      setRole(user.role === 'guest' ? 'participant' : user.role);
      setIsActive(user.is_active);
      setPendingRole(null);
      setError(null);
    }
  }, [user]);

  const handleRoleChange = (newRole: NonNullable<UserUpdateRequest['role']>) => {
    if (newRole !== role) setPendingRole(newRole);
  };

  const handleRoleConfirmed = () => {
    if (pendingRole) setRole(pendingRole);
    setPendingRole(null);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user) return;
    setSubmitting(true);
    setError(null);
    try {
      const payload: UserUpdateRequest = { display_name: displayName, is_active: isActive };
      // Guests hold no persistent role/email — those fields are disabled and
      // never sent (the backend rejects them anyway).
      if (!isGuest) {
        payload.role = role;
        payload.email = email;
      }
      await updateUser(orgSlug, user.id, payload);
      onSuccess();
    } catch (err) {
      // Never the API's English text.
      setError(userErrorMessage(err, t));
    } finally {
      setSubmitting(false);
    }
  };

  if (!user) return null;

  // While a change is staged the panel shows what the *new* role would grant —
  // that is precisely the question the confirm is asking, so the two read as one
  // thought rather than two competing explanations.
  const panelRole: RoleName = isGuest ? 'guest' : (pendingRole ?? displayRole);

  return (
    <>
    <Dialog
      open={open}
      onClose={submitting ? undefined : onClose}
      maxWidth="xs"
      fullWidth
      fullScreen={fullScreen}
      aria-labelledby="user-edit-dialog-title"
      slotProps={{ paper: { sx: { overflow: 'visible', position: 'relative' } } }}
    >
      <form onSubmit={(e) => void handleSubmit(e)}>
        <DialogTitle id="user-edit-dialog-title">{t('admin:users.edit.title')}</DialogTitle>
        <DialogContent>
          {error && <ErrorAlert message={error} />}
          <TextField
            fullWidth
            label={t('admin:users.edit.displayNameLabel')}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            margin="normal"
            required
            disabled={submitting}
          />
          <TextField
            fullWidth
            type="email"
            label={t('admin:users.edit.emailLabel', { defaultValue: 'E-Mail' })}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            margin="normal"
            disabled={submitting || isGuest}
            slotProps={{ input: { readOnly: isGuest } }}
          />
          <FormControl fullWidth margin="normal" disabled={submitting || isGuest}>
            <InputLabel id="user-edit-role-label">{t('admin:users.edit.roleLabel')}</InputLabel>
            <Select
              labelId="user-edit-role-label"
              id="user-edit-role"
              value={role}
              label={t('admin:users.edit.roleLabel')}
              onChange={(e) =>
                handleRoleChange(e.target.value as NonNullable<UserUpdateRequest['role']>)
              }
              onClose={clearPreview}
              MenuProps={{ slotProps: { list: { onMouseLeave: clearPreview } } }}
              disabled={submitting || isGuest}
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
            {isGuest && (
              <FormHelperText>
                {t('admin:users.edit.guestHint', {
                  defaultValue: 'Gäste können sich nur selbst zu einem Konto hochstufen.',
                })}
              </FormHelperText>
            )}
          </FormControl>
          <FormControlLabel
            control={
              <Switch checked={isActive} onChange={(e) => setIsActive(e.target.checked)} disabled={submitting} />
            }
            label={t('admin:users.edit.activeLabel')}
            sx={{ mt: 1 }}
          />
          {!floatPanel && <RoleCapabilityPanel role={panelRole} variant="inline" />}
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={submitting}>
            {t('common:actions.cancel')}
          </Button>
          <Button type="submit" variant="contained" disabled={submitting}>
            {submitting ? <CircularProgress size={22} /> : t('admin:users.edit.submitButton')}
          </Button>
        </DialogActions>
        {floatPanel && <RoleCapabilityPanel role={panelRole} variant="floating" />}
      </form>
    </Dialog>
    {/* Outside the form on purpose — inside it, the confirm button submits it. */}
    <ConfirmDialog
      open={pendingRole !== null}
      title={t('admin:users.edit.roleChangeTitle')}
      message={t('admin:users.edit.roleChangeMessage', {
        name: user.display_name,
        oldRole: t(`admin:users.roles.${role}`),
        newRole: t(`admin:users.roles.${pendingRole ?? role}`),
      })}
      onConfirm={handleRoleConfirmed}
      onCancel={() => setPendingRole(null)}
    />
    </>
  );
};

export default UserEditDialog;
