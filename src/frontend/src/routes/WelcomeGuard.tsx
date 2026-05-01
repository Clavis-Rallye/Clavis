import React, { useEffect, useState } from 'react';
import { Alert, Box } from '@mui/material';
import { useTranslation } from 'react-i18next';
import { Navigate, useLocation, useParams } from 'react-router';
import { useAuth } from '../auth/useAuth';
import { useAdoptResolvedTeam } from '../wizard/WizardStateContext';
import ClavisCircularProgress from '../components/ClavisCircularProgress';
import { fetchByCode } from '../services/publicRuns';
import {
  teamPendingPath,
  useResolveJoinDestination,
} from './useResolveJoinDestination';
import { useDelayedFlag } from '../hooks/useDelayedFlag';

/**
 * The next step of an authenticated user who lands on `/join/:code`: the
 * ProfileSetup step. Exported for unit tests.
 */
// eslint-disable-next-line react-refresh/only-export-components
export function getNextStep(code: string): string {
  return `/join/${code}/profile`;
}

/**
 * Ask the public lookup whether the run belongs to an archived
 * organization. A signed-in visitor never reaches WelcomeStep, and the team
 * lookup answers 404 for such a run, which reads as "no team". So the guard
 * asks first and stops on the lookup's ORG_ARCHIVED refusal.
 */
function useOrgArchived(code: string | undefined): 'loading' | 'archived' | 'ok' {
  const [state, setState] = useState<{ code: string; archived: boolean } | null>(null);
  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    let archived = false;
    fetchByCode(code, undefined, {
      onOrgArchived: () => {
        archived = true;
      },
    }).then(() => {
      if (!cancelled) setState({ code, archived });
    });
    return () => {
      cancelled = true;
    };
  }, [code]);
  if (!code) return 'ok';
  if (state?.code !== code) return 'loading';
  return state.archived ? 'archived' : 'ok';
}

interface WelcomeGuardProps {
  children: React.ReactNode;
}

const WelcomeGuard: React.FC<WelcomeGuardProps> = ({ children }) => {
  const { isAuthenticated, isLoading } = useAuth();
  const { code } = useParams<{ code?: string }>();
  const location = useLocation();
  const destination = useResolveJoinDestination(
    isAuthenticated && code ? code : undefined,
  );
  useAdoptResolvedTeam(destination);
  const { t } = useTranslation('onboarding');
  const orgState = useOrgArchived(isAuthenticated && code ? code : undefined);
  const showLoadingIndicator = useDelayedFlag(
    destination.kind === 'loading' || orgState === 'loading',
  );

  // No redirect while auth is still resolving. The children render, and the
  // view settles once useAuth does.
  if (isLoading) return <>{children}</>;

  if (isAuthenticated) {
    if (code) {
      // This is the first stop of a browser-back chain, so the team check
      // belongs here too: a member of a still-unnamed team must never walk
      // through profile setup and the "create or join" choice again. Hold the
      // render until my-team answers.
      if (destination.kind === 'loading' || orgState === 'loading') {
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
      if (orgState === 'archived') {
        return (
          <Box sx={{ padding: '40px' }}>
            <Alert severity="warning" role="status">
              {t('join.errors.org_archived')}
            </Alert>
          </Box>
        );
      }
      if (destination.kind === 'team-pending') {
        return <Navigate to={teamPendingPath(code, destination.isCreator)} replace />;
      }
      // A logged-in user skips Welcome. The search query goes along, so
      // deep-link params like `?team=<code>` from the QR share survive the
      // redirect chain.
      return <Navigate to={{ pathname: getNextStep(code), search: location.search }} replace />;
    }
    // Logged-in users on `/` go straight to the dashboard.
    return <Navigate to="/dashboard" replace />;
  }

  return <>{children}</>;
};

export default WelcomeGuard;
