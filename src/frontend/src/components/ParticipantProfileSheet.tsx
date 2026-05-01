import React, { useId, useState } from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import {
  Box,
  Button,
  Chip,
  Divider,
  Drawer,
  IconButton,
  Stack,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import LogoutIcon from '@mui/icons-material/Logout';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import PersonAddIcon from '@mui/icons-material/PersonAdd';
import GuestLogoutDialog from './GuestLogoutDialog';
import UserAvatar from './UserAvatar';
import { useAuth } from '../auth/useAuth';

interface ParticipantProfileSheetProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Slide-in profile sheet for the participant run shell. It keeps the
 * participant in the run: the admin Profile route `/profile` is unreachable
 * for guests and offers no back-to-run path.
 *
 * Logged-in users see a compact summary plus a link that opens the full
 * admin profile in a new tab so they can edit password / 2FA there.
 * Guests see a "create account" CTA.
 */
const ParticipantProfileSheet: React.FC<ParticipantProfileSheetProps> = ({ open, onClose }) => {
  const { user, logout } = useAuth();
  const { t } = useTranslation('runs');
  const navigate = useNavigate();
  const titleId = useId();

  const isGuest = user?.role === 'guest';

  const [confirmGuestLogout, setConfirmGuestLogout] = useState(false);

  const doLogout = async () => {
    setConfirmGuestLogout(false);
    onClose();
    await logout();
    navigate('/login');
  };

  // A guest cannot come back after a sign-out, so a guest is asked.
  const handleLogout = () => {
    if (isGuest) setConfirmGuestLogout(true);
    else void doLogout();
  };

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      /* The paper is the dialog. Its visible heading is its name. */
      slotProps={{
        paper: { 'aria-labelledby': titleId, sx: { width: { xs: '100%', sm: 360 } } },
      }}
    >
      <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', p: 2, pb: 1 }}>
        <Typography id={titleId} variant="h5" component="h2">
          {t('participant.profile.title')}
        </Typography>
        <IconButton aria-label={t('participant.profile.close')} onClick={onClose} edge="end">
          <CloseIcon />
        </IconButton>
      </Stack>
      <Divider />

      <Stack spacing={2.5} sx={{ p: 3, alignItems: 'center', textAlign: 'center' }}>
        <UserAvatar
          displayName={user?.display_name}
          email={user?.email}
          size={80}
          sx={{ fontSize: 32 }}
        />
        <Box>
          <Typography variant="h5" component="p">
            {user?.display_name ?? '—'}
          </Typography>
          {user?.email && (
            <Typography variant="body2" color="text.secondary">
              {user.email}
            </Typography>
          )}
          {user?.role && (
            <Chip
              label={t(`participant.profile.roles.${user.role}`, { defaultValue: user.role })}
              size="small"
              color={isGuest ? 'default' : 'primary'}
              variant={isGuest ? 'outlined' : 'filled'}
              sx={{ mt: 1 }}
            />
          )}
        </Box>

        {isGuest && (
          <Box
            sx={{
              bgcolor: 'secondary.light',
              color: 'text.primary',
              borderRadius: 2,
              p: 2,
              width: '100%',
              textAlign: 'left',
            }}
          >
            <Typography variant="body2" sx={{ mb: 1.5 }}>
              {t('participant.profile.guestNotice')}
            </Typography>
            <Button
              variant="contained"
              startIcon={<PersonAddIcon />}
              onClick={() => {
                onClose();
                navigate('/register');
              }}
              fullWidth
            >
              {t('participant.profile.createAccount')}
            </Button>
          </Box>
        )}

        {!isGuest && (
          <Button
            variant="outlined"
            startIcon={<OpenInNewIcon />}
            onClick={() => {
              window.open('/profile', '_blank', 'noopener');
            }}
            fullWidth
          >
            {t('participant.profile.fullProfile')}
          </Button>
        )}
      </Stack>

      <Box sx={{ flex: 1 }} />
      <Divider />
      <Box sx={{ p: 2 }}>
        <Button
          variant="text"
          color="error"
          startIcon={<LogoutIcon />}
          onClick={handleLogout}
          fullWidth
        >
          {t('participant.profile.logout')}
        </Button>
      </Box>
      <GuestLogoutDialog
        open={confirmGuestLogout}
        onConfirm={() => void doLogout()}
        onCancel={() => setConfirmGuestLogout(false)}
      />
    </Drawer>
  );
};

export default ParticipantProfileSheet;
