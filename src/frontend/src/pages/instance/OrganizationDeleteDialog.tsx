import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  TextField,
  useMediaQuery,
} from '@mui/material';
import { instanceApiErrorMessage, requestInstanceOrganizationDeletion } from '../../api/instance';
import { ApiError } from '../../api/client';
import type {
  InstanceOrganizationDeletionResponse,
  InstanceOrganizationListItem,
} from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';

interface OrganizationDeleteDialogProps {
  open: boolean;
  org: InstanceOrganizationListItem | null;
  onSuccess: (result: InstanceOrganizationDeletionResponse) => void;
  onClose: () => void;
}

/**
 * Schedules an archived organization's hard deletion.
 * The confirm field must match the organization's slug exactly before the
 * submit button enables — the backend re-checks it (422
 * `CONFIRMATION_MISMATCH`), but the client refuses the accidental submit
 * before that round trip.
 */
const OrganizationDeleteDialog: React.FC<OrganizationDeleteDialogProps> = ({
  open,
  org,
  onSuccess,
  onClose,
}) => {
  const { t } = useTranslation('instance');
  const fullScreen = useMediaQuery('(max-width:599px)');

  const [confirmSlug, setConfirmSlug] = useState('');
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (org) {
      setConfirmSlug('');
      setFieldError(null);
      setError(null);
    }
  }, [org]);

  const handleClose = () => {
    setConfirmSlug('');
    setFieldError(null);
    setError(null);
    onClose();
  };

  const canSubmit = org !== null && confirmSlug === org.slug;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!org || !canSubmit) return;
    setSubmitting(true);
    setError(null);
    setFieldError(null);
    try {
      const result = await requestInstanceOrganizationDeletion(org.slug, {
        confirm_slug: confirmSlug,
      });
      onSuccess(result);
    } catch (err) {
      if (err instanceof ApiError && err.errorCode === 'CONFIRMATION_MISMATCH') {
        setFieldError(t('errors.CONFIRMATION_MISMATCH'));
      }
      setError(instanceApiErrorMessage(err));
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
      aria-labelledby="org-delete-dialog-title"
    >
      <form onSubmit={(e) => void handleSubmit(e)} noValidate>
        <DialogTitle id="org-delete-dialog-title">{t('delete.title')}</DialogTitle>
        <DialogContent>
          {error && <ErrorAlert message={error} />}
          <DialogContentText sx={{ mb: 2 }}>
            {t('delete.message', { name: org.name })}
          </DialogContentText>
          <TextField
            fullWidth
            label={t('delete.confirmLabel')}
            value={confirmSlug}
            onChange={(e) => setConfirmSlug(e.target.value)}
            margin="normal"
            required
            disabled={submitting}
            error={!!fieldError}
            helperText={fieldError ?? t('delete.confirmHelper', { slug: org.slug })}
            slotProps={{ formHelperText: { role: fieldError ? 'alert' : undefined } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose} disabled={submitting}>
            {t('actions.cancel')}
          </Button>
          <Button type="submit" color="error" variant="contained" disabled={submitting || !canSubmit}>
            {submitting ? <CircularProgress size={22} /> : t('delete.submitButton')}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};

export default OrganizationDeleteDialog;
