import React from 'react';
import { Navigate, useLocation } from 'react-router';
import { useAuth } from './useAuth';
import { CircularProgress, Box } from '@mui/material';
import { useTranslation } from 'react-i18next';
import AccessDenied from '../pages/AccessDenied';
import { navigation } from '../api/client';

export type Role = 'guest' | 'participant' | 'coordinator' | 'creator' | 'org_admin';

const ROLE_HIERARCHY: Role[] = ['guest', 'participant', 'coordinator', 'creator', 'org_admin'];

interface ProtectedRouteProps {
  children: React.ReactNode;
  minRole?: Role;
}

function hasMinRole(userRole: Role, min: Role): boolean {
  return ROLE_HIERARCHY.indexOf(userRole) >= ROLE_HIERARCHY.indexOf(min);
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ children, minRole }) => {
  const { isAuthenticated, isLoading, user } = useAuth();
  const location = useLocation();
  const { t } = useTranslation('common');

  // The API client already navigates to the login with a reason (session
  // revoked, account disabled, organization archived). Keep the spinner and
  // let that navigation land, so no bare /login replaces it.
  if (isLoading || (!isAuthenticated && navigation.pending)) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          minHeight: "100vh"
        }}>
        {/* Named for screen readers, because it shows on every full page load. */}
        <CircularProgress aria-label={t('loading')} />
      </Box>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (minRole && user && !hasMinRole(user.role, minRole)) {
    return <AccessDenied />;
  }

  return <>{children}</>;
};

export default ProtectedRoute;
