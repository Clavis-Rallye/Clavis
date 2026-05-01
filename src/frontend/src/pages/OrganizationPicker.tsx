import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemText from '@mui/material/ListItemText';
import Typography from '@mui/material/Typography';
import { alpha } from '@mui/material/styles';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';

import OnboardingShell from '../components/OnboardingShell';
import OnboardingBrandHeader from '../components/OnboardingBrandHeader';
import ProgressIndicator from '../components/ProgressIndicator';
import { authHeadingSx, authSubtitleSx } from '../styles/authChrome';
import { onboardingBackPillSx, onboardingPrimaryPillSx } from '../styles/onboardingButtons';
import { listAll, type PublicOrganization } from '../services/publicOrganizations';
import { useWizardState } from '../wizard/useWizardState';
import { getStepInfo } from '../wizard/wizardSteps';

interface OrganizationPickerProps {
  /** Called once the user confirms a selection. */
  onSelect: (org: PublicOrganization) => void;
}

/**
 * Fallback when a user reaches `/register` directly without
 * a Welcome run-code lookup. Lists all active organisations from
 * `GET /api/public/organizations` and writes the chosen slug to the wizard
 * state so ProfileSetupStep knows which org to register the user into.
 *
 * Uses the shared OnboardingShell chrome so it matches /login and the
 * ProfileSetupStep it hands off to: the same small brand mark at the same Y,
 * the same heading, the same Back / Continue pills, progress pinned to the
 * bottom. `fillViewport` keeps the header, actions and progress fixed so only
 * the org list scrolls.
 */
const OrganizationPicker: React.FC<OrganizationPickerProps> = ({ onSelect }) => {
  const { t } = useTranslation('profileSetup');
  const { t: tc } = useTranslation('common');
  const navigate = useNavigate();
  const wizard = useWizardState();
  const stepInfo = getStepInfo('org-picker', wizard.pathKind);
  const [orgs, setOrgs] = useState<PublicOrganization[] | null>(null);
  const [selected, setSelected] = useState<PublicOrganization | null>(null);
  // A failed load is not an empty installation: it says so and offers a
  // retry.
  const [loadError, setLoadError] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    listAll()
      .then((list) => {
        if (!cancelled) setOrgs(list);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = () => {
    setLoadError(false);
    setOrgs(null);
    setAttempt((n) => n + 1);
  };

  return (
    <OnboardingShell
      fillViewport
      progress={<ProgressIndicator current={stepInfo.current} total={stepInfo.total} />}
    >
      <OnboardingBrandHeader />
      <Typography component="h1" sx={{ ...authHeadingSx, flexShrink: 0 }}>
        {t('orgPicker.headline')}
      </Typography>
      <Typography sx={{ ...authSubtitleSx, flexShrink: 0 }}>{t('orgPicker.subtitle')}</Typography>

      {/* Only this region scrolls — the brand mark, heading, actions and the
          progress bar stay fixed. */}
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          marginBlockStart: '8px',
          // Bleed the scroll region 4 px into the 40 px gutter so the selected
          // item's border and the scrollbar aren't clipped by overflow.
          marginInline: '-4px',
          paddingInline: '4px',
        }}
      >
        {orgs == null && !loadError && (
          <Typography variant="body2" color="text.secondary">
            {t('orgPicker.loading')}
          </Typography>
        )}
        {loadError && (
          <Alert
            severity="error"
            action={
              <Button color="inherit" size="small" onClick={retry}>
                {tc('actions.retry')}
              </Button>
            }
          >
            {t('orgPicker.loadError')}
          </Alert>
        )}
        {orgs != null && orgs.length === 0 && <Alert severity="info">{t('orgPicker.empty')}</Alert>}
        {orgs != null && orgs.length > 0 && (
          <List disablePadding>
            {orgs.map((org) => {
              const isSelected = selected?.slug === org.slug;
              return (
                <ListItem key={org.slug} disablePadding>
                  <ListItemButton
                    selected={isSelected}
                    onClick={() => setSelected(org)}
                    sx={{
                      gap: 1,
                      borderRadius: 1,
                      borderInlineStart: '4px solid transparent',
                      '&.Mui-selected, &.Mui-selected:hover': {
                        bgcolor: (theme) => alpha(theme.palette.primary.main, 0.16),
                        borderInlineStartColor: 'primary.main',
                      },
                      '&.Mui-selected .MuiListItemText-primary': {
                        fontWeight: 700,
                        color: 'primary.main',
                      },
                    }}
                  >
                    <ListItemText primary={org.name} secondary={org.slug} />
                    {isSelected && <CheckCircleIcon color="primary" fontSize="small" />}
                  </ListItemButton>
                </ListItem>
              );
            })}
          </List>
        )}
      </Box>

      {/* Fixed action row — mirrors ProfileSetupStep so the /register flow keeps
          the same Back / Continue buttons across steps. Back returns to login. */}
      <Box
        sx={{
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'row',
          gap: '16px',
          paddingBlockStart: '16px',
        }}
      >
        <Box component="button" type="button" onClick={() => navigate('/login')} sx={onboardingBackPillSx}>
          {tc('actions.back', { defaultValue: 'Back' })}
        </Box>
        <Box
          component="button"
          type="button"
          disabled={!selected}
          onClick={() => selected && onSelect(selected)}
          sx={onboardingPrimaryPillSx}
        >
          {tc('actions.continue', { defaultValue: 'Continue' })}
        </Box>
      </Box>
    </OnboardingShell>
  );
};

export default OrganizationPicker;
