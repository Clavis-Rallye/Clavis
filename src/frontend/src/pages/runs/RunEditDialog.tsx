import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
} from '@mui/material';
import { updateRun } from '../../api/runs';
import type { Run } from '../../api/types';
import ErrorAlert from '../../components/ErrorAlert';
import { userErrorMessage } from '../../components/admin/userErrors';

interface RunEditDialogProps {
  open: boolean;
  orgSlug: string;
  run: Run;
  onSaved: () => void;
  onClose: () => void;
}

/** "YYYY-MM-DDTHH:mm" in local time, the value a datetime-local field takes. */
function toLocalInput(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Name, team limit and start time of a run before its start. The API allows
 * it while the run is "Konfiguriert" or "Geplant". An empty team limit means
 * no limit, an empty start time means a manual start.
 */
const RunEditDialog: React.FC<RunEditDialogProps> = ({ open, orgSlug, run, onSaved, onClose }) => {
  const { t } = useTranslation(['runs', 'common', 'admin']);
  const [name, setName] = useState(run.name);
  const [maxTeams, setMaxTeams] = useState(run.max_teams ? String(run.max_teams) : '');
  const [scheduledFor, setScheduledFor] = useState(toLocalInput(run.scheduled_for));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nameMissing = !name.trim();
  const limitInvalid = maxTeams !== '' && !(Number(maxTeams) >= 1 && Number.isInteger(Number(maxTeams)));

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (nameMissing || limitInvalid) return;
    setSaving(true);
    setError(null);
    try {
      await updateRun(orgSlug, run.id, {
        name: name.trim(),
        max_teams: maxTeams === '' ? null : Number(maxTeams),
        scheduled_for: scheduledFor ? new Date(scheduledFor).toISOString() : null,
      });
      onSaved();
    } catch (err) {
      setError(userErrorMessage(err, t));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="xs" fullWidth aria-labelledby="run-edit-title">
      <form onSubmit={(e) => void handleSave(e)} noValidate>
        <DialogTitle id="run-edit-title">{t('dashboard.edit.title')}</DialogTitle>
        <DialogContent>
          {error && <ErrorAlert message={error} />}
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              label={t('create.nameLabel')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              error={nameMissing}
              helperText={nameMissing ? t('common:validation.required', { defaultValue: '' }) : undefined}
              disabled={saving}
              fullWidth
            />
            <TextField
              label={t('create.maxTeams')}
              type="number"
              value={maxTeams}
              onChange={(e) => setMaxTeams(e.target.value)}
              error={limitInvalid}
              helperText={t('dashboard.edit.maxTeamsHelper')}
              slotProps={{ htmlInput: { min: 1 } }}
              disabled={saving}
              fullWidth
            />
            <TextField
              label={t('create.scheduledStart')}
              type="datetime-local"
              value={scheduledFor}
              onChange={(e) => setScheduledFor(e.target.value)}
              helperText={t('dashboard.edit.scheduledHelper')}
              slotProps={{ inputLabel: { shrink: true } }}
              disabled={saving}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose} disabled={saving}>
            {t('common:actions.cancel')}
          </Button>
          <Button type="submit" variant="contained" disabled={saving || nameMissing || limitInvalid}>
            {saving ? <CircularProgress size={22} /> : t('dashboard.edit.save')}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
};

export default RunEditDialog;
