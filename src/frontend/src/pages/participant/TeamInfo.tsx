import {
  Avatar,
  Box,
  Button,
  Card,
  CardContent,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  List,
  ListItem,
  ListItemAvatar,
  ListItemText,
  Stack,
  Typography,
} from '@mui/material';
import PersonIcon from '@mui/icons-material/Person';
import LogoutIcon from '@mui/icons-material/Logout';
import React, { useState } from 'react';
import { useNavigate, useOutletContext, useParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import { memberDisplayName } from '../../utils/memberName';
import { leaveTeam } from '../../api/runs';
import { ApiError } from '../../api/client';
import type { Team } from '../../api/types';
import type { PublicRunState } from '../../services/publicRuns';
import { useAuth } from '../../auth/useAuth';
import ErrorAlert from '../../components/ErrorAlert';
import LoadingSpinner from '../../components/LoadingSpinner';
import { clearLeaveMark, markLeaveStarted } from './leaveInProgress';

/** Error code of the 409 that refuses a leave which would dissolve the team. */
const TEAM_WOULD_DISSOLVE = 'TEAM_WOULD_DISSOLVE';

interface TeamMemberDTO {
  id: string;
  display_name: string;
  is_guest: boolean;
}

interface TeamWithMembers extends Team {
  members?: TeamMemberDTO[];
}

/** Forwarded by TeamTab. RunShell owns the team and switches it live. */
interface TeamTabOutletContext {
  team: Team | null;
  run: PublicRunState | null;
  /** What the shell knows of the caller's membership in this run.
   *  Absent where the page is embedded without the shell. */
  membership?: 'unknown' | 'present' | 'none' | 'error';
  /** Asks the server for the membership again after a failed lookup. */
  retryMembership?: () => void;
}

const TeamInfo: React.FC = () => {
  const { runId } = useParams<{ runId: string }>();
  const { user, logout } = useAuth();
  const { t } = useTranslation('runs');
  const navigate = useNavigate();
  // Optional: TeamInfo is normally nested under the Outlet of TeamTab, which
  // supplies `team`. Other embeddings must not crash without it.
  const outlet = useOutletContext<TeamTabOutletContext>();
  // The team comes from the shell, which switches it live on
  // `team_member_moved`. Fetching by a `sessionStorage` id cached at mount
  // would keep this view on the old team after a move.
  const team = (outlet?.team ?? null) as TeamWithMembers | null;

  const [leaveOpen, setLeaveOpen] = useState(false);
  // The page decides nothing about a dissolution. The leave goes out after the
  // plain question, and only the refusal of the server (409
  // TEAM_WOULD_DISSOLVE) brings up the dissolution text. The state holds the id
  // of the team the refusal was shown for, so a confirmation never carries over
  // to another team that the shell may switch to while the dialog is open.
  const [refusedTeamId, setRefusedTeamId] = useState<string | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [leaveError, setLeaveError] = useState<string | null>(null);

  // Membership snapshot from mount, read once. While the shell is still
  // fetching, the view shows a spinner. Only a participant without any
  // membership sees the error. The live team itself arrives via the context.
  const [hadMembershipAtMount] = useState(
    () => !!runId && !!sessionStorage.getItem(`clavis_team_for_run_${runId}`),
  );

  const openLeaveConfirmation = () => {
    // Every opening starts with the plain question again.
    setRefusedTeamId(null);
    setLeaveOpen(true);
  };

  // True only while the refusal for this very team is on the screen. Nothing
  // else may send confirm_dissolution.
  const refusalShown = team !== null && refusedTeamId === team.id;

  const handleLeave = async () => {
    if (!user?.organization_slug || !runId || !team) return;
    setLeaving(true);
    setLeaveError(null);
    // The shell hears `team_dissolved` of the leave of a last member, maybe
    // before the answer. This tab started the leave, so the shell must not
    // treat the event as a removal. The mark comes off when the request is over.
    const leavingTeamId = team.id;
    markLeaveStarted(leavingTeamId);
    try {
      if (refusalShown) {
        await leaveTeam(user.organization_slug, runId, team.id, { confirmDissolution: true });
      } else {
        await leaveTeam(user.organization_slug, runId, team.id);
      }
      sessionStorage.removeItem(`clavis_team_for_run_${runId}`);
      // The key is gone, so a later event of this team finds no membership in
      // the shell to send out. A mark that stayed would silence the team for
      // a member who joins it again.
      clearLeaveMark(leavingTeamId);
      if (user.role === 'guest') {
        // A departed guest cannot re-join this run and has no
        // other surface in the app, so end the session cleanly. Navigate to the
        // public Welcome screen before clearing the session. `logout()`
        // re-renders the ProtectedRoute shell, and its /login redirect would
        // otherwise win the race against the navigate.
        navigate('/', { replace: true });
        await logout();
      } else {
        navigate('/dashboard', { replace: true });
      }
    } catch (err) {
      clearLeaveMark(leavingTeamId);
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        err.errorCode === TEAM_WOULD_DISSOLVE
      ) {
        // Not an error: nothing was changed, and the person decides with the
        // text that names the dissolution. A refusal after a confirmed send
        // is shown the same way and waits for a new confirmation.
        setRefusedTeamId(team.id);
        setLeaving(false);
        return;
      }
      setLeaveError(err instanceof ApiError ? err.detail : String(err));
      setLeaving(false);
    }
  };

  if (!team) {
    // The shell asks the server when the stored key is missing, and its answer
    // decides. Only without that answer (an embedding without the shell) the
    // key read at mount decides.
    const membership = outlet?.membership;
    // The lookup failed: no answer, so neither a spinner nor "no membership".
    if (membership === 'error') {
      return <ErrorAlert message={t('team.info.lookupFailed')} onRetry={outlet?.retryMembership} />;
    }
    const waiting = membership === undefined ? hadMembershipAtMount : membership !== 'none';
    return waiting ? (
      <LoadingSpinner />
    ) : (
      <ErrorAlert message={t('team.info.noMembership')} />
    );
  }

  // The guest keeps the sentence about the new join in both texts.
  const isGuest = user?.role === 'guest';
  const leaveMessageKey = refusalShown
    ? isGuest
      ? 'dissolveRefused.participantGuest'
      : 'dissolveRefused.participant'
    : isGuest
      ? 'team.leave.confirmMessageGuest'
      : 'team.leave.confirmMessage';

  return (
    <Stack spacing={3}>
      <Card>
        <CardContent>
          <Stack direction="row" spacing={2} sx={{ alignItems: 'center' }}>
            <Box
              sx={{
                width: 48,
                height: 48,
                borderRadius: '50%',
                bgcolor: team.color ?? 'primary.main',
              }}
            />
            <Box sx={{ flex: 1 }}>
              <Typography variant="h5" component="h1">
                {team.name}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {t('team.info.memberCount', { count: team.member_count })}
              </Typography>
            </Box>
          </Stack>
        </CardContent>
      </Card>

      {/* No share-code block here. The TeamTab layout above the sub-tabs
          already renders it, and a second one would double the code. */}
      {team.members && team.members.length > 0 && (
        <Card>
          <CardContent>
            <Typography variant="subtitle1" component="h2" gutterBottom>
              {t('team.info.membersTitle')}
            </Typography>
            <Divider />
            <List>
              {team.members.map((m) => (
                <ListItem key={m.id} disablePadding sx={{ py: 0.5 }}>
                  <ListItemAvatar>
                    <Avatar><PersonIcon /></Avatar>
                  </ListItemAvatar>
                  <ListItemText primary={memberDisplayName(m.display_name)} />
                </ListItem>
              ))}
            </List>
          </CardContent>
        </Card>
      )}

      <Box>
        {leaveError && <ErrorAlert message={leaveError} />}
        <Button
          color="error"
          variant="outlined"
          startIcon={<LogoutIcon />}
          onClick={openLeaveConfirmation}
        >
          {t('team.leave.button')}
        </Button>
      </Box>

      <Dialog open={leaveOpen} onClose={() => !leaving && setLeaveOpen(false)}>
        <DialogTitle>{t('team.leave.confirmTitle')}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {t(leaveMessageKey)}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setLeaveOpen(false)} disabled={leaving}>
            {t('team.leave.cancel')}
          </Button>
          <Button color="error" onClick={handleLeave} disabled={leaving}>
            {t('team.leave.confirm')}
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};

export default TeamInfo;
