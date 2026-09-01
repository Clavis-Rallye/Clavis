import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  Chip,
  FormControlLabel,
  IconButton,
  Link as MuiLink,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material';
import { Link as RouterLink } from 'react-router';
import AddIcon from '@mui/icons-material/Add';
import ArchiveIcon from '@mui/icons-material/Archive';
import DeleteForeverIcon from '@mui/icons-material/DeleteForever';
import EditIcon from '@mui/icons-material/Edit';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import UnarchiveIcon from '@mui/icons-material/Unarchive';
import InstanceShell from './InstanceShell';
import { useInstanceAuth } from './useInstanceAuth';
import {
  archiveInstanceOrganization,
  instanceApiErrorMessage,
  listInstanceOrganizations,
  restoreInstanceOrganization,
} from '../../api/instance';
import type {
  InstanceOrganizationDeletionResponse,
  InstanceOrganizationListItem,
  InstanceOrganizationResponse,
  InstanceOrgAdminInvitationResponse,
} from '../../api/types';
import DataTable, { type DataTableColumn } from '../../components/DataTable';
import ConfirmDialog from '../../components/ConfirmDialog';
import LoadingSpinner from '../../components/LoadingSpinner';
import OrganizationCreateDialog from './OrganizationCreateDialog';
import OrganizationEditDialog from './OrganizationEditDialog';
import OrganizationDeleteDialog from './OrganizationDeleteDialog';
import OrgAdminInviteDialog from './OrgAdminInviteDialog';
import { formatDate, formatDateTime } from '../../utils/formatDate';

