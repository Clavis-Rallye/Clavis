import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  Checkbox,
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
import { createInstanceOrganization, instanceApiErrorMessage } from '../../api/instance';
import { ApiError } from '../../api/client';
import type { InstanceOrganizationResponse } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import { slugFromName } from '../../utils/slugFromName';

const SLUG_RE = /^[a-z0-9-]+$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
// Fixed display/selection order. The first checked language is the
// invitation-mail language.
const LANGUAGES = ['de', 'en'] as const;

interface OrganizationCreateDialogProps {
  open: boolean;
  onSuccess: (org: InstanceOrganizationResponse) => void;
  onClose: () => void;
}

const OrganizationCreateDialog: React.FC<OrganizationCreateDialogProps> = ({
  open,
  onSuccess,
  onClose,
}) => {
  const { t } = useTranslation('instance');
  const fullScreen = useMediaQuery('(max-width:599px)');

  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  // Until the operator edits the slug field, every change of the name sets
  // the slug to the suggestion made from it.
  const [slugEdited, setSlugEdited] = useState(false);
  const [languages, setLanguages] = useState<Record<(typeof LANGUAGES)[number], boolean>>({
    de: true,
    en: false,
  });
  const [email, setEmail] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const resetForm = () => {
    setName('');
    setSlug('');
    setSlugEdited(false);
    setLanguages({ de: true, en: false });
    setEmail('');
    setFieldErrors({});
    setError(null);
  };

  const handleClose = () => {
    resetForm();
    onClose();
  };

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!name.trim()) errors.name = t('create.required');
    if (!slug.trim()) errors.slug = t('create.required');
    else if (!SLUG_RE.test(slug)) errors.slug = t('create.slugInvalid');
    if (!LANGUAGES.some((l) => languages[l])) errors.languages = t('create.required');
    if (!email.trim()) errors.email = t('create.required');
    else if (!EMAIL_RE.test(email)) errors.email = t('create.emailInvalid');
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    // An earlier server error must not outlive the next attempt, also when
    // that attempt stops at the client check.
    setError(null);
    if (!validate()) return;
    setSubmitting(true);
    try {
      const org = await createInstanceOrganization({
        name,
        slug,
        content_languages: LANGUAGES.filter((l) => languages[l]),
        first_admin_email: email,
      });
      resetForm();
      onSuccess(org);
    } catch (err) {
      // A coded error that belongs to one field shows at that field only,
      // not a second time in the alert above the form.
      if (err instanceof ApiError && err.errorCode === 'ORG_SLUG_TAKEN') {
        setFieldErrors((prev) => ({ ...prev, slug: t('errors.ORG_SLUG_TAKEN') }));
      } else if (
        err instanceof ApiError &&
        (err.errorCode === 'EMAIL_HAS_PENDING_INVITATION' || err.errorCode === 'EMAIL_HAS_ACCOUNT')
      ) {
        // An address with an account can never accept the invitation.
        setFieldErrors((prev) => ({ ...prev, email: t(`errors.${err.errorCode}`) }));
      } else {
        setError(instanceApiErrorMessage(err));
      }
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
      aria-labelledby="org-create-dialog-title"
    >
      <form onSubmit={(e) => void handleSubmit(e)} noValidate>
        <DialogTitle id="org-create-dialog-title">{t('create.title')}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {t('create.intro')}
          </Typography>
          {error && <ErrorAlert message={error} />}
          <TextField
            fullWidth
            label={t('create.nameLabel')}
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              if (!slugEdited) setSlug(slugFromName(e.target.value));
            }}
            margin="normal"
            required
            disabled={submitting}
            error={!!fieldErrors.name}
            helperText={fieldErrors.name}
            slotProps={{ formHelperText: { role: 'alert' } }}
          />
          <TextField
            fullWidth
            label={t('create.slugLabel')}
            value={slug}
            onChange={(e) => {
              setSlug(e.target.value);
              setSlugEdited(true);
            }}
            margin="normal"
            required
            disabled={submitting}
            error={!!fieldErrors.slug}
            helperText={fieldErrors.slug ?? t('create.slugHelper')}
            slotProps={{ formHelperText: { role: fieldErrors.slug ? 'alert' : undefined } }}
          />
          <FormGroup sx={{ mt: 1 }}>
            <Typography variant="body2" component="legend">
              {t('create.languagesLabel')}
            </Typography>
            {LANGUAGES.map((lang) => (
              <FormControlLabel
                key={lang}
                control={
                  <Checkbox
                    checked={languages[lang]}
                    onChange={(e) =>
                      setLanguages((prev) => ({ ...prev, [lang]: e.target.checked }))
                    }
                    disabled={submitting}
                  />
                }
                label={t(`create.language${lang === 'de' ? 'De' : 'En'}`)}
              />
            ))}
            <FormHelperText error={!!fieldErrors.languages} role={fieldErrors.languages ? 'alert' : undefined}>
              {fieldErrors.languages ?? t('create.languagesHelper')}
            </FormHelperText>
          </FormGroup>
          <TextField
            fullWidth
            label={t('create.emailLabel')}
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
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose} disabled={submitting}>
            {t('actions.cancel')}
          </Button>
          <Button type="submit" variant="contained" disabled={submitting}>
            {submitting ? <CircularProgress size={22} /> : t('create.submitButton')}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};

export default OrganizationCreateDialog;
