import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert,
  Box,
  Button,
  type ButtonProps,
  Checkbox,
  Chip,
  FormControlLabel,
  IconButton,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import EditIcon from '@mui/icons-material/Edit';
import DeleteIcon from '@mui/icons-material/Delete';
import RestoreIcon from '@mui/icons-material/RestoreFromTrash';
import PersonAddAlt1Icon from '@mui/icons-material/PersonAddAlt1';
import LockResetIcon from '@mui/icons-material/LockReset';
import {
  getMailStatus,
  getTakeoverNotice,
  listInvitations,
  listUsers,
  updateUser,
  deleteUser,
  restoreUser,
  sendPasswordReset,
} from '../../api/admin';
import { ApiError } from '../../api/client';
import { useAuth } from '../../auth/useAuth';
import type { InvitationResponse, UserResponse } from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import DataTable, { type DataTableColumn, type DataTableFilter } from '../../components/DataTable';
import UserCreateDialog from '../../components/admin/UserCreateDialog';
import UserEditDialog from '../../components/admin/UserEditDialog';
import UserInviteDialog from '../../components/admin/UserInviteDialog';
import PendingInvitations from '../../components/admin/PendingInvitations';
import { formatDateTime } from '../../utils/formatDate';

// Backend caps the list `limit` query param at 200.
const FETCH_LIMIT = 200;

/** Shared look of the activate/deactivate action in the table. A medium button
 * at the theme button size (14 px). `error.main` marks the destructive
 * direction and `text.primary` the harmless one, because `warning.main` misses
 * the 4.5:1 contrast on white. */
const toggleActiveButtonProps = (isActive: boolean): ButtonProps => ({
  size: 'medium',
  variant: 'outlined',
  color: isActive ? 'error' : 'inherit',
  sx: {
    minWidth: 'auto',
    px: 1.5,
    // Spelled out rather than left to the `color` prop: MUI resolves a variant
    // colour through a CSS custom property, which the greyed-out `Mui-disabled`
    // state must keep overriding. Hence the `:not()` guard.
    '&:not(.Mui-disabled)': isActive
      ? { color: 'error.main', borderColor: 'error.main' }
      : { color: 'text.primary', borderColor: 'text.secondary' },
  },
});

