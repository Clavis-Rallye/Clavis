import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Autocomplete,
  Box,
  FormControlLabel,
  Switch,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from '@mui/material';
import {
  getMailStatus,
  getMediaSummary,
  getOrg,
  getRetentionConfig,
  purgeOrgMedia,
  sendTestMail,
  updateOrg,
  updateRetentionConfig,
} from '../../api/admin';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type {
  MailStatusResponse,
  OrganizationResponse,
  OrganizationUpdateRequest,
  RetentionClass,
  RetentionConfigResponse,
} from '../../api/types';
import type { MediaPurgeType, MediaSummaryResponse } from '../../api/admin';
import { useOrg } from '../../contexts/OrgContext';
import { COMMON_LOCALES, REQUIRED_LOCALE, localeLabel } from '../../i18n/contentLocales';
import LoadingSpinner from '../../components/LoadingSpinner';
import RichTextEditor from '../../components/RichTextEditor';
import LegalTemplateTools from '../../components/LegalTemplateTools';
import type { LegalTemplateValues } from '../../utils/legalTemplates';
import { getGeocodeConfig, type GeocodeConfig } from '../../utils/geocode';
import { AUTO_START_SETTING } from '../../hooks/useAutoStartRuns';

const sameLanguages = (a: readonly string[], b: readonly string[]): boolean =>
  a.length === b.length && [...a].sort().join() === [...b].sort().join();

type TabKey = 'general' | 'legal' | 'retention' | 'media' | 'mail';

const RETENTION_ORDER: RetentionClass[] = ['LK_A', 'LK_B', 'LK_C', 'LK_H'];

const retentionDaysOf = (data: RetentionConfigResponse): Record<RetentionClass, string> => {
  const days = (cls: RetentionClass) => String(data.periods.find((p) => p.deletion_class === cls)?.days ?? '');
  return { LK_A: days('LK_A'), LK_B: days('LK_B'), LK_C: days('LK_C'), LK_H: days('LK_H') };
};

const MEDIA_TYPES: MediaPurgeType[] = ['photo', 'video', 'audio'];

