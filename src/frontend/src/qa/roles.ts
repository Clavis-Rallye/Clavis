/**
 * The role and the device a page knows, for the bar's badges.
 *
 * The organization role comes from the session. The operator console keeps
 * its own session that the bar does not read, so under `/instance`, except its
 * login page, the page counts as the operator's. Everything else is signed out.
 */
type QaRole = 'operator' | 'org_admin' | 'creator' | 'coordinator' | 'participant' | 'guest' | 'signed_out';

const QA_ROLES: readonly QaRole[] = [
  'operator',
  'org_admin',
  'creator',
  'coordinator',
  'participant',
  'guest',
  'signed_out',
];

export function isQaRole(value: string | null | undefined): value is QaRole {
  return typeof value === 'string' && (QA_ROLES as readonly string[]).includes(value);
}

export function pageRole(user: { role: string } | null | undefined, pathname: string): QaRole {
  const underInstance = pathname === '/instance' || pathname.startsWith('/instance/');
  if (underInstance && pathname !== '/instance/login') return 'operator';
  if (user && isQaRole(user.role)) return user.role;
  return 'signed_out';
}

type QaDevice = 'phone' | 'desktop';

/**
 * The device the page runs on: a phone has a coarse pointer and a narrow
 * window. A tablet in landscape counts as a desktop.
 */
export function pageDevice(): QaDevice {
  const coarse = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches;
  return coarse && window.innerWidth < 900 ? 'phone' : 'desktop';
}

/** A guest step is also right for a participant with an account: both play the run. */
export function rolesMatch(expected: string, actual: QaRole): boolean {
  return expected === actual || (expected === 'guest' && actual === 'participant');
}
