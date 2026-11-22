import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Box, Button, CircularProgress, Link as MuiLink, Stack, TextField, Typography } from '@mui/material';
import { Link as RouterLink } from 'react-router';
import InstanceShell from './InstanceShell';
import { useInstanceAuth } from './useInstanceAuth';
import { getInstanceLegal, instanceApiErrorMessage, updateInstanceLegal } from '../../api/instance';
import LoadingSpinner from '../../components/LoadingSpinner';
import RichTextEditor from '../../components/RichTextEditor';
import LegalTemplateTools from '../../components/LegalTemplateTools';
import type { InstanceLegalResponse, InstanceLegalUpdateRequest } from '../../api/types';

// Sends an empty field back as null to clear an override — the same
// discipline OrgSettings.tsx uses for the organization's own legal texts.
const toNull = (s: string): string | null => (s.trim() === '' ? null : s);

const InstanceLegal: React.FC = () => {
  const { t } = useTranslation('instance');
  const { status, signOut } = useInstanceAuth();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [legalImprint, setLegalImprint] = useState('');
  const [legalPrivacy, setLegalPrivacy] = useState('');
  const [legalAccessibility, setLegalAccessibility] = useState('');
  // Days a backup can still hold deleted data. The privacy template states it.
  const [backupDays, setBackupDays] = useState('');
  // The texts as the server holds them, for the warning about placeholders
  // that are already published. The same answer carries the tile server and
  // the address-search sources of section 7 — /api/geocode/config would
  // refuse an instance-only operator session.
  const [saved, setSaved] = useState<InstanceLegalResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  useEffect(() => {
    if (status !== 'authenticated') return;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setLoadError(null);
      try {
        const data = await getInstanceLegal();
        if (cancelled) return;
        setSaved(data);
        setLegalImprint(data.legal_imprint ?? '');
        setLegalPrivacy(data.legal_privacy ?? '');
        setLegalAccessibility(data.legal_accessibility ?? '');
        setBackupDays(String(data.backup_retention_days));
      } catch (err) {
        if (!cancelled) setLoadError(instanceApiErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [status]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      // InstanceLegalUpdateRequest (api/types.ts) does not list the backup
      // period, so the body type adds it here.
      const body: InstanceLegalUpdateRequest & { backup_retention_days?: number | null } = {};
      // Only a value that differs from the one the page loaded is sent. A
      // stored text the operator did not touch is not rewritten, and the route
      // keeps an omitted field as it is.
      if (legalImprint !== (saved?.legal_imprint ?? '')) body.legal_imprint = toNull(legalImprint);
      if (legalPrivacy !== (saved?.legal_privacy ?? '')) body.legal_privacy = toNull(legalPrivacy);
      if (legalAccessibility !== (saved?.legal_accessibility ?? '')) {
        body.legal_accessibility = toNull(legalAccessibility);
      }
      // A number the operator changes is their statement, also when it equals
      // the default. An empty field states nothing (null).
      if (saved && backupDays !== String(saved.backup_retention_days)) {
        body.backup_retention_days = backupDays.trim() === '' ? null : Number(backupDays);
      }
      const updated = await updateInstanceLegal(body);
      setSaved(updated);
      setLegalImprint(updated.legal_imprint ?? '');
      setLegalPrivacy(updated.legal_privacy ?? '');
      setLegalAccessibility(updated.legal_accessibility ?? '');
      setBackupDays(String(updated.backup_retention_days));
      setMessage({ type: 'success', text: t('legal.saveSuccess') });
    } catch (err) {
      setMessage({ type: 'error', text: instanceApiErrorMessage(err) });
    } finally {
      setSaving(false);
    }
  };

  const handleSignOut = async () => {
    setSignOutError(null);
    try {
      await signOut();
    } catch (err) {
      setSignOutError(instanceApiErrorMessage(err));
    }
  };

  if (status === 'loading' || status === 'unauthenticated') {
    return (
      <InstanceShell title={t('legal.title')}>
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
    <InstanceShell title={t('legal.title')} onSignOut={() => void handleSignOut()} maxWidth="md">
      {signOutError && (
        <Alert severity="error" role="alert" onClose={() => setSignOutError(null)} sx={{ mb: 2 }}>
          {signOutError}
        </Alert>
      )}
      {loading ? (
        <LoadingSpinner />
      ) : loadError ? (
        <Alert severity="error" role="alert">
          {loadError}
        </Alert>
      ) : (
        <Box>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            {t('legal.intro')}
          </Typography>
          <form onSubmit={(e) => void handleSave(e)}>
            <Stack spacing={3}>
              <Box>
                <Typography variant="subtitle2" component="h2" sx={{ mb: 0.5 }}>
                  {t('legal.backup.title')}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                  {t('legal.backup.intro')}
                </Typography>
                <TextField
                  type="number"
                  label={t('legal.backup.daysLabel')}
                  value={backupDays}
                  onChange={(e) => setBackupDays(e.target.value)}
                  disabled={saving}
                  slotProps={{ htmlInput: { min: 1, max: 3650 } }}
                  helperText={t(
                    saved?.backup_retention_source === 'instance' ? 'legal.backup.stated' : 'legal.backup.notStated',
                  )}
                />
              </Box>
              <Box>
                <Typography variant="subtitle2" component="label" sx={{ display: 'block', mb: 0.5 }}>
                  {t('legal.imprint')}
                </Typography>
                <RichTextEditor
                  value={legalImprint}
                  onChange={setLegalImprint}
                  disabled={saving}
                  ariaLabel={t('legal.imprint')}
                  allowImages={false}
                />
                <LegalTemplateTools
                  kind="imprint"
                  fieldLabel={t('legal.imprint')}
                  value={legalImprint}
                  savedValue={saved?.legal_imprint}
                  onInsert={setLegalImprint}
                  disabled={saving}
                />
              </Box>
              <Box>
                <Typography variant="subtitle2" component="label" sx={{ display: 'block', mb: 0.5 }}>
                  {t('legal.privacy')}
                </Typography>
                <RichTextEditor
                  value={legalPrivacy}
                  onChange={setLegalPrivacy}
                  disabled={saving}
                  ariaLabel={t('legal.privacy')}
                  allowImages={false}
                />
                <LegalTemplateTools
                  kind="privacy"
                  templateValues={
                    saved
                      ? {
                          retentionDays: saved.retention_days,
                          backupDays: saved.backup_retention_days,
                          backupDaysConfirmed: saved.backup_retention_source === 'instance',
                          technicalLogDays: saved.technical_log_days,
                          auditLogDays: saved.audit_log_days,
                          abandonedRunDays: saved.abandoned_run_days,
                          resultLinkDays: saved.result_link_days,
                          map: { tileUrl: saved.map_tile_url, sources: saved.address_search_sources },
                        }
                      : undefined
                  }
                  fieldLabel={t('legal.privacy')}
                  value={legalPrivacy}
                  savedValue={saved?.legal_privacy}
                  onInsert={setLegalPrivacy}
                  disabled={saving}
                />
              </Box>
              <Box>
                <Typography variant="subtitle2" component="label" sx={{ display: 'block', mb: 0.5 }}>
                  {t('legal.accessibility')}
                </Typography>
                <RichTextEditor
                  value={legalAccessibility}
                  onChange={setLegalAccessibility}
                  disabled={saving}
                  ariaLabel={t('legal.accessibility')}
                  allowImages={false}
                />
                <LegalTemplateTools
                  kind="accessibility"
                  fieldLabel={t('legal.accessibility')}
                  value={legalAccessibility}
                  savedValue={saved?.legal_accessibility}
                  onInsert={setLegalAccessibility}
                  disabled={saving}
                />
              </Box>

              {message && (
                <Alert severity={message.type} role={message.type === 'error' ? 'alert' : undefined}>
                  {message.text}
                </Alert>
              )}

              <Button type="submit" variant="contained" disabled={saving} sx={{ alignSelf: 'flex-start' }}>
                {saving ? <CircularProgress size={22} /> : t('legal.saveButton')}
              </Button>
            </Stack>
          </form>
        </Box>
      )}
    </InstanceShell>
  );
};

export default InstanceLegal;