const OrgSettings: React.FC = () => {
  const { t, i18n } = useTranslation('admin');
  const { user } = useAuth();
  const { refresh: refreshOrg } = useOrg();
  const orgSlug = user?.organization_slug ?? '';

  const [org, setOrg] = useState<OrganizationResponse | null>(null);
  const [name, setName] = useState('');
  const [languages, setLanguages] = useState<string[]>([]);
  const [legalImprint, setLegalImprint] = useState('');
  const [legalPrivacy, setLegalPrivacy] = useState('');
  const [legalAccessibility, setLegalAccessibility] = useState('');
  // The automatic start switch is a field of the general form. On until the
  // stored value says off, like the backend.
  const [autoStart, setAutoStart] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [tab, setTab] = useState<TabKey>('general');
  const [mailStatus, setMailStatus] = useState<MailStatusResponse | null>(null);
  const [testEmail, setTestEmail] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ type: 'success' | 'error'; text: string } | null>(
    null,
  );

  // Retention config: lazy-loaded when the retention tab or the
  // legal tab is first opened. The legal tab needs it for the template, which
  // states the periods.
  const [retention, setRetention] = useState<RetentionConfigResponse | null>(null);
  const [retentionDays, setRetentionDays] = useState<Record<RetentionClass, string>>({
    LK_A: '',
    LK_B: '',
    LK_C: '',
    LK_H: '',
  });
  const [retentionLoading, setRetentionLoading] = useState(false);

  // The tile server and address-search sources of the installation, for
  // section 7 of the privacy template. Asked when the legal
  // tab is first opened; the insert button waits for the answer, and a failed
  // fetch leaves the template's bracket placeholders instead of a provider.
  const [geocode, setGeocode] = useState<GeocodeConfig | null>(null);

  useEffect(() => {
    if (tab !== 'legal' || geocode) return;
    let cancelled = false;
    void getGeocodeConfig().then((config) => {
      if (cancelled) return;
      setGeocode(config);
      if (config.unknown) {
        setMessage({
          type: 'error',
          text: t('mapLoadFailed', { ns: 'legalTemplates' }),
        });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [tab, geocode, t]);

  // Media purge: counts load fresh on every visit of the
  // media tab; the purge dialog re-asks for the organization's slug each
  // time, so a bulk delete cannot ride on a stale confirmation.
  const [mediaSummary, setMediaSummary] = useState<MediaSummaryResponse | null>(null);
  const [mediaLoading, setMediaLoading] = useState(false);
  const [purgeTarget, setPurgeTarget] = useState<MediaPurgeType | null>(null);
  const [confirmSlug, setConfirmSlug] = useState('');
  const [purging, setPurging] = useState(false);
  const [purgeError, setPurgeError] = useState<string | null>(null);

  const loadMediaSummary = useCallback(async () => {
    if (!orgSlug) return;
    setMediaLoading(true);
    try {
      setMediaSummary(await getMediaSummary(orgSlug));
    } catch (err) {
      setMessage({ type: 'error', text: err instanceof ApiError ? err.detail : String(err) });
    } finally {
      setMediaLoading(false);
    }
  }, [orgSlug]);

  useEffect(() => {
    if (tab === 'media') void loadMediaSummary();
  }, [tab, loadMediaSummary]);

  const openPurgeDialog = (type: MediaPurgeType) => {
    setPurgeTarget(type);
    setConfirmSlug('');
    setPurgeError(null);
  };

  const closePurgeDialog = () => {
    setPurgeTarget(null);
    setConfirmSlug('');
    setPurgeError(null);
  };

  const handlePurge = async () => {
    if (!purgeTarget) return;
    setPurging(true);
    setPurgeError(null);
    try {
      const result = await purgeOrgMedia(orgSlug, {
        type: purgeTarget,
        confirm_slug: confirmSlug.trim(),
      });
      setMessage({ type: 'success', text: t('org.media.success', { count: result.purged }) });
      closePurgeDialog();
      await loadMediaSummary();
    } catch (err) {
      setPurgeError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setPurging(false);
    }
  };

  useEffect(() => {
    if ((tab !== 'retention' && tab !== 'legal') || !orgSlug || retention) return;
    let cancelled = false;
    const loadRetention = async () => {
      setRetentionLoading(true);
      try {
        const data = await getRetentionConfig(orgSlug);
        if (!cancelled) {
          setRetention(data);
          setRetentionDays(retentionDaysOf(data));
        }
      } catch (err) {
        // On the legal tab a failed load only costs the template its periods:
        // it then writes them as placeholders.
        if (!cancelled && tab === 'retention') {
          setMessage({ type: 'error', text: err instanceof ApiError ? err.detail : String(err) });
        }
      } finally {
        if (!cancelled) setRetentionLoading(false);
      }
    };
    void loadRetention();
    return () => {
      cancelled = true;
    };
  }, [tab, orgSlug, retention]);

  const applyRetention = (data: RetentionConfigResponse) => {
    setRetention(data);
    setRetentionDays(retentionDaysOf(data));
  };

  // What the privacy template states. The map values stand on their own: a
  // failed retention load keeps the tile and search wording, and periods
  // stay placeholders until the retention settings arrive.
  const templateValues: LegalTemplateValues | undefined = {
    ...(retention
      ? {
          retentionDays: Object.fromEntries(retention.periods.map((p) => [p.deletion_class, p.days])),
          backupDays: retention.backup_retention_days,
          backupDaysConfirmed: retention.backup_retention_source === 'instance',
          technicalLogDays: retention.technical_log_days,
          auditLogDays: retention.audit_log_days,
          abandonedRunDays: retention.abandoned_run_days,
          resultLinkDays: retention.result_link_days,
        }
      : {}),
    map: {
      tileUrl: geocode && !geocode.unknown ? geocode.tile_url : undefined,
      sources: geocode && !geocode.unknown ? geocode.sources : undefined,
    },
  };

  const handleSaveRetention = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setMessage(null);
    try {
      const updated = await updateRetentionConfig(orgSlug, {
        lk_a_days: Number(retentionDays.LK_A),
        lk_b_days: Number(retentionDays.LK_B),
        lk_c_days: Number(retentionDays.LK_C),
        lk_h_days: Number(retentionDays.LK_H),
      });
      applyRetention(updated);
      setMessage({ type: 'success', text: t('org.success') });
    } catch (err) {
      setMessage({ type: 'error', text: err instanceof ApiError ? err.detail : String(err) });
    } finally {
      setSaving(false);
    }
  };

  const handleResetRetention = async () => {
    setSaving(true);
    setMessage(null);
    try {
      const updated = await updateRetentionConfig(orgSlug, {
        lk_a_days: null,
        lk_b_days: null,
        lk_c_days: null,
        lk_h_days: null,
      });
      applyRetention(updated);
      setMessage({ type: 'success', text: t('org.success') });
    } catch (err) {
      setMessage({ type: 'error', text: err instanceof ApiError ? err.detail : String(err) });
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    if (!orgSlug) return;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const data = await getOrg(orgSlug);
        if (!cancelled) {
          setOrg(data);
          setName(data.name);
          setLanguages(data.languages.includes(REQUIRED_LOCALE) ? data.languages : [REQUIRED_LOCALE, ...data.languages]);
          setLegalImprint(data.legal_imprint ?? '');
          setLegalPrivacy(data.legal_privacy ?? '');
          setLegalAccessibility(data.legal_accessibility ?? '');
          setAutoStart(data.settings?.[AUTO_START_SETTING] !== false);
        }
      } catch (err) {
        if (!cancelled) {
          setMessage({
            type: 'error',
            text: err instanceof ApiError ? err.detail : String(err),
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);

  const langsChanged = !!org && !sameLanguages(languages, org.languages);

  // Planned runs start on their own unless this is off. Missing means on, like
  // the backend. The switch is saved with the form.
  const autoStartStored = org?.settings?.[AUTO_START_SETTING] !== false;
  const autoStartChanged = !!org && autoStart !== autoStartStored;
  const generalChanged = !!org && (name !== org.name || langsChanged || autoStartChanged);

  // Normalize an Autocomplete value list: trim/lowercase, dedupe, German first.
  const setLanguagesFrom = (values: readonly string[]) => {
    const seen = new Set<string>();
    const rest: string[] = [];
    for (const v of values) {
      const code = String(v).trim().toLowerCase();
      if (code && code !== REQUIRED_LOCALE && !seen.has(code)) {
        seen.add(code);
        rest.push(code);
      }
    }
    setLanguages([REQUIRED_LOCALE, ...rest]);
  };

  const handleSaveGeneral = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!org || !generalChanged) return;
    setSaving(true);
    setMessage(null);
    try {
      // The key is written only when the person touched the switch.
      const updated = await updateOrg(orgSlug, {
        name,
        languages,
        ...(autoStartChanged
          ? { settings: { ...(org.settings ?? {}), [AUTO_START_SETTING]: autoStart } }
          : {}),
      });
      setOrg(updated);
      setName(updated.name);
      setLanguages(updated.languages);
      setAutoStart(updated.settings?.[AUTO_START_SETTING] !== false);
      setMessage({ type: 'success', text: t('org.success') });
      refreshOrg();
    } catch (err) {
      setMessage({
        type: 'error',
        text: err instanceof ApiError ? err.detail : String(err),
      });
    } finally {
      setSaving(false);
    }
  };

  const handleSaveLegal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!org) return;
    setSaving(true);
    setMessage(null);
    // Send empty strings as null to clear an override and fall back to
    // the bundled copy. The API treats explicit null differently from
    // omitted keys (Pydantic model_fields_set).
    const toNull = (s: string): string | null => (s.trim() === '' ? null : s);
    // Only a text that differs from the value the page loaded goes into the
    // body. A stored text the person did not touch is not sent, so the server
    // does not rewrite it. Both routes keep an omitted text as it is.
    const body: Pick<OrganizationUpdateRequest, 'legal_imprint' | 'legal_privacy' | 'legal_accessibility'> = {};
    if (legalImprint !== (org.legal_imprint ?? '')) body.legal_imprint = toNull(legalImprint);
    if (legalPrivacy !== (org.legal_privacy ?? '')) body.legal_privacy = toNull(legalPrivacy);
    if (legalAccessibility !== (org.legal_accessibility ?? '')) body.legal_accessibility = toNull(legalAccessibility);
    try {
      const updated = await updateOrg(orgSlug, body);
      setOrg(updated);
      setLegalImprint(updated.legal_imprint ?? '');
      setLegalPrivacy(updated.legal_privacy ?? '');
      setLegalAccessibility(updated.legal_accessibility ?? '');
      setMessage({ type: 'success', text: t('org.success') });
    } catch (err) {
      setMessage({
        type: 'error',
        text: err instanceof ApiError ? err.detail : String(err),
      });
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    if (!orgSlug || tab !== 'mail') return;
    getMailStatus(orgSlug)
      .then(setMailStatus)
      .catch(() => setMailStatus({ enabled: false }));
  }, [orgSlug, tab]);

  if (loading) return <LoadingSpinner />;

  // The probe reports a refused message as a successful request with
  // `success: false` — the transport error is the answer, not an exception.
  const handleTestMail = async (e: React.FormEvent) => {
    e.preventDefault();
    setTestResult(null);
    setTesting(true);
    try {
      const result = await sendTestMail(orgSlug, {
        email: testEmail.trim(),
        language: i18n.resolvedLanguage === 'en' ? 'en' : 'de',
      });
      setTestResult(
        result.success
          ? { type: 'success', text: t('org.mail.testSuccess', { email: testEmail.trim() }) }
          : { type: 'error', text: t('org.mail.testFailed', { error: result.error ?? result.message }) },
      );
    } catch (err) {
      setTestResult({
        type: 'error',
        text: err instanceof ApiError ? err.detail : String(err),
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Box>
      {/* The German title is one long word. Its string carries a soft hyphen
          (U+00AD), so every browser breaks it at the same place with a hyphen.
          hyphens auto and overflow-wrap stay as the fallback for a still
          narrower window, where the two halves need a further break. */}
      <Typography
        variant="h2"
        component="h1"
        gutterBottom
        sx={{ overflowWrap: 'anywhere', hyphens: 'auto' }}
      >
        {t('org.title')}
      </Typography>

      {/* Scrollable so the last tabs stay reachable on a phone — with the
          standard variant they lay beyond the screen edge and cannot be
          focused. allowScrollButtonsMobile keeps the arrow buttons below sm. */}
      <Tabs
        value={tab}
        onChange={(_, next: TabKey) => {
          setTab(next);
          setMessage(null);
        }}
        variant="scrollable"
        scrollButtons="auto"
        allowScrollButtonsMobile
        sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
      >
        <Tab value="general" label={t('org.tabs.general', { defaultValue: 'Allgemein' })} />
        <Tab value="legal" label={t('org.tabs.legal', { defaultValue: 'Rechtliches' })} />
        <Tab value="retention" label={t('org.tabs.retention', { defaultValue: 'Löschfristen' })} />
        <Tab value="media" label={t('org.tabs.media', { defaultValue: 'Medien' })} />
        <Tab value="mail" label={t('org.tabs.mail', { defaultValue: 'E-Mail' })} />
      </Tabs>

      {tab === 'general' && (
        <Card>
          <CardContent>
            <form onSubmit={(e) => void handleSaveGeneral(e)}>
              <TextField
                fullWidth
                label={t('org.nameLabel')}
                value={name}
                onChange={(e) => setName(e.target.value)}
                margin="normal"
                required
                disabled={saving}
              />
              <TextField
                fullWidth
                label={t('org.slugLabel')}
                value={org?.slug ?? ''}
                margin="normal"
                disabled
                slotProps={{ input: { readOnly: true } }}
              />

              <Box sx={{ mt: 2 }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                  {t('org.languagesHelp')}
                </Typography>
                <Autocomplete<string, true, false, true>
                  multiple
                  freeSolo
                  disabled={saving}
                  options={COMMON_LOCALES.filter(
                    (c) => c !== REQUIRED_LOCALE && !languages.includes(c),
                  )}
                  value={languages.filter((c) => c !== REQUIRED_LOCALE)}
                  onChange={(_, values) => setLanguagesFrom(values)}
                  getOptionLabel={(code) => `${localeLabel(code, i18n.language)} (${code})`}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label={t('org.languagesLabel')}
                      placeholder={t('org.languagesAdd')}
                    />
                  )}
                />
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mt: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    {t('org.languagesRequired')}
                  </Typography>
                  <Chip
                    size="small"
                    label={`${localeLabel(REQUIRED_LOCALE, i18n.language)} (${REQUIRED_LOCALE})`}
                  />
                </Stack>
              </Box>

              <Box sx={{ mt: 3 }}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={autoStart}
                      onChange={(e) => setAutoStart(e.target.checked)}
                      disabled={saving || !org}
                      slotProps={{ input: { 'aria-describedby': 'org-auto-start-help' } }}
                    />
                  }
                  label={t('org.autoStartLabel')}
                />
                <Typography id="org-auto-start-help" variant="body2" color="text.secondary">
                  {autoStart ? t('org.autoStartHelpOn') : t('org.autoStartHelpOff')}
                </Typography>
              </Box>

              {message && (
                <Alert severity={message.type} sx={{ mt: 2 }}>
                  {message.text}
                </Alert>
              )}

              <Button
                type="submit"
                variant="contained"
                sx={{ mt: 2 }}
                disabled={saving || !org || !generalChanged}
              >
                {saving ? <CircularProgress size={22} /> : t('org.save')}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}

      {tab === 'legal' && (
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {t('org.legal.intro', {
                defaultValue:
                  'Hier kannst du die Rechtstexte deiner Organisation überschreiben. Felder, die leer bleiben, fallen auf den Text der Installation zurück.',
              })}
            </Typography>
            <form onSubmit={(e) => void handleSaveLegal(e)}>
              <Stack spacing={3}>
                <Box>
                  <Typography variant="subtitle2" component="label" sx={{ display: 'block', mb: 0.5 }}>
                    {t('org.legal.imprint', { defaultValue: 'Impressum' })}
                  </Typography>
                  <RichTextEditor
                    value={legalImprint}
                    onChange={setLegalImprint}
                    disabled={saving}
                    ariaLabel={t('org.legal.imprint', { defaultValue: 'Impressum' })}
                  />
                  <LegalTemplateTools
                    kind="imprint"
                    fieldLabel={t('org.legal.imprint', { defaultValue: 'Impressum' })}
                    value={legalImprint}
                    savedValue={org?.legal_imprint}
                    onInsert={setLegalImprint}
                    disabled={saving}
                  />
                </Box>
                <Box>
                  <Typography variant="subtitle2" component="label" sx={{ display: 'block', mb: 0.5 }}>
                    {t('org.legal.privacy', { defaultValue: 'Datenschutz' })}
                  </Typography>
                  <RichTextEditor
                    value={legalPrivacy}
                    onChange={setLegalPrivacy}
                    disabled={saving}
                    ariaLabel={t('org.legal.privacy', { defaultValue: 'Datenschutz' })}
                  />
                  <LegalTemplateTools
                    kind="privacy"
                    templateValues={templateValues}
                    fieldLabel={t('org.legal.privacy', { defaultValue: 'Datenschutz' })}
                    value={legalPrivacy}
                    savedValue={org?.legal_privacy}
                    onInsert={setLegalPrivacy}
                    disabled={saving || geocode === null}
                  />
                </Box>
                <Box>
                  <Typography variant="subtitle2" component="label" sx={{ display: 'block', mb: 0.5 }}>
                    {t('org.legal.accessibility', { defaultValue: 'Barrierefreiheit' })}
                  </Typography>
                  <RichTextEditor
                    value={legalAccessibility}
                    onChange={setLegalAccessibility}
                    disabled={saving}
                    ariaLabel={t('org.legal.accessibility', { defaultValue: 'Barrierefreiheit' })}
                  />
                  <LegalTemplateTools
                    kind="accessibility"
                    fieldLabel={t('org.legal.accessibility', { defaultValue: 'Barrierefreiheit' })}
                    value={legalAccessibility}
                    savedValue={org?.legal_accessibility}
                    onInsert={setLegalAccessibility}
                    disabled={saving}
                  />
                </Box>

                {message && (
                  <Alert severity={message.type}>{message.text}</Alert>
                )}

                <Box>
                  <Button type="submit" variant="contained" disabled={saving || !org}>
                    {saving ? <CircularProgress size={22} /> : t('org.save')}
                  </Button>
                </Box>
              </Stack>
            </form>
          </CardContent>
        </Card>
      )}

      {tab === 'retention' && (
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {t('org.retention.intro', {
                defaultValue:
                  'Löschfristen für personenbezogene Daten (DSGVO-Löschklassen). Der tägliche Löschjob wendet diese Fristen automatisch an. Zahlen wie Teams, Antworten und Punkte bleiben für die Statistik erhalten.',
              })}
            </Typography>
            {retentionLoading || !retention ? (
              <LoadingSpinner />
            ) : (
              <form onSubmit={(e) => void handleSaveRetention(e)}>
                <Stack spacing={3}>
                  {RETENTION_ORDER.map((cls) => {
                    const info = retention.periods.find((p) => p.deletion_class === cls);
                    if (!info) return null;
                    return (
                      <Box key={cls}>
                        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 0.5 }}>
                          <Typography variant="subtitle2" component="span">
                            {t(`org.retention.className.${cls}`, { defaultValue: cls })}
                          </Typography>
                          <Typography variant="caption" color="text.secondary" component="span">
                            {t('org.retention.classCode', { defaultValue: 'Löschklasse {{code}}', code: cls })}
                          </Typography>
                          {info.source !== 'default' && (
                            <Chip
                              size="small"
                              label={t(`org.retention.source.${info.source}`, {
                                defaultValue: info.source === 'override' ? 'Angepasst' : 'Standard der Installation',
                              })}
                            />
                          )}
                        </Stack>
                        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                          {t(`org.retention.classDesc.${cls}`, { defaultValue: info.description })}
                        </Typography>
                        <TextField
                          type="number"
                          label={t('org.retention.daysLabel', { defaultValue: 'Frist (Tage)' })}
                          value={retentionDays[cls]}
                          onChange={(e) =>
                            setRetentionDays((prev) => ({ ...prev, [cls]: e.target.value }))
                          }
                          disabled={saving}
                          required
                          slotProps={{ htmlInput: { min: retention.min_days, max: retention.max_days } }}
                          helperText={t('org.retention.bounds', {
                            defaultValue: `Zwischen ${retention.min_days} und ${retention.max_days} Tagen`,
                            min: retention.min_days,
                            max: retention.max_days,
                          })}
                        />
                      </Box>
                    );
                  })}

                  <Box>
                    {/* An h3 directly after the page's h1 would skip a level. The
                        component prop sets h2, the variant keeps the look. */}
                    <Typography variant="subtitle2" component="h2" sx={{ mb: 0.5 }}>
                      {t('org.retention.backup.title')}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {t(
                        retention.backup_retention_source === 'instance'
                          ? 'org.retention.backup.stated'
                          : 'org.retention.backup.assumed',
                        { count: retention.backup_retention_days },
                      )}
                    </Typography>
                  </Box>

                  <Typography variant="body2" color="text.secondary">
                    {t('org.retention.privacyHint')}
                  </Typography>

                  {message && <Alert severity={message.type}>{message.text}</Alert>}

                  <Stack direction="row" spacing={2}>
                    <Button type="submit" variant="contained" disabled={saving}>
                      {saving ? <CircularProgress size={22} /> : t('org.save')}
                    </Button>
                    <Button
                      type="button"
                      variant="outlined"
                      disabled={saving}
                      onClick={() => void handleResetRetention()}
                    >
                      {t('org.retention.reset', { defaultValue: 'Auf Standard zurücksetzen' })}
                    </Button>
                  </Stack>
                </Stack>
              </form>
            )}
          </CardContent>
        </Card>
      )}

      {tab === 'media' && (
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {t('org.media.intro')}
            </Typography>
            {mediaLoading && !mediaSummary ? (
              <LoadingSpinner />
            ) : mediaSummary ? (
              <Stack spacing={2} divider={<Box sx={{ borderTop: 1, borderColor: 'divider' }} />}>
                {MEDIA_TYPES.map((type) => (
                  <Stack
                    key={type}
                    direction="row"
                    spacing={2}
                    sx={{ alignItems: 'center', justifyContent: 'space-between' }}
                  >
                    <Box>
                      <Typography variant="subtitle2" component="span">
                        {t(`org.media.type.${type}`)}
                      </Typography>{' '}
                      <Typography variant="body2" color="text.secondary" component="span">
                        {t('org.media.count', { count: mediaSummary[type] })}
                      </Typography>
                    </Box>
                    <Button
                      variant="outlined"
                      color="error"
                      disabled={mediaSummary[type] === 0}
                      onClick={() => openPurgeDialog(type)}
                    >
                      {t('org.media.purgeButton', { type: t(`org.media.type.${type}`) })}
                    </Button>
                  </Stack>
                ))}
                {message && <Alert severity={message.type}>{message.text}</Alert>}
              </Stack>
            ) : null}
          </CardContent>
        </Card>
      )}

      {tab === 'mail' && (
        <Card>
          <CardContent>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {t('org.mail.description')}
            </Typography>

            {mailStatus === null ? (
              <LoadingSpinner />
            ) : (
              <Stack spacing={2}>
                <Alert severity={mailStatus.enabled ? 'success' : 'warning'}>
                  {mailStatus.enabled
                    ? t('org.mail.enabled', { from: mailStatus.from_address ?? '' })
                    : t('org.mail.disabled')}
                  {mailStatus.enabled && mailStatus.reply_to && (
                    <>
                      <br />
                      {t('org.mail.replyTo', { address: mailStatus.reply_to })}
                    </>
                  )}
                </Alert>

                <form onSubmit={(e) => void handleTestMail(e)}>
                  <Stack spacing={2}>
                    <TextField
                      fullWidth
                      type="email"
                      label={t('org.mail.testLabel')}
                      value={testEmail}
                      onChange={(e) => setTestEmail(e.target.value)}
                      disabled={testing || !mailStatus.enabled}
                      required
                      autoComplete="email"
                    />
                    {testResult && <Alert severity={testResult.type}>{testResult.text}</Alert>}
                    <Box>
                      <Button
                        type="submit"
                        variant="contained"
                        disabled={testing || !mailStatus.enabled || !testEmail.trim()}
                      >
                        {testing ? <CircularProgress size={22} /> : t('org.mail.testButton')}
                      </Button>
                    </Box>
                  </Stack>
                </form>
              </Stack>
            )}
          </CardContent>
        </Card>
      )}

      <Dialog open={purgeTarget !== null} onClose={closePurgeDialog} fullWidth maxWidth="sm">
        <DialogTitle>
          {t('org.media.dialogTitle', {
            type: purgeTarget ? t(`org.media.type.${purgeTarget}`) : '',
          })}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 1 }}>
            {t('org.media.dialogWhat', {
              type: purgeTarget ? t(`org.media.type.${purgeTarget}`) : '',
            })}
          </DialogContentText>
          <DialogContentText sx={{ mb: 2 }}>{t('org.media.dialogScores')}</DialogContentText>
          {purgeError && <Alert severity="error" sx={{ mb: 2 }}>{purgeError}</Alert>}
          <TextField
            fullWidth
            label={t('org.media.dialogSlugLabel')}
            value={confirmSlug}
            onChange={(e) => setConfirmSlug(e.target.value)}
            disabled={purging}
            helperText={org?.slug}
            autoComplete="off"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={closePurgeDialog} disabled={purging}>
            {t('org.media.dialogCancel')}
          </Button>
          <Button
            color="error"
            variant="contained"
            disabled={purging || !org || confirmSlug.trim() !== org.slug}
            onClick={() => void handlePurge()}
          >
            {purging ? <CircularProgress size={22} /> : t('org.media.dialogConfirm')}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default OrgSettings;
