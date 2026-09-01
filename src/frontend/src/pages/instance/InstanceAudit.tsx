import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Alert, Link as MuiLink, Typography } from '@mui/material';
import { Link as RouterLink } from 'react-router';
import InstanceShell from './InstanceShell';
import { useInstanceAuth } from './useInstanceAuth';
import { instanceApiErrorMessage, listInstanceAuditLog } from '../../api/instance';
import type { InstanceAuditLogEntryResponse } from '../../api/types';
import DataTable, {
  type DataTableColumn,
  type DataTableFilter,
  type DataTableQuery,
} from '../../components/DataTable';
import LoadingSpinner from '../../components/LoadingSpinner';

// Every `instance.*` event the backend emits (routers/instance.py,
// routers/instance_auth.py, routers/invitations.py, services/org_deletion.py,
// cli.py), as the option list of the filter dropdown.
const EVENT_TYPES = [
  'instance.login',
  'instance.login_failed',
  'instance.password_changed',
  'instance.org_created',
  'instance.org_updated',
  'instance.org_archived',
  'instance.org_restored',
  'instance.org_admin_invited',
  'instance.org_admins_viewed',
  'instance.org_admin_takeover',
  'instance.legal_updated',
  'instance.org_deletion_requested',
  'instance.org_deletion_cancelled',
  'instance.org_deleted',
  'instance.operator_created',
  'instance.operator_disabled',
  'instance.operator_enabled',
  'instance.operator_totp_reset',
  'instance.operator_password_set',
];

const rowId = (e: InstanceAuditLogEntryResponse) =>
  `${e.timestamp ?? ''}-${e.event ?? ''}-${e.operator_id ?? ''}-${e.target_slug ?? ''}`;

const InstanceAudit: React.FC = () => {
  const { t, i18n } = useTranslation('instance');
  const { status, signOut } = useInstanceAuth();
  // A German label per event, like the organization log. An event
  // this list does not know yet shows its code instead of nothing.
  const eventLabel = (event: string) =>
    t(`audit.events.${event.replace(/^instance\./, '')}`, { defaultValue: event });

  const [entries, setEntries] = useState<InstanceAuditLogEntryResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  // Server mode: the table emits the query, the page keeps the last one and
  // loads with it, same pattern as StationLibraryList.
  const [query, setQuery] = useState<DataTableQuery | null>(null);
  // Drops responses that arrive after a newer request was issued.
  const requestRef = useRef(0);

  const load = useCallback(async (q: DataTableQuery) => {
    const requestId = ++requestRef.current;
    setLoading(true);
    setError(null);
    try {
      const resp = await listInstanceAuditLog({
        event: q.filters.event,
        offset: q.page * q.rowsPerPage,
        limit: q.rowsPerPage,
      });
      if (requestId !== requestRef.current) return;
      setEntries(resp.items);
      setTotal(resp.total);
    } catch (err) {
      if (requestId !== requestRef.current) return;
      setError(instanceApiErrorMessage(err));
    } finally {
      if (requestId === requestRef.current) setLoading(false);
    }
  }, []);

  const reload = useCallback(() => {
    if (query) void load(query);
  }, [query, load]);

  const reloadRef = useRef(reload);
  useEffect(() => {
    reloadRef.current = reload;
  }, [reload]);

  useEffect(() => {
    if (query) void reloadRef.current();
  }, [query, load]);

  const handleSignOut = async () => {
    setSignOutError(null);
    try {
      await signOut();
    } catch (err) {
      setSignOutError(instanceApiErrorMessage(err));
    }
  };

  const columns: DataTableColumn<InstanceAuditLogEntryResponse>[] = [
    {
      id: 'timestamp',
      header: t('audit.table.timestamp'),
      // With seconds, as the log of an organization shows it (LogViewer).
      render: (e) => (e.timestamp ? new Date(e.timestamp).toLocaleString(i18n.language) : '—'),
    },
    {
      id: 'event',
      header: t('audit.table.event'),
      render: (e) => (e.event ? eventLabel(e.event) : '—'),
    },
    {
      id: 'operator',
      header: t('audit.table.operator'),
      // The scheduled deletion runs without an operator: it is the system.
      // A server command says so.
      render: (e) =>
        e.operator_email ??
        (e.operator_id
          ? `${e.operator_id.slice(0, 8)}…`
          : e.actor_type === 'cli'
            ? t('audit.cliActor')
            : // A failed sign-in has no operator yet: somebody tried,
              // not the system.
              e.event === 'instance.login_failed'
              ? t('audit.anonymousActor')
              : t('audit.systemActor')),
    },
    {
      id: 'target',
      header: t('audit.table.target'),
      render: (e) => e.target_slug ?? e.target_email ?? '—',
    },
  ];

  const filters: DataTableFilter<InstanceAuditLogEntryResponse>[] = [
    {
      id: 'event',
      label: t('audit.filters.event'),
      options: EVENT_TYPES.map((event) => ({ value: event, label: eventLabel(event) })),
    },
  ];

  if (status === 'loading' || status === 'unauthenticated') {
    return (
      <InstanceShell title={t('audit.title')}>
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
    <InstanceShell title={t('audit.title')} onSignOut={() => void handleSignOut()} maxWidth="lg">
      {signOutError && (
        <Alert severity="error" role="alert" onClose={() => setSignOutError(null)} sx={{ mb: 2 }}>
          {signOutError}
        </Alert>
      )}
      <DataTable<InstanceAuditLogEntryResponse>
        rows={entries}
        columns={columns}
        getRowId={rowId}
        loading={loading}
        error={error}
        onRetry={reload}
        emptyMessage={t('audit.empty')}
        searchable={false}
        filters={filters}
        server={{ total, onQueryChange: setQuery }}
        ariaLabel={t('audit.title')}
      />
    </InstanceShell>
  );
};

export default InstanceAudit;
