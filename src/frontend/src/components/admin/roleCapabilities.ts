// Read-only capability map for the role info panel. Not a permissions model,
// but a plain-language visualization derived from the RBAC ladder
// (app/auth/rbac.py: guest < participant < coordinator < creator < org_admin).

export type RoleName = 'guest' | 'participant' | 'coordinator' | 'creator' | 'org_admin';

/** Capabilities shown in the panel, each with the minimum role that grants it.
 * `key` maps to an i18n key `admin:users.capabilities.<key>`. */
export const ROLE_CAPABILITIES: { key: string; minRole: RoleName }[] = [
  // A guest joins a run by code and chats with the team. Everything else needs
  // a registered role.
  { key: 'joinRuns', minRole: 'guest' },
  { key: 'teamChat', minRole: 'guest' },
  { key: 'controlRuns', minRole: 'coordinator' },
  { key: 'scoreSubmissions', minRole: 'coordinator' },
  { key: 'seeStatistics', minRole: 'coordinator' },
  { key: 'exportData', minRole: 'coordinator' },
  { key: 'manageContent', minRole: 'creator' },
  { key: 'manageUsers', minRole: 'org_admin' },
  { key: 'orgSettings', minRole: 'org_admin' },
];

const ROLE_ORDER: Record<RoleName, number> = {
  guest: 0,
  participant: 1,
  coordinator: 2,
  creator: 3,
  org_admin: 4,
};

/** True if `role` sits at or above `minRole` on the RBAC ladder. */
export function roleHasCapability(role: RoleName, minRole: RoleName): boolean {
  return ROLE_ORDER[role] >= ROLE_ORDER[minRole];
}