const UserList: React.FC = () => {
  const { t, i18n } = useTranslation('admin');
  const { user } = useAuth();
  const orgSlug = user?.organization_slug ?? '';

  const [users, setUsers] = useState<UserResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editUser, setEditUser] = useState<UserResponse | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<UserResponse | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteImmediate, setDeleteImmediate] = useState(false);
  const [showDeleted, setShowDeleted] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [invitations, setInvitations] = useState<InvitationResponse[]>([]);
  const [mailEnabled, setMailEnabled] = useState(true);
  const [resetTarget, setResetTarget] = useState<UserResponse | null>(null);
  const [sendingReset, setSendingReset] = useState(false);

  const load = useCallback(async () => {
    if (!orgSlug) return;
    setLoading(true);
    setError(null);
    try {
      const data = await listUsers(orgSlug, { limit: FETCH_LIMIT, include_deleted: showDeleted });
      setUsers(data.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setLoading(false);
    }
  }, [orgSlug, showDeleted]);

  // Invitations load separately from users: a failure here must not blank the
  // user table, which is the page's actual job.
  const loadInvitations = useCallback(async () => {
    if (!orgSlug) return;
    try {
      const data = await listInvitations(orgSlug);
      setInvitations(data.items);
    } catch {
      setInvitations([]);
    }
  }, [orgSlug]);

  useEffect(() => {
    void load();
    void loadInvitations();
  }, [load, loadInvitations]);

  // An admin who took over through an operator invitation reads which
  // accounts that deactivated.
  const [takeover, setTakeover] = useState<string | null>(null);
  useEffect(() => {
    if (!orgSlug) return;
    let cancelled = false;
    getTakeoverNotice(orgSlug)
      .then((notice) => {
        if (cancelled || !notice?.replaced?.length) return;
        setTakeover(notice.replaced.map((a) => `${a.display_name} (${a.email})`).join(', '));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [orgSlug]);

  useEffect(() => {
    if (!orgSlug) return;
    // A deployment without mail can still create invitations; the UI just has
    // to stop implying that anything was delivered.
    getMailStatus(orgSlug)
      .then((status) => setMailEnabled(status.enabled))
      .catch(() => setMailEnabled(false));
  }, [orgSlug]);

  const handleSendReset = async () => {
    if (!resetTarget) return;
    setSendingReset(true);
    try {
      await sendPasswordReset(orgSlug, resetTarget.id);
      setSuccessMessage(t('users.sendReset.success', { email: resetTarget.email }));
      setResetTarget(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setSendingReset(false);
    }
  };

  const handleToggleActive = async (u: UserResponse) => {
    try {
      await updateUser(orgSlug, u.id, { is_active: !u.is_active });
      void load();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteUser(orgSlug, deleteTarget.id, deleteImmediate ? 'immediate' : 'grace');
      setDeleteTarget(null);
      setSuccessMessage(deleteImmediate ? t('users.delete.successImmediate') : t('users.delete.success'));
      setDeleteImmediate(false);
      void load();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setDeleting(false);
    }
  };

  const handleRestore = async (u: UserResponse) => {
    try {
      await restoreUser(orgSlug, u.id);
      setSuccessMessage(t('users.restore.success', { name: u.display_name }));
      void load();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    }
  };

  // Anonymized rows are past the grace window — nothing left to restore.
  const isAnonymized = (u: UserResponse) => !!u.email && u.email.endsWith('@anonymized.local');

  const formatLastLogin = (lastLogin: string | null) => {
    if (!lastLogin) return t('users.lastLoginNever');
    // No seconds: they add nothing and make the cell wrap.
    return formatDateTime(lastLogin, i18n.language);
  };

  // The person a row button acts on: its name ends the button's name, so two
  // rows never share one (WCAG 2.4.6).
  const personOf = (u: UserResponse) => u.display_name || u.email || u.id;
  const toggleActiveLabel = (u: UserResponse) =>
    u.is_active ? t('users.actions.deactivate') : t('users.actions.activate');

  const columns: DataTableColumn<UserResponse>[] = [
    {
      id: 'name',
      header: t('users.table.name'),
      render: (u) => u.display_name,
      sortValue: (u) => u.display_name ?? '',
      searchValue: (u) => u.display_name ?? '',
    },
    {
      id: 'email',
      header: t('users.table.email'),
      render: (u) => u.email,
      sortValue: (u) => u.email ?? '',
      searchValue: (u) => u.email ?? '',
    },
    {
      id: 'role',
      header: t('users.table.role'),
      render: (u) => <Chip label={t(`users.roles.${u.role}`)} size="small" />,
      sortValue: (u) => t(`users.roles.${u.role}`),
    },
    {
      id: 'status',
      header: t('users.table.status'),
      render: (u) =>
        u.is_deleted ? (
          <Chip
            label={isAnonymized(u) ? t('users.status.anonymized') : t('users.status.deleted')}
            color="error"
            variant="outlined"
            size="small"
          />
        ) : (
          <Chip
            label={u.is_active ? t('users.status.active') : t('users.status.inactive')}
            color={u.is_active ? 'success' : 'default'}
            size="small"
          />
        ),
      sortValue: (u) => (u.is_deleted ? -1 : u.is_active ? 1 : 0),
    },
    {
      id: 'twoFactor',
      header: t('users.table.twoFactor'),
      render: (u) =>
        u.totp_enabled ? t('users.twoFactor.enabled') : t('users.twoFactor.disabled'),
      sortValue: (u) => (u.totp_enabled ? 1 : 0),
    },
    {
      id: 'lastLogin',
      header: t('users.table.lastLogin'),
      // nowrap: the date and its time belong on one line,
      // otherwise the narrow column breaks them apart and the row grows.
      render: (u) => (
        <Box component="span" sx={{ whiteSpace: 'nowrap' }}>
          {formatLastLogin(u.last_login)}
        </Box>
      ),
      sortValue: (u) => u.last_login ?? '',
    },
    {
      id: 'actions',
      header: t('users.table.actions'),
      actions: true,
      render: (u) => {
        const isSelf = user?.id === u.id;
        if (u.is_deleted) {
          return (
            <Stack direction="row" spacing={0.5}>
              <Tooltip
                title={isAnonymized(u) ? t('users.restore.anonymizedHint') : t('users.actions.restore')}
              >
                <span>
                  <IconButton
                    color="success"
                    aria-label={t('users.actions.restore')}
                    disabled={isAnonymized(u)}
                    onClick={() => void handleRestore(u)}
                  >
                    <RestoreIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
            </Stack>
          );
        }
        return (
          <Stack direction="row" spacing={0.5}>
            <Tooltip title={isSelf ? t('users.actions.selfDisabled') : t('users.actions.edit')}>
              <span>
                <IconButton
                  aria-label={`${t('users.actions.edit')}: ${personOf(u)}`}
                  disabled={isSelf}
                  onClick={() => setEditUser(u)}
                >
                  <EditIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            {isSelf ? (
              <Tooltip title={t('users.actions.selfDisabled')}>
                <span>
                  <Button
                    {...toggleActiveButtonProps(u.is_active)}
                    aria-label={`${toggleActiveLabel(u)}: ${personOf(u)}`}
                    disabled
                  >
                    {u.is_active ? t('users.actions.deactivate') : t('users.actions.activate')}
                  </Button>
                </span>
              </Tooltip>
            ) : (
              <Button
                {...toggleActiveButtonProps(u.is_active)}
                aria-label={`${toggleActiveLabel(u)}: ${personOf(u)}`}
                onClick={() => void handleToggleActive(u)}
              >
                {u.is_active ? t('users.actions.deactivate') : t('users.actions.activate')}
              </Button>
            )}
            <Tooltip
              title={u.email ? t('users.sendReset.action') : t('users.sendReset.noEmail')}
            >
              <span>
                <IconButton
                  aria-label={`${t('users.sendReset.action')}: ${personOf(u)}`}
                  disabled={!u.email || !u.is_active}
                  onClick={() => setResetTarget(u)}
                >
                  <LockResetIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title={isSelf ? t('users.actions.selfDisabled') : t('users.actions.delete')}>
              <span>
                <IconButton
                  color="error"
                  aria-label={`${t('users.actions.delete')}: ${personOf(u)}`}
                  disabled={isSelf}
                  onClick={() => setDeleteTarget(u)}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Stack>
        );
      },
    },
  ];

  const filters: DataTableFilter<UserResponse>[] = [
    {
      id: 'role',
      label: t('users.table.role'),
      options: [
        { value: 'participant', label: t('users.roles.participant') },
        { value: 'coordinator', label: t('users.roles.coordinator') },
        { value: 'creator', label: t('users.roles.creator') },
        { value: 'org_admin', label: t('users.roles.org_admin') },
      ],
      match: (u, value) => u.role === value,
    },
    {
      id: 'status',
      label: t('users.table.status'),
      options: [
        { value: 'active', label: t('users.status.active') },
        { value: 'inactive', label: t('users.status.inactive') },
      ],
      match: (u, value) => (value === 'active' ? u.is_active : !u.is_active),
    },
  ];

  return (
    <Box>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        spacing={2}
        sx={{
          justifyContent: 'space-between',
          alignItems: { sm: 'center' },
          mb: 3,
        }}
      >
        <Typography variant="h2" component="h1">
          {t('users.title')}
        </Typography>
        <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
          <FormControlLabel
            control={
              <Switch
                checked={showDeleted}
                onChange={(e) => setShowDeleted(e.target.checked)}
                size="small"
              />
            }
            label={t('users.filter.showDeleted')}
          />
          <Button variant="outlined" startIcon={<AddIcon />} onClick={() => setCreateOpen(true)}>
            {t('users.createButton')}
          </Button>
          <Button
            variant="contained"
            startIcon={<PersonAddAlt1Icon />}
            onClick={() => setInviteOpen(true)}
          >
            {t('users.inviteButton')}
          </Button>
        </Stack>
      </Stack>
      {takeover && (
        <Alert severity="info" sx={{ mb: 2 }}>
          {t('users.takeover', { names: takeover })}
        </Alert>
      )}
      {successMessage && (
        <Alert severity="success" onClose={() => setSuccessMessage(null)} sx={{ mb: 2 }}>
          {successMessage}
        </Alert>
      )}
      <PendingInvitations
        orgSlug={orgSlug}
        invitations={invitations}
        onChanged={() => void loadInvitations()}
        onNotify={setSuccessMessage}
        onError={setError}
      />
      <DataTable<UserResponse>
        rows={users}
        columns={columns}
        getRowId={(u) => u.id}
        loading={loading}
        error={error}
        onRetry={load}
        emptyMessage={t('users.empty')}
        searchPlaceholder={t('users.filter.searchPlaceholder')}
        filters={filters}
        defaultSort={{ columnId: 'name', dir: 'asc' }}
        paginate
        ariaLabel={t('users.title')}
      />
      <UserInviteDialog
        open={inviteOpen}
        orgSlug={orgSlug}
        mailEnabled={mailEnabled}
        onSuccess={(invitation) => {
          setInviteOpen(false);
          setSuccessMessage(
            invitation.mail_delivered
              ? t('users.invite.success', { email: invitation.email })
              : t('users.invite.successNotDelivered', { email: invitation.email }),
          );
          void loadInvitations();
        }}
        onClose={() => setInviteOpen(false)}
      />
      <UserCreateDialog
        open={createOpen}
        orgSlug={orgSlug}
        onSuccess={() => {
          setCreateOpen(false);
          setSuccessMessage(t('users.create.success'));
          void load();
        }}
        onClose={() => setCreateOpen(false)}
      />
      <UserEditDialog
        open={editUser !== null}
        user={editUser}
        orgSlug={orgSlug}
        onSuccess={() => {
          setEditUser(null);
          setSuccessMessage(t('users.edit.success'));
          void load();
        }}
        onClose={() => setEditUser(null)}
      />
      <ConfirmDialog
        open={resetTarget !== null}
        title={t('users.sendReset.title')}
        message={resetTarget ? t('users.sendReset.message', { name: resetTarget.display_name }) : ''}
        confirmLabel={t('users.sendReset.action')}
        loading={sendingReset}
        onConfirm={() => void handleSendReset()}
        onCancel={() => setResetTarget(null)}
      />
      <ConfirmDialog
        open={deleteTarget !== null}
        title={t('users.delete.title')}
        // One message per choice: the grace delete is restorable, the
        // immediate one is not. Both in one text would contradict each other.
        message={
          deleteTarget
            ? t(deleteImmediate ? 'users.delete.message' : 'users.delete.graceMessage', {
                name: deleteTarget.display_name,
              })
            : ''
        }
        confirmLabel={t('users.actions.delete')}
        destructive
        loading={deleting}
        onConfirm={() => void handleDelete()}
        onCancel={() => {
          setDeleteTarget(null);
          setDeleteImmediate(false);
        }}
      >
        <Stack spacing={1} sx={{ mt: 2 }}>
          <FormControlLabel
            control={
              <Checkbox
                checked={deleteImmediate}
                onChange={(e) => setDeleteImmediate(e.target.checked)}
                disabled={deleting}
              />
            }
            label={t('users.delete.immediateLabel')}
          />
        </Stack>
      </ConfirmDialog>
    </Box>
  );
};

export default UserList;
