import React, { useEffect, useState } from 'react';
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
import { instanceApiErrorMessage, updateInstanceOrganization } from '../../api/instance';
import { ApiError } from '../../api/client';
import type { InstanceOrganizationListItem, InstanceOrganizationResponse } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import { localeLabel } from '../../i18n/contentLocales';

// Every language the app supports, used only to offer a checkbox for a
// language the organization does not carry. The saved order is `languages`
// below, not this constant, which never reorders the organization's own list.
// That order later decides the language of the admin invitation mail
// (`content_languages[0]` in `routers/instance.py`).
const KNOWN_LANGUAGES = ['de', 'en'] as const;

interface OrganizationEditDialogProps {
  open: boolean;
  org: InstanceOrganizationListItem | null;
  onSuccess: (org: InstanceOrganizationResponse) => void;
  onClose: () => void;
}

const OrganizationEditDialog: React.FC<OrganizationEditDialogProps> = ({
  open,
  org,
  onSuccess,
  onClose,
}) => {
  const { t, i18n } = useTranslation('instance');
  const fullScreen = useMediaQuery('(max-width:599px)');

  const [name, setName] = useState('');
  // Ordered, checked only: exactly what is sent as content_languages.
  // Unchecking removes a language from its place, and checking appends it at
  // the end, so the organization's order is never reshuffled.
  const [languages, setLanguages] = useState<string[]>([]);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // The slug never changes here. Everything else reloads from the row
  // whenever a new organization is opened for editing.
  useEffect(() => {
    if (org) {
      setName(org.name);
      setLanguages([...org.content_languages]);
      setFieldErrors({});
      setError(null);
    }
  }, [org]);

  // Checked languages first, in their saved order, then any language the
  // organization does not carry, so the operator always sees which language
  // is first without the list jumping around.
  const displayLanguages = [
    ...languages,
    ...KNOWN_LANGUAGES.filter((l) => !languages.includes(l)),
  ];

  const toggleLanguage = (lang: string, checked: boolean) => {
    setLanguages((prev) =>
      checked ? (prev.includes(lang) ? prev : [...prev, lang]) : prev.filter((l) => l !== lang),
    );
  };

  const handleClose = () => {
    setFieldErrors({});
    setError(null);
    onClose();
  };

  const validate = (): boolean => {
    const errors: Record<string, string> = {};
    if (!name.trim()) errors.name = t('edit.required');
    if (languages.length === 0) errors.languages = t('edit.required');
    setFieldErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!org) return;
    // An earlier server error must not outlive the next attempt, also when
    // that attempt stops at the client check.
    setError(null);
    if (!validate()) return;
    setSubmitting(true);
    try {
      const updated = await updateInstanceOrganization(org.slug, {
        name,
        content_languages: languages,
      });
      onSuccess(updated);
    } catch (err) {
      if (err instanceof ApiError && err.errorCode === 'ORG_NAME_BLANK') {
        setFieldErrors((prev) => ({ ...prev, name: t('errors.ORG_NAME_BLANK') }));
      } else if (
        err instanceof ApiError &&
        (err.errorCode === 'ORG_LANGUAGES_EMPTY' || err.errorCode === 'ORG_LANGUAGE_UNSUPPORTED')
      ) {
        setFieldErrors((prev) => ({ ...prev, languages: t(`errors.${err.errorCode}`) }));
      } else {
        // Only an error without a field of its own goes into the alert.
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
      aria-labelledby="org-edit-dialog-title"
    >
      <form onSubmit={(e) => void handleSubmit(e)} noValidate>
        <DialogTitle id="org-edit-dialog-title">{t('edit.title')}</DialogTitle>
        <DialogContent>
          {error && <ErrorAlert message={error} />}
          <TextField
            fullWidth
            label={t('edit.nameLabel')}
            value={name}
            onChange={(e) => setName(e.target.value)}
            margin="normal"
            required
            disabled={submitting}
            error={!!fieldErrors.name}
            helperText={fieldErrors.name}
            slotProps={{ formHelperText: { role: 'alert' } }}
          />
          <TextField
            fullWidth
            label={t('edit.slugLabel')}
            value={org.slug}
            margin="normal"
            disabled
            helperText={t('edit.slugHelper')}
            slotProps={{ input: { readOnly: true } }}
          />
          <FormGroup sx={{ mt: 1 }}>
            <Typography variant="body2" component="legend">
              {t('edit.languagesLabel')}
            </Typography>
            {displayLanguages.map((lang) => (
              <FormControlLabel
                key={lang}
                control={
                  <Checkbox
                    checked={languages.includes(lang)}
                    onChange={(e) => toggleLanguage(lang, e.target.checked)}
                    disabled={submitting}
                  />
                }
                // Every code by its own name: the organization's admin may
                // carry languages beyond de and en. Without it, hi and th would
                // show as two more "Englisch" boxes.
                label={localeLabel(lang, i18n.language)}
              />
            ))}
            <FormHelperText error={!!fieldErrors.languages} role={fieldErrors.languages ? 'alert' : undefined}>
              {fieldErrors.languages ?? t('edit.languagesHelper')}
            </FormHelperText>
          </FormGroup>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleClose} disabled={submitting}>
            {t('actions.cancel')}
          </Button>
          <Button type="submit" variant="contained" disabled={submitting}>
            {submitting ? <CircularProgress size={22} /> : t('edit.submitButton')}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};

export default OrganizationEditDialog;
