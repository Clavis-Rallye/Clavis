import React, { useEffect, useState } from 'react';
import { Navigate } from 'react-router';
import { useAuth } from '../auth/useAuth';
import { getMyRunHistory } from '../api/me';
import LazyRoute from '../components/LazyRoute';
import LoadingSpinner from '../components/LoadingSpinner';

const Dashboard = React.lazy(() => import('../pages/Dashboard'));

// Run states in which a team still plays. A paused run continues, so its
// members belong in it, not on a page that asks for a join code.
const PLAYING = new Set(['active', 'paused']);

/**
 * Where a participant or guest lands instead of the staff dashboard.
 *
 * The sign-in, the welcome guard, the registration and every unknown route
 * send a signed-in person to /dashboard. The staff dashboard would show a
 * participant "Noch keine Rallyes erstellt" and no join code field, also while
 * their team is playing. So the run history decides. A run still in play sends
 * the person into that run. An ended run sends them to /run-history. Otherwise
 * they go to /no-runs, which explains how to join with a code and offers the field.
 */
function ParticipantLanding() {
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMyRunHistory()
      .then(({ items }) => {
        if (cancelled) return;
        // Newest first, as the API sorts it.
        const playing = items.find((item) => PLAYING.has(item.state));
        if (!playing) {
          // A person whose runs have ended has their team's final standings to
          // look at. "Noch in keinem Lauf" would be wrong and show no way there.
          // A run that has not started is no history.
          setTarget(items.some((item) => item.state === 'ended') ? '/run-history' : '/no-runs');
          return;
        }
        // The run shell reads the team from here. A fresh sign-in on a new
        // device has no join flow that wrote it.
        try {
          sessionStorage.setItem(`clavis_team_for_run_${playing.run_id}`, playing.team_id);
        } catch {
          // sessionStorage disabled: the shell still opens, without the team
        }
        setTarget(`/runs/${playing.run_id}/participant/stations`);
      })
      .catch(() => {
        // The code field is the way forward in every case.
        if (!cancelled) setTarget('/no-runs');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!target) return <LoadingSpinner />;
  return <Navigate to={target} replace />;
}

/** /dashboard: the staff dashboard for staff, a run or /no-runs for participants. */
const DashboardRoute: React.FC = () => {
  const { user } = useAuth();
  if (user && (user.role === 'participant' || user.role === 'guest')) {
    return <ParticipantLanding />;
  }
  return (
    <LazyRoute>
      <Dashboard />
    </LazyRoute>
  );
};

export default DashboardRoute;
