import React from 'react';
import { useTranslation } from 'react-i18next';
import ConfirmDialog from './ConfirmDialog';

interface GuestLogoutDialogProps {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * The question a guest gets before "Abmelden". A guest has no
 * password: the session in this browser is the only way into the team, so a
 * sign-out cannot be undone. An account user is signed out without it.
 * Destructive, so the dialog opens on "Abbrechen".
 */
const GuestLogoutDialog: React.FC<GuestLogoutDialogProps> = ({ open, onConfirm, onCancel }) => {
  const { t } = useTranslation('runs');
  return (
    <ConfirmDialog
      open={open}
      title={t('participant.guestLogout.title')}
      message={t('participant.guestLogout.message')}
      confirmLabel={t('participant.guestLogout.confirm')}
      destructive
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
};

export default GuestLogoutDialog;
