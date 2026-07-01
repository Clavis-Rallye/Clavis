import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Stack, Typography } from '@mui/material';
import CheckIcon from '@mui/icons-material/Check';
import CloseIcon from '@mui/icons-material/Close';
import { ROLE_CAPABILITIES, roleHasCapability, type RoleName } from './roleCapabilities';

interface RoleCapabilityPanelProps {
  role: RoleName;
  /** `floating` sits outside the dialog to its right (desktop); `inline` stacks below (mobile). */
  variant?: 'floating' | 'inline';
}

/** Read-only "what this role can do" checklist. Rendered next to the role
 * select in the user create/edit dialogs; live-reflects the passed role. */
const RoleCapabilityPanel: React.FC<RoleCapabilityPanelProps> = ({ role, variant = 'floating' }) => {
  const { t } = useTranslation('admin');

  // `floating` sits just outside the dialog to its right (the dialog's Paper is
  // position:relative + overflow:visible, so this doesn't resize/move it) — a
  // solid, elevated card so the dimmed backdrop behind never shows through.
  // `inline` stacks below the fields inside the dialog (subtle tinted block).
  const variantSx =
    variant === 'floating'
      ? {
          position: 'absolute' as const,
          top: 0,
          left: '100%',
          ml: 2,
          width: 250,
          bgcolor: 'background.paper',
          boxShadow: 6,
        }
      : { mt: 2, bgcolor: 'action.hover' };

  return (
    <Box sx={{ borderRadius: 1, p: 2, ...variantSx }} aria-live="polite">
      <Typography variant="subtitle2" component="h3" sx={{ mb: 1 }}>
        {t('users.capabilities.title', { defaultValue: 'Was diese Rolle darf' })}
      </Typography>
      <Stack spacing={0.75}>
        {ROLE_CAPABILITIES.map((cap) => {
          const allowed = roleHasCapability(role, cap.minRole);
          return (
            <Stack key={cap.key} direction="row" spacing={1} sx={{ alignItems: 'center' }}>
              {/* Not-granted rows read as on-surface-variant, not as a light grey:
                  `text.disabled` is 38 % black and misses 4.5:1. The cross icon,
                  not the tint, marks the denial. */}
              {allowed ? (
                <CheckIcon fontSize="small" color="success" aria-hidden />
              ) : (
                <CloseIcon fontSize="small" sx={{ color: 'text.secondary' }} aria-hidden />
              )}
              <Typography variant="body2" sx={{ color: allowed ? 'text.primary' : 'text.secondary' }}>
                {t(`users.capabilities.${cap.key}`, { defaultValue: cap.key })}
                <Box
                  component="span"
                  sx={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}
                >
                  {allowed
                    ? t('users.capabilities.allowed', { defaultValue: 'erlaubt' })
                    : t('users.capabilities.denied', { defaultValue: 'nicht erlaubt' })}
                </Box>
              </Typography>
            </Stack>
          );
        })}
      </Stack>
    </Box>
  );
};

export default RoleCapabilityPanel;
