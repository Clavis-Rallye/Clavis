import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  FormGroup,
  FormHelperText,
  TextField,
  Typography,
  useMediaQuery,
} from '@mui/material';
import { inviteInstanceOrgAdmin, instanceApiErrorMessage, listInstanceOrgAdmins } from '../../api/instance';
import { ApiError } from '../../api/client';
import type {
  InstanceOrganizationListItem,
  InstanceOrgAdminInvitationResponse,
  InstanceOrgAdminSummary,
} from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface OrgAdminInviteDialogProps {
  open: boolean;
  org: InstanceOrganizationListItem | null;
  onSuccess: (invitation: InstanceOrgAdminInvitationResponse) => void;
  onClose: () => void;
}

const OrgAdminInviteDialog: React.FC<OrgAdminInviteDialogProps> = ({
  open,
  org,
  onSuccess,
  onClose,
}) => {
  const { t } = useTranslation('instance');
  const fullScreen = useMediaQuery('(max-width:599px)');

  const [email, setEmail] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // The invitation can take over from current admins. The list is a narrow
  // exception to "the operator sees no people".
  const [admins, setAdmins] = useState<InstanceOrgAdminSummary[] | null>(null);
  const [replaceIds, setReplaceIds] = useState<string[]>([]);

  useEffect(() => {
    if (org) {
      setEmail('');
      setFieldErrors({});
      setError(null);
      setReplaceIds([]);
    }
  }, [org]);

  useEffect(() => {
    if (!open || !org) return;
    let cancelled = false;
    setAdmins(null);
    listInstanceOrgAdmins(org.slug)
      .then((items) => {
        if (!cancelled) setAdmins(items);
      })
      .catch(() => {
        if (!cancelled) setAdmins([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, org]);

  const toggleReplace = (id: string, checked: boolean) =>
    setReplaceIds((prev) => (checked ? [...prev, id] : prev.filter((x) => x !== id)));

  const handleClose = () => {
    setEmail('');
    setFieldErrors({});
    setError(null);
    onClose();
  };

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!email.trim()) errors.email = t('invite.required');
    else if (!EMAIL_RE.test(email)) errors.email = t('invite.emailInvalid');
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!org) return;
    // An earlier server error must not outlive the next attempt.
    setError(null);
    if (!validate()) return;
    setSubmitting(true);
    try {
      const invitation = await inviteInstanceOrgAdmin(org.slug, {
        email,
        ...(replaceIds.length ? { replace_admin_ids: replaceIds } : {}),
      });
      setEmail('');
      onSuccess(invitation);
    } catch (err) {
      // Both refusals belong to the address: they show at the field only.
      // An address with an account can never accept.
      if (
        err instanceof ApiError &&
        (err.errorCode === 'EMAIL_HAS_PENDING_INVITATION' || err.errorCode === 'EMAIL_HAS_ACCOUNT')
      ) {
        setFieldErrors((prev) => ({ ...prev, email: t(`errors.${err.errorCode}`) }));
      } else {
        setError(instanceApiErrorMessage(err));
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (!org) return null;

  return (
    <Dialog
      open={open}
      onClose={submitting ? undefined : handleClose}
      maxWidth="xs"
      fullWidth
      fullScreen={fullScreen}
      aria-labelledby="org-admin-invite-dialog-title"
    >
      <form onSubmit={(e) => void handleSubmit(e)} noValidate>
        <DialogTitle id="org-admin-invite-dialog-title">{t('invite.title')}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {t('invite.intro', { name: org.name })}
          </Typography>
          {error && <ErrorAlert message={error} />}
          <TextField
            fullWidth
            label={t('invite.emailLabel')}
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
          {admins === null ? (
            <CircularProgress size={20} sx={{ mt: 2 }} aria-label={t('invite.replaceLabel')} />
          ) : (
            <Box component="fieldset" sx={{ border: 0, p: 0, m: 0, mt: 2 }}>
              <Typography variant="body2" component="legend" sx={{ fontWeight: 600 }}>
                {t('invite.replaceLabel')}
              </Typography>
              {admins.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  {t('invite.replaceNone')}
                </Typography>
              ) : (
                <FormGroup>
                  {admins.map((a) => (
                    <FormControlLabel
                      key={a.id}
                      control={
                        <Checkbox
                          checked={replaceIds.includes(a.id)}
                          onChange={(e) => toggleReplace(a.id, e.target.checked)}
                          disabled={submitting}
                        />
                      }
                      label={
                        <span>
                          {a.display_name} ({a.email})
                          {!a.is_active && (
                            <Chip size="small" label={t('invite.replaceInactive')} sx={{ ml: 1 }} />
                          )}
                        </span>
                      }
                    />
                  ))}
                </FormGroup>
              )}
              <FormHelperText>{t('invite.replaceHelp')}</FormHelperText>
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose} disabled={submitting}>
            {t('actions.cancel')}
          </Button>
          <Button type="submit" variant="contained" disabled={submitting}>
            {submitting ? <CircularProgress size={22} /> : t('invite.submitButton')}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};

export default OrgAdminInviteDialog;
