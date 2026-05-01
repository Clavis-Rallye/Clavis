import React from 'react';
import { Navigate, useLocation, useParams } from 'react-router';

import { useAuth } from '../auth/useAuth';
import { useAdoptResolvedTeam } from '../wizard/WizardStateContext';
import {
  teamPendingPath,
  useResolveJoinDestination,
} from './useResolveJoinDestination';

/**
 * The path after profile setup or login for an authenticated user, when no
 * pre-check is possible (no run code, or the pre-check is still loading).
 * `/dashboard` without a code, else `/join/:code/team`, which redirects to
 * /team/find. The team flow is the only option.
 *
 * With a run code, callers should prefer the async `useResolveJoinDestination`
 * hook, which also catches the `team-ready → /runs/:runId/participant` shortcut.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function getNextAfterAuth(code: string | undefined): string {
  if (!code) return '/dashboard';
  return `/join/${code}/team`;
}

interface ProfileSetupRedirectProps {
  children: React.ReactNode;
}

/**
 * Guards `/register` and `/join/:code/profile`: a logged-in user skips profile
 * setup and goes to the next step. A member of a fully named team of this run
 * goes straight into the run shell instead of FindTeam. That pre-check runs
 * only with a run code.
 */
const ProfileSetupRedirect: React.FC<ProfileSetupRedirectProps> = ({
  children,
}) => {
  const { isAuthenticated, isLoading } = useAuth();
  const { code } = useParams<{ code?: string }>();
  const location = useLocation();
  const destination = useResolveJoinDestination(
    isAuthenticated && code ? code : undefined,
  );
  useAdoptResolvedTeam(destination);

  if (isLoading) return <>{children}</>;
  if (!isAuthenticated) return <>{children}</>;
  if (!code) return <Navigate to="/dashboard" replace />;
  if (destination.kind === 'loading') return null;
  if (destination.kind === 'team-ready') {
    return <Navigate to={`/runs/${destination.runId}/participant/stations`} replace />;
  }
  // A member of a still-unnamed team goes back to their own team step, not
  // through FindTeam: the choice there would create a second team for the
  // creator and offer a joiner the team they are already in.
  if (destination.kind === 'team-pending') {
    return <Navigate to={teamPendingPath(code, destination.isCreator)} replace />;
  }
  // 'no-team' continues to the team flow. It goes straight to /team/find,
  // because the /team → /team/find redirect in App.tsx would drop the query
  // string, and deep-link params like `?team=<code>` from the QR share must
  // reach FindTeamStep's preset-code handler.
  return <Navigate to={{ pathname: `/join/${code}/team/find`, search: location.search }} replace />;
};

export default ProfileSetupRedirect;
