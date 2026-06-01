import React from 'react';
import { useNavigate } from 'react-router';
import { useTranslation } from 'react-i18next';
import { Badge, BottomNavigation, BottomNavigationAction, Paper } from '@mui/material';
import NotificationsIcon from '@mui/icons-material/Notifications';
import LocationOnIcon from '@mui/icons-material/LocationOn';
import PeopleIcon from '@mui/icons-material/People';
import { BOTTOM_NAV_HEIGHT, TAB_ROUTE, type ParticipantTab } from './participantNav';
import { useUnreadNotices } from '../hooks/useUnreadNotices';

interface ParticipantBottomNavProps {
  /** Run the tabs navigate into. When null the tabs are inert (no active run). */
  runId: string | null;
  /** Highlighted tab, or false for none (e.g. on history / legal pages). */
  active: ParticipantTab | false;
}

/**
 * The participant bottom navigation bar. Used inside the run shell (with an
 * active tab) and on the run-less history / legal pages (nothing selected,
 * routing back into the last run the participant visited).
 */
const ParticipantBottomNav: React.FC<ParticipantBottomNavProps> = ({ runId, active }) => {
  const { t } = useTranslation('runs');
  const navigate = useNavigate();
  // The unread mark on the activity tab: coordination notices this
  // participant hasn't seen yet, from the registry that the run shell and the
  // feed keep up to date.
  const { unreadCount } = useUnreadNotices(runId);

  const handleTab = (_: React.SyntheticEvent, next: ParticipantTab) => {
    if (runId) navigate(`/runs/${runId}/participant/${TAB_ROUTE[next]}`);
  };

  return (
    <Paper
      component="nav"
      aria-label={t('participant.nav.label')}
      elevation={8}
      sx={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        zIndex: (theme) => theme.zIndex.appBar,
      }}
    >
      <BottomNavigation
        value={active}
        onChange={handleTab}
        showLabels
        sx={{ height: BOTTOM_NAV_HEIGHT, maxWidth: 720, mx: 'auto' }}
      >
        <BottomNavigationAction
          value="feed"
          label={t('participant.nav.feed')}
          icon={
            <Badge badgeContent={unreadCount} color="error" max={9}>
              <NotificationsIcon />
            </Badge>
          }
          // The badge digit is decoration — the count travels in the
          // accessible name so screen readers announce it.
          aria-label={
            unreadCount > 0
              ? t('participant.nav.unreadLabel', { count: unreadCount })
              : undefined
          }
          disabled={!runId}
        />
        <BottomNavigationAction
          value="rallye"
          label={t('participant.nav.rallye')}
          icon={<LocationOnIcon />}
          disabled={!runId}
        />
        <BottomNavigationAction
          value="team"
          label={t('participant.nav.team')}
          icon={<PeopleIcon />}
          disabled={!runId}
        />
      </BottomNavigation>
    </Paper>
  );
};

export default ParticipantBottomNav;
