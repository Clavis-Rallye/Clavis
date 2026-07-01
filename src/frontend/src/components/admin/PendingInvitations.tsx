import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Chip,
  Divider,
  List,
  ListItem,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import MailOutlinedIcon from '@mui/icons-material/MailOutlined';
import ErrorOutlinedIcon from '@mui/icons-material/ErrorOutlined';
import { resendInvitation, revokeInvitation } from '../../api/admin';
import { ApiError } from '../../api/client';
import type { InvitationResponse } from '../../api/types';
import ConfirmDialog from '../ConfirmDialog';

interface PendingInvitationsProps {
  orgSlug: string;
  invitations: InvitationResponse[];
  onChanged: () => void;
  onNotify: (message: string) => void;
  onError: (message: string) => void;
}

/**
 * Invitations that have gone out but not been redeemed.
 *
 * Shown above the user table rather than mixed into it: these are not accounts.
 * They have no login, no last-seen, nothing to deactivate. The only useful
 * actions are "send it again" and "take it back". Hiding them entirely would
 * be worse: an invitation to a mistyped address would then stay valid for a
 * week with nobody able to see it.
 */
const PendingInvitations: React.FC<PendingInvitationsProps> = ({
  orgSlug,
  invitations,
  onChanged,
  onNotify,
  onError,
}) => {
  const { t, i18n } = useTranslation('admin');
  const [revokeTarget, setRevokeTarget] = useState<InvitationResponse | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  if (invitations.length === 0) return null;

  const handleResend = async (invitation: InvitationResponse) => {
    setBusyId(invitation.id);
    try {
      await resendInvitation(orgSlug, invitation.id);
      onNotify(t('users.invitations.resendSuccess', { email: invitation.email }));
      onChanged();
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setBusyId(null);
    }
  };

  const handleRevoke = async () => {
    if (!revokeTarget) return;
    setBusyId(revokeTarget.id);
    try {
      await revokeInvitation(orgSlug, revokeTarget.id);
      onNotify(t('users.invitations.revokeSuccess', { email: revokeTarget.email }));
      setRevokeTarget(null);
      onChanged();
    } catch (err) {
      onError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Paper variant="outlined" sx={{ mb: 3, borderRadius: 3 }}>
      <Box sx={{ px: 2, pt: 2, pb: 1 }}>
        <Typography variant="h3" component="h2" sx={{ fontSize: '1rem', fontWeight: 700 }}>
          {t('users.invitations.heading')}
        </Typography>
      </Box>
      <Divider />
      <List disablePadding>
        {invitations.map((invitation, index) => (
          <ListItem
            key={invitation.id}
            divider={index < invitations.length - 1}
            sx={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 1,
              justifyContent: 'space-between',
              alignItems: { sm: 'center' },
              py: 1.5,
            }}
          >
            <Stack spacing={0.25} sx={{ minWidth: 0, flex: '1 1 260px' }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                <MailOutlinedIcon fontSize="small" color="action" />
                <Typography sx={{ fontWeight: 600, wordBreak: 'break-all' }}>
                  {invitation.email}
                </Typography>
                <Chip size="small" label={t('users.invitations.pendingChip')} />
                <Chip size="small" variant="outlined" label={t(`users.roles.${invitation.role}`)} />
                {!invitation.mail_delivered && (
                  <Tooltip title={t('users.invite.mailDisabled')}>
                    <Chip
                      size="small"
                      color="warning"
                      icon={<ErrorOutlinedIcon />}
                      label={t('users.invitations.notDelivered')}
                    />
                  </Tooltip>
                )}
              </Stack>
              <Typography variant="body2" color="text.secondary">
                {t('users.invitations.invitedBy', { name: invitation.invited_by_name })}
                {' · '}
                {t('users.invitations.expiresOn', {
                  date: new Date(invitation.expires_at).toLocaleDateString(i18n.language),
                })}
              </Typography>
            </Stack>
            <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
              <Button
                size="small"
                variant="outlined"
                disabled={busyId === invitation.id}
                onClick={() => void handleResend(invitation)}
              >
                {t('users.invitations.resend')}
              </Button>
              <Button
                size="small"
                variant="outlined"
                color="error"
                disabled={busyId === invitation.id}
                onClick={() => setRevokeTarget(invitation)}
              >
                {t('users.invitations.revoke')}
              </Button>
            </Stack>
          </ListItem>
        ))}
      </List>

      <ConfirmDialog
        open={revokeTarget !== null}
        title={t('users.invitations.revokeTitle')}
        message={
          revokeTarget ? t('users.invitations.revokeMessage', { email: revokeTarget.email }) : ''
        }
        confirmLabel={t('users.invitations.revoke')}
        destructive
        loading={busyId !== null && busyId === revokeTarget?.id}
        onConfirm={() => void handleRevoke()}
        onCancel={() => setRevokeTarget(null)}
      />
    </Paper>
  );
};

export default PendingInvitations;
