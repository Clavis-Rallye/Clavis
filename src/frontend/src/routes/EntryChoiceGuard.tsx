import React from 'react';
import { Box } from '@mui/material';
import { Navigate, useParams } from 'react-router';

import { useAuth } from '../auth/useAuth';
import { useAdoptResolvedTeam } from '../wizard/WizardStateContext';
import ClavisCircularProgress from '../components/ClavisCircularProgress';
import { getNextAfterAuth } from './ProfileSetupRedirect';
import {
  teamPendingPath,
  useResolveJoinDestination,
} from './useResolveJoinDestination';
import { useDelayedFlag } from '../hooks/useDelayedFlag';

interface EntryChoiceGuardProps {
  children: React.ReactNode;
}

/**
 * Guards `/auth-choice` and `/join/:code/auth-choice`:
 * logged-in users skip the entry-choice screen.
 *
 * Later-team-joining: if the user is already a member of a fully-named team
 * in this run, jump them directly into the run shell. Otherwise fall back
 * to `getNextAfterAuth` (dashboard or /join/:code/team).
 */
const EntryChoiceGuard: React.FC<EntryChoiceGuardProps> = ({ children }) => {
  const { isAuthenticated, isLoading } = useAuth();
  const { code } = useParams<{ code?: string }>();
  const destination = useResolveJoinDestination(
    isAuthenticated && code ? code : undefined,
  );
  useAdoptResolvedTeam(destination);
  const showLoadingIndicator = useDelayedFlag(destination.kind === 'loading');

  if (isLoading) return <>{children}</>;
  if (!isAuthenticated) return <>{children}</>;
  if (!code) return <Navigate to={getNextAfterAuth(undefined)} replace />;
  if (destination.kind === 'loading') {
    return showLoadingIndicator ? (
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          minHeight: '100vh',
        }}
      >
        <ClavisCircularProgress indeterminate />
      </Box>
    ) : null;
  }
  if (destination.kind === 'team-ready') {
    return <Navigate to={`/runs/${destination.runId}/participant/stations`} replace />;
  }
  // A member of a still-unnamed team skips the team flow's entry and returns
  // to their own step (invite for the creator, waiting for a joiner).
  if (destination.kind === 'team-pending') {
    return <Navigate to={teamPendingPath(code, destination.isCreator)} replace />;
  }
  return <Navigate to={getNextAfterAuth(code)} replace />;
};

export default EntryChoiceGuard;
