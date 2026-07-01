import React, { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControl,
  InputLabel,
  MenuItem,
  Select,
  TextField,
  useMediaQuery,
} from '@mui/material';
import { createInvitation, sendPasswordReset } from '../../api/admin';
import { ApiError } from '../../api/client';
import { userErrorMessage } from './userErrors';
import type { InvitableRole, InvitationResponse } from '../../api/types';
import ErrorAlert from '../ErrorAlert';
import RoleCapabilityPanel from './RoleCapabilityPanel';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface UserInviteDialogProps {
  open: boolean;
  orgSlug: string;
  /** False when the deployment cannot send mail — the dialog says so up front. */
  mailEnabled: boolean;
  onSuccess: (invitation: InvitationResponse) => void;
  onClose: () => void;
}

/**
 * Invite a colleague by address and role.
 *
 * Deliberately shorter than the "create user" dialog next to it: there is no
 * password field, because the whole point is that the administrator never
 * chooses, sees, or has to transmit somebody else's password.
 */
const UserInviteDialog: React.FC<UserInviteDialogProps> = ({
  open,
  orgSlug,
  mailEnabled,
  onSuccess,
  onClose,
}) => {
  const { t, i18n } = useTranslation(['admin', 'auth', 'common']);
  const fullScreen = useMediaQuery('(max-width:599px)');

  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState<InvitableRole>('coordinator');
  const [language, setLanguage] = useState<'de' | 'en'>(
    (i18n.resolvedLanguage === 'en' ? 'en' : 'de'),
  );
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // EMAIL_BELONGS_TO_USER carries the colliding user's id only for an active
  // account in the same organization. That id is the remedy target for "send
  // a password reset link" instead of a dead end.
  const [conflictUserId, setConflictUserId] = useState<string | null>(null);
  const [resetSent, setResetSent] = useState(false);
  const [sendingReset, setSendingReset] = useState(false);
  // Bumped every time the collision on screen changes (a new submit, or the
  // email field edited). A reset started against an earlier collision checks
  // this before applying its result, so a late answer for address A can never
  // land on address B's remedy.
  const conflictVersionRef = useRef(0);

  const resetForm = () => {
    setEmail('');
    setDisplayName('');
    setRole('coordinator');
    setFieldErrors({});
    setError(null);
    setConflictUserId(null);
    setResetSent(false);
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!email.trim()) {
      errors.email = t('auth:register.errors.required');
    } else if (!EMAIL_RE.test(email.trim())) {
      errors.email = t('auth:register.errors.emailInvalid');
    }
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    conflictVersionRef.current += 1;
    setError(null);
    setConflictUserId(null);
    setResetSent(false);
    setSendingReset(false);
    if (!validate()) return;
    setSubmitting(true);
    try {
      const invitation = await createInvitation(orgSlug, {
        email: email.trim(),
        role,
        language,
        ...(displayName.trim() ? { display_name: displayName.trim() } : {}),
      });
      resetForm();
      onSuccess(invitation);
    } catch (err) {
      if (err instanceof ApiError && err.errorCode === 'EMAIL_BELONGS_TO_USER' && UUID_RE.test(err.detail)) {
        setConflictUserId(err.detail);
      } else if (err instanceof ApiError && err.errorCode === 'EMAIL_BELONGS_TO_USER') {
        setError(t('admin:users.invite.errors.emailBelongsToUser'));
      } else if (err instanceof ApiError && err.errorCode === 'EMAIL_BELONGS_TO_INACTIVE_USER') {
        setError(t('admin:users.invite.errors.emailBelongsToInactiveUser'));
      } else {
        // Never the API's English text.
        setError(userErrorMessage(err, t));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleEmailChange = (value: string) => {
    setEmail(value);
    // The remedy targets a specific address's collision. Once the admin edits
    // the field, that collision no longer describes what's on screen — clear
    // it so a stale button never sends a reset to the wrong account, and
    // invalidate any reset already in flight for the old one.
    conflictVersionRef.current += 1;
    setConflictUserId(null);
    setResetSent(false);
    setSendingReset(false);
  };

  const handleSendReset = async () => {
    if (!conflictUserId) return;
    const requestVersion = conflictVersionRef.current;
    setSendingReset(true);
    try {
      await sendPasswordReset(orgSlug, conflictUserId);
      // The email field may have changed (or been resubmitted) while this
      // request was in flight — an answer for a stale collision must not
      // claim a link was sent for whatever is on screen now.
      if (conflictVersionRef.current === requestVersion) {
        setResetSent(true);
      }
    } catch (err) {
      if (conflictVersionRef.current === requestVersion) {
        setError(err instanceof ApiError ? err.detail : String(err));
      }
    } finally {
      if (conflictVersionRef.current === requestVersion) {
        setSendingReset(false);
      }
    }
  };

  return (
    <Dialog open={open} onClose={handleClose} fullWidth maxWidth="sm" fullScreen={fullScreen}>
      <form onSubmit={handleSubmit} noValidate>
        <DialogTitle>{t('admin:users.invite.title')}</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ marginBlockEnd: 2 }}>
            {t('admin:users.invite.intro')}
          </DialogContentText>

          {!mailEnabled && (
            <Alert severity="warning" sx={{ marginBlockEnd: 2 }}>
              {t('admin:users.invite.mailDisabled')}
            </Alert>
          )}

          {error && <ErrorAlert message={error} />}

          {conflictUserId && (
            <Alert
              severity={resetSent ? 'success' : 'warning'}
              sx={{ marginBlockEnd: 2 }}
              action={
                resetSent ? undefined : (
                  <Button color="inherit" size="small" onClick={handleSendReset} disabled={sendingReset}>
                    {sendingReset ? <CircularProgress size={16} /> : t('admin:users.sendReset.action')}
                  </Button>
                )
              }
            >
              {resetSent
                ? t('admin:users.sendReset.success', { email: email.trim() })
                : t('admin:users.invite.errors.emailBelongsToActiveUser')}
            </Alert>
          )}

          <TextField
            autoFocus
            fullWidth
            margin="dense"
            type="email"
            label={t('admin:users.invite.emailLabel')}
            value={email}
            onChange={(e) => handleEmailChange(e.target.value)}
            error={!!fieldErrors.email}
            helperText={fieldErrors.email}
            disabled={submitting}
            required
            autoComplete="email"
          />

          <TextField
            fullWidth
            margin="dense"
            label={t('admin:users.invite.displayNameLabel')}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            helperText={t('admin:users.invite.displayNameHelper')}
            disabled={submitting}
          />

          <FormControl fullWidth margin="dense">
            <InputLabel id="invite-role-label">{t('admin:users.invite.roleLabel')}</InputLabel>
            <Select
              labelId="invite-role-label"
              label={t('admin:users.invite.roleLabel')}
              value={role}
              onChange={(e) => setRole(e.target.value as InvitableRole)}
              disabled={submitting}
            >
              <MenuItem value="participant">{t('admin:users.roles.participant')}</MenuItem>
              <MenuItem value="coordinator">{t('admin:users.roles.coordinator')}</MenuItem>
              <MenuItem value="creator">{t('admin:users.roles.creator')}</MenuItem>
              <MenuItem value="org_admin">{t('admin:users.roles.org_admin')}</MenuItem>
            </Select>
          </FormControl>
          {/* The same panel as in "Person anlegen": whoever invites decides
              on a role here too and needs to see what it may do. */}
          <RoleCapabilityPanel role={role} variant="inline" />

          <FormControl fullWidth margin="dense">
            <InputLabel id="invite-language-label">
              {t('admin:users.invite.languageLabel')}
            </InputLabel>
            <Select
              labelId="invite-language-label"
              label={t('admin:users.invite.languageLabel')}
              value={language}
              onChange={(e) => setLanguage(e.target.value as 'de' | 'en')}
              disabled={submitting}
            >
              <MenuItem value="de">Deutsch</MenuItem>
              <MenuItem value="en">English</MenuItem>
            </Select>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose} disabled={submitting}>
            {t('common:actions.cancel')}
          </Button>
          <Button type="submit" variant="contained" disabled={submitting}>
            {submitting ? <CircularProgress size={20} /> : t('admin:users.invite.submitButton')}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};

export default UserInviteDialog;