const InstanceOrganizations: React.FC = () => {
  const { t, i18n } = useTranslation('instance');
  const { status, signOut } = useInstanceAuth();

  const [orgs, setOrgs] = useState<InstanceOrganizationListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<InstanceOrganizationListItem | null>(null);
  const [inviteTarget, setInviteTarget] = useState<InstanceOrganizationListItem | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<InstanceOrganizationListItem | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<InstanceOrganizationListItem | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<InstanceOrganizationListItem | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  // Shown inside whichever confirm dialog is open — a page-level alert would
  // sit behind the modal's aria-hidden backdrop, invisible to the operator.
  const [actionError, setActionError] = useState<string | null>(null);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listInstanceOrganizations();
      setOrgs(data.items);
    } catch (err) {
      setError(instanceApiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (status === 'authenticated') void load();
  }, [status, load]);

  const handleCreateSuccess = (org: InstanceOrganizationResponse) => {
    setCreateOpen(false);
    setSuccessMessage(t('organizations.createSuccess', { name: org.name }));
    void load();
  };

  const handleEditSuccess = (org: InstanceOrganizationResponse) => {
    setEditTarget(null);
    setSuccessMessage(t('organizations.editSuccess', { name: org.name }));
    void load();
  };

  const handleInviteSuccess = (invitation: InstanceOrgAdminInvitationResponse) => {
    setInviteTarget(null);
    setSuccessMessage(t('organizations.inviteSuccess', { email: invitation.email }));
    void load();
  };

  const handleArchive = async () => {
    if (!archiveTarget) return;
    setActionLoading(true);
    setActionError(null);
    try {
      await archiveInstanceOrganization(archiveTarget.slug);
      setSuccessMessage(t('organizations.archiveSuccess', { name: archiveTarget.name }));
      setArchiveTarget(null);
      void load();
    } catch (err) {
      setActionError(instanceApiErrorMessage(err));
    } finally {
      setActionLoading(false);
    }
  };

  const handleRestore = async () => {
    if (!restoreTarget) return;
    setActionLoading(true);
    setActionError(null);
    try {
      await restoreInstanceOrganization(restoreTarget.slug);
      setSuccessMessage(t('organizations.restoreSuccess', { name: restoreTarget.name }));
      setRestoreTarget(null);
      void load();
    } catch (err) {
      setActionError(instanceApiErrorMessage(err));
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteSuccess = (result: InstanceOrganizationDeletionResponse) => {
    // The message names the organization, not its slug.
    const name = deleteTarget?.name ?? result.slug;
    setDeleteTarget(null);
    setSuccessMessage(
      t('organizations.deleteSuccess', {
        name,
        date: formatDate(result.deletion_due_at, i18n.language),
      }),
    );
    void load();
  };

  const handleSignOut = async () => {
    setSignOutError(null);
    try {
      await signOut();
    } catch (err) {
      setSignOutError(instanceApiErrorMessage(err));
    }
  };

  // Order: what the operator must see before an archive, the
  // state and the active runs, sits right after the name. The window at
  // 1284 px shows it without a sideways scroll, and the actions stay at the
  // right edge while the rest of the table scrolls inside its own frame.
  const columns: DataTableColumn<InstanceOrganizationListItem>[] = [
    {
      id: 'name',
      header: t('organizations.table.name'),
      // The slug rides under the name: it is what the operator types in the
      // CLI and in a delete dialog, but it does not need a column of its own.
      render: (o) => (
        <Box>
          <Box component="span" sx={{ display: 'block' }}>{o.name}</Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {o.slug}
          </Typography>
        </Box>
      ),
      sortValue: (o) => o.name,
      searchValue: (o) => `${o.name} ${o.slug}`,
    },
    {
      id: 'status',
      header: t('organizations.table.status'),
      render: (o) => (
        <Stack direction="row" spacing={0.5}>
          {o.is_archived ? (
            <Chip label={t('organizations.archivedChip')} color="default" size="small" />
          ) : (
            <Chip label={t('organizations.activeChip')} color="success" size="small" />
          )}
          {o.deletion_due_at && (
            <Chip
              label={t('organizations.deletionDueChip', {
                date: formatDate(o.deletion_due_at, i18n.language),
              })}
              color="error"
              size="small"
            />
          )}
        </Stack>
      ),
      sortValue: (o) => (o.is_archived ? 1 : 0),
    },
    {
      id: 'activeRuns',
      header: t('organizations.table.activeRuns'),
      // An archived organization runs nothing a participant can reach, so a
      // count there would read as "still running".
      render: (o) => (o.is_archived ? t('organizations.noActivity') : o.active_run_count),
      sortValue: (o) => (o.is_archived ? -1 : o.active_run_count),
      align: 'right',
    },
    {
      id: 'lastActivity',
      header: t('organizations.table.lastActivity'),
      render: (o) =>
        o.last_activity_at ? formatDateTime(o.last_activity_at, i18n.language) : t('organizations.noActivity'),
      sortValue: (o) => o.last_activity_at ?? '',
      hideBelow: 'lg',
    },
    {
      id: 'users',
      header: t('organizations.table.users'),
      render: (o) => o.user_count,
      sortValue: (o) => o.user_count,
      align: 'right',
      hideBelow: 'lg',
    },
    {
      id: 'rallyes',
      header: t('organizations.table.rallyes'),
      render: (o) => o.rallye_count,
      sortValue: (o) => o.rallye_count,
      align: 'right',
      hideBelow: 'lg',
    },
    {
      id: 'runs',
      header: t('organizations.table.runs'),
      render: (o) => o.run_count,
      sortValue: (o) => o.run_count,
      align: 'right',
      hideBelow: 'lg',
    },
    {
      id: 'admins',
      header: t('organizations.table.admins'),
      render: (o) => o.org_admin_count,
      sortValue: (o) => o.org_admin_count,
      align: 'right',
      hideBelow: 'lg',
    },
    {
      id: 'invitations',
      header: t('organizations.table.invitations'),
      render: (o) => o.pending_admin_invitation_count,
      sortValue: (o) => o.pending_admin_invitation_count,
      align: 'right',
      hideBelow: 'lg',
    },
    {
      id: 'languages',
      header: t('organizations.table.languages'),
      render: (o) => o.content_languages.join(', '),
      hideBelow: 'xl',
    },
    {
      id: 'createdAt',
      header: t('organizations.table.createdAt'),
      render: (o) => formatDate(o.created_at, i18n.language),
      sortValue: (o) => o.created_at,
      hideBelow: 'xl',
    },
    {
      id: 'actions',
      header: t('organizations.table.actions'),
      actions: true,
      stickyEnd: true,
      render: (o) => (
        <Stack direction="row" spacing={0.5}>
          <Tooltip title={t('organizations.editButton')}>
            <IconButton
              aria-label={`${t('organizations.editButton')}: ${o.name}`}
              onClick={() => setEditTarget(o)}
            >
              <EditIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          {/* Nobody can sign in to an archived organization, so an invitation
              there leads nowhere. */}
          {!o.is_archived && (
            <Tooltip title={t('organizations.inviteAdminButton')}>
              <IconButton
                aria-label={`${t('organizations.inviteAdminButton')}: ${o.name}`}
                onClick={() => setInviteTarget(o)}
              >
                <PersonAddIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          {o.is_archived ? (
            <>
              <Tooltip
                title={
                  o.deletion_due_at
                    ? t('organizations.cancelDeletionButton')
                    : t('organizations.restoreButton')
                }
              >
                <IconButton
                  color="success"
                  aria-label={
                    o.deletion_due_at
                      ? t('organizations.cancelDeletionButton')
                      : t('organizations.restoreButton')
                  }
                  onClick={() => {
                    setActionError(null);
                    setRestoreTarget(o);
                  }}
                >
                  <UnarchiveIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              {/* A planned deletion is cancelled by the restore button; deleting
                  again would plan it twice. */}
              {!o.deletion_due_at && (
                <Tooltip title={t('organizations.deleteButton')}>
                  <IconButton
                    color="error"
                    aria-label={`${t('organizations.deleteButton')}: ${o.name}`}
                    onClick={() => {
                      setActionError(null);
                      setDeleteTarget(o);
                    }}
                  >
                    <DeleteForeverIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
            </>
          ) : (
            <Tooltip title={t('organizations.archiveButton')}>
              <IconButton
                color="warning"
                aria-label={`${t('organizations.archiveButton')}: ${o.name}`}
                onClick={() => {
                  setActionError(null);
                  setArchiveTarget(o);
                }}
              >
                <ArchiveIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
        </Stack>
      ),
    },
  ];

  const visibleOrgs = showArchived ? orgs : orgs.filter((o) => !o.is_archived);

  if (status === 'loading' || status === 'unauthenticated') {
    return (
      <InstanceShell title={t('organizations.title')}>
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
    <InstanceShell title={t('organizations.title')} onSignOut={() => void handleSignOut()} maxWidth="xl">
      <Box>
        {successMessage && (
          <Alert severity="success" onClose={() => setSuccessMessage(null)} sx={{ mb: 2 }}>
            {successMessage}
          </Alert>
        )}
        {signOutError && (
          <Alert severity="error" role="alert" onClose={() => setSignOutError(null)} sx={{ mb: 2 }}>
            {signOutError}
          </Alert>
        )}
        <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 2 }}>
          <FormControlLabel
            control={
              <Switch
                checked={showArchived}
                onChange={(e) => setShowArchived(e.target.checked)}
              />
            }
            label={t('organizations.showArchivedLabel')}
          />
          <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>
            {t('organizations.createButton')}
          </Button>
        </Stack>
        <DataTable<InstanceOrganizationListItem>
          rows={visibleOrgs}
          columns={columns}
          getRowId={(o) => o.slug}
          loading={loading}
          error={error}
          onRetry={load}
          emptyMessage={t('organizations.empty')}
          defaultSort={{ columnId: 'name', dir: 'asc' }}
          paginate
          ariaLabel={t('organizations.title')}
        />
      </Box>
      <OrganizationCreateDialog
        open={createOpen}
        onSuccess={handleCreateSuccess}
        onClose={() => setCreateOpen(false)}
      />
      <OrganizationEditDialog
        open={editTarget !== null}
        org={editTarget}
        onSuccess={handleEditSuccess}
        onClose={() => setEditTarget(null)}
      />
      <OrganizationDeleteDialog
        open={deleteTarget !== null}
        org={deleteTarget}
        onSuccess={handleDeleteSuccess}
        onClose={() => setDeleteTarget(null)}
      />
      <OrgAdminInviteDialog
        open={inviteTarget !== null}
        org={inviteTarget}
        onSuccess={handleInviteSuccess}
        onClose={() => setInviteTarget(null)}
      />
      <ConfirmDialog
        open={archiveTarget !== null}
        title={t('organizations.archiveTitle')}
        message={archiveTarget ? t('organizations.archiveMessage', { name: archiveTarget.name }) : ''}
        confirmLabel={t('organizations.archiveButton')}
        cancelLabel={t('actions.cancel')}
        destructive
        loading={actionLoading}
        onConfirm={() => void handleArchive()}
        onCancel={() => {
          setArchiveTarget(null);
          setActionError(null);
        }}
      >
        {/* The warning: an archive locks the people of a running rallye out
            at once. */}
        {archiveTarget && archiveTarget.active_run_count > 0 && (
          <Alert severity="warning" role="note" sx={{ mt: 2 }}>
            {t('organizations.archiveActiveRuns', { count: archiveTarget.active_run_count })}
          </Alert>
        )}
        {actionError && (
          <Alert severity="error" role="alert" sx={{ mt: 2 }}>
            {actionError}
          </Alert>
        )}
      </ConfirmDialog>
      <ConfirmDialog
        open={restoreTarget !== null}
        // A pending deletion says what the restore also does.
        title={t(
          restoreTarget?.deletion_due_at ? 'organizations.cancelDeletionTitle' : 'organizations.restoreTitle',
        )}
        message={
          restoreTarget
            ? t(
                restoreTarget.deletion_due_at
                  ? 'organizations.cancelDeletionMessage'
                  : 'organizations.restoreMessage',
                { name: restoreTarget.name },
              )
            : ''
        }
        confirmLabel={t(
          restoreTarget?.deletion_due_at ? 'organizations.cancelDeletionButton' : 'organizations.restoreButton',
        )}
        // "Abbrechen" beside "Löschung abbrechen" would read as two cancels
        // with opposite effect.
        cancelLabel={t(restoreTarget?.deletion_due_at ? 'organizations.cancelDeletionKeep' : 'actions.cancel')}
        loading={actionLoading}
        onConfirm={() => void handleRestore()}
        onCancel={() => {
          setRestoreTarget(null);
          setActionError(null);
        }}
      >
        {actionError && (
          <Alert severity="error" role="alert" sx={{ mt: 2 }}>
            {actionError}
          </Alert>
        )}
      </ConfirmDialog>
    </InstanceShell>
  );
};

export default InstanceOrganizations;
