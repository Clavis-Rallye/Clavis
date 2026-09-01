import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { instanceLogout, instanceMe } from '../../api/instance';
import { ApiError } from '../../api/client';
import { clearApiCache } from '../../utils/apiCache';
import type { InstanceMeResponse } from '../../api/types';

// Remembers for this tab that an operator session existed, so its end can
// be told apart from a first visit.
const SIGNED_IN_KEY = 'clavis_instance_signed_in';
function markSignedIn(value: boolean): void {
  try {
    if (value) sessionStorage.setItem(SIGNED_IN_KEY, '1');
    else sessionStorage.removeItem(SIGNED_IN_KEY);
  } catch {
    /* storage unavailable: the login then shows no hint */
  }
}
function wasSignedIn(): boolean {
  try {
    return sessionStorage.getItem(SIGNED_IN_KEY) === '1';
  } catch {
    return false;
  }
}

type InstanceAuthStatus = 'loading' | 'authenticated' | 'unauthenticated' | 'forbidden';

interface UseInstanceAuthResult {
  status: InstanceAuthStatus;
  admin: InstanceMeResponse | null;
  /** Re-run the session probe, e.g. right after a successful sign-in. */
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}

/**
 * Probes the operator session with `GET /api/instance/auth/me` on mount.
 *
 * A 401 sends the operator to their own login, `/instance/login`, never to the
 * organization login. A 403 means the connection carries a valid
 * organization-member session instead of an operator one (the cross-realm check
 * of `require_instance_admin`). The page then stays on `/instance` and shows
 * "no operator access" with a link to the regular login, so the visitor never
 * bounces between the two logins.
 */
export function useInstanceAuth(): UseInstanceAuthResult {
  const navigate = useNavigate();
  const [status, setStatus] = useState<InstanceAuthStatus>('loading');
  const [admin, setAdmin] = useState<InstanceMeResponse | null>(null);
  // The number of the newest probe. A probe acts on its answer only while it
  // is still the newest. Each new probe and the mount effect's cleanup bump the
  // counter, so a replaced probe, a cleaned-up effect and an unmounted component
  // stay silent. React.StrictMode mounts, cleans up and mounts again in a
  // development build. Without the guard the 401 branch could then run twice:
  // the first probe would consume the signed-in flag, and the second would
  // navigate without reason=session_expired and lose the notice.
  const probeSeq = useRef(0);

  // Retires every probe started so far. The mount effect's cleanup calls it,
  // because an unmounted component must not navigate.
  const retireProbes = useCallback(() => {
    probeSeq.current++;
  }, []);

  const refresh = useCallback(async () => {
    const seq = ++probeSeq.current;
    setStatus('loading');
    try {
      const me = await instanceMe();
      if (seq !== probeSeq.current) return;
      setAdmin(me);
      setStatus('authenticated');
      markSignedIn(true);
      // A confirmed session drops what a previous one left in the /api/ cache:
      // the operator may just have signed in as another account.
      await clearApiCache();
    } catch (err) {
      if (seq !== probeSeq.current) return;
      setAdmin(null);
      if (err instanceof ApiError && err.status === 403) {
        setStatus('forbidden');
        return;
      }
      setStatus('unauthenticated');
      // An operator who was signed in and now is not: the session ran out.
      // The login page must say so.
      const expired = wasSignedIn();
      markSignedIn(false);
      // A 401 ended the session, so its cached /api/ answers go with it. A
      // failure without an answer only means offline, and the cache keeps serving.
      if (err instanceof ApiError && err.status === 401) {
        await clearApiCache();
      }
      navigate(expired ? '/instance/login?reason=session_expired' : '/instance/login', { replace: true });
    }
  }, [navigate]);

  useEffect(() => {
    void refresh();
    return retireProbes;
  }, [refresh, retireProbes]);

  // The operator cookie is HttpOnly, so only the server can clear it. Leaving
  // the page on a failed logout would leave a live cookie while the UI claims
  // the operator is signed out. So only a successful answer clears local state
  // and leaves the page. The caller shows a failure, and the session stays.
  const signOut = useCallback(async () => {
    await instanceLogout();
    markSignedIn(false);
    // The operator's cached /api/ answers do not outlive their session.
    await clearApiCache();
    setAdmin(null);
    setStatus('unauthenticated');
    navigate('/instance/login', { replace: true });
  }, [navigate]);

  return { status, admin, refresh, signOut };
}
