# Roles and Permissions — App Implementation

> **Scope.** The roles form a cumulative "onion": Guest Participant → Registered Participant → Coordinator → Creator → Org Admin. Each role holds the capabilities of the roles inside it.

This file documents the **App-specific enforcement** of that model: authentication, password policy, the permission matrix, PII field-level visibility, and the instance operator. This is the identity the App places above every organization. The role onion does not model it, because it is not an organization role.

## Authentication Requirements

| User Type | Auth Method | 2FA Required |
|-----------|-------------|-------------|
| Guest Participant | Token/session (no account) | No |
| Registered Participant | Password-based login | No |
| Coordinator | Password-based login | No |
| Creator | Password-based login | No |
| Org Admin / City staff | Password-based login | **Yes**, a code from an authenticator app (TOTP). `ENFORCE_ORG_ADMIN_TOTP=false` lifts the requirement |

### Password Policy

- Configurable complexity (min 8 chars, 3 of 4 complexity criteria)
- Configurable failed attempt lockout
- Encrypted transmission and hashed storage (BSI TR-02102-1)
- Password change available at any time
- Last password cannot be reused
- Password-forgot function: a reset link by email, valid for 1 hour and usable once
- Auto-logout after a configurable inactivity period: `SESSION_TIMEOUT_MINUTES`, default 60, at most 43200 (30 days)

## Permission Matrix

| Action | Guest | Participant | Coordinator | Creator | Org Admin |
|--------|:-----:|:-----------:|:-----------:|:-------:|:---------:|
| Join run | x | x | x | x | x |
| Submit challenges | x | x | x | x | x |
| View feed | x | x | x | x | x |
| Team chat | x | x | x | x | x |
| View history | | x | x | x | x |
| Manage runs | | | x | x | x |
| Score submissions | | | x | x | x |
| Post announcements | | | x | x | x |
| Join team chat (on request) | | | x | x | x |
| Move members between teams (live runs) | | | x | x | x |
| Create/edit content | | | | x | x |
| Archive content | | | | x | x |
| Manage roles | | | | | x |
| Restore/delete content | | | | | x |
| Purge participant media | | | | | x |
| Org settings | | | | | x |

## The Instance Operator — Outside Every Organization

The operator (instance admin) is not a role in the onion: it is a separate
identity in `instance_admins` — not a `users` row — and belongs to no
organization. Accounts exist only through the server-side CLI
(`python -m app.cli instance-admin create`, see
[deployment/README.md](../../deployment/README.md)); no HTTP route creates
one. Login always requires password **and** TOTP.

What the operator can do, from `/api/instance/…`:

- List every organization — name, slug, content languages, created at,
  `is_archived`, and the aggregate counts of users, rallyes and runs.
  Metadata and counts only: the list never carries user, team or submission
  data, so the operator holds none of the PII scopes below.
- Create an organization and invite its first org admin through the standard
  invitation flow; after the bootstrap organization exists, the operator is
  the only creator (`POST /api/organizations` refuses every organization
  token — org_admin included — with the coded 403 `ORG_CREATE_INSTANCE_ONLY`).
- Edit an organization's name and content languages — never its slug, which
  stays fixed for the organization's life — and invite an organization admin
  to replace a lost one (an open invitation for the same address is renewed,
  not duplicated).
- Archive an organization and restore it — an instance-level off switch for a
  tenant, not a deletion (see
  [security-and-compliance.md](../development/security-and-compliance.md)).
- Schedule an archived organization's hard deletion, and cancel it by
  restoring the organization before the grace period ends (see
  [security-and-compliance.md](../development/security-and-compliance.md)).
- Read its own audit log (`instance.*` events only, never an organization's
  own log), maintain the instance's own imprint, privacy and accessibility
  texts, and change its own password.

What it cannot do: anything inside an organization. No role, no membership, no
runs, no content, no user management — which is why the permission matrix
above has no operator column. Disabling an account, re-enabling it, resetting
its TOTP enrollment and setting its password without the current one are
server commands, never HTTP routes — same trust anchor as account creation
(see [deployment/README.md](../../deployment/README.md)).

**Tokens never cross realms.** An operator JWT carries `scope: "instance"`
and deliberately no `org_id` and no `role`; organization tokens carry no scope
claim. `get_current_user` rejects instance-scoped tokens with 403
(*Instance operator token not valid for organization endpoints*),
`require_instance_admin` rejects organization tokens with 403 (*Organization
token not valid for instance endpoints*) — a valid token for the wrong realm
is a permission problem, not the anonymous 401. Each realm has its own cookie
(`access_token` vs `instance_token`), so an operator who is also an
organization member keeps both sessions side by side in one browser; logging
in or out of one never touches the other.

## PII Field Visibility

**Requirement:** The API must enforce field-level access control over personally identifiable information. Each role may only see the PII necessary for its function. This applies to both API response serialization and database query scoping.

| PII Scope | Guest | Participant | Coordinator | Creator | Org Admin |
|-----------|-------|-------------|-------------|---------|-----------|
| Own profile data | x | x | x | x | x |
| Own team members (display name) | x | x | x | x | x |
| All participants in a run (name, team) | | | x | x | x |
| Participant email / account details | | | | | x |
| Audit log actor references | | | | | x |

Design considerations for implementation:

- API responses must filter PII fields based on the caller's role — never rely solely on the frontend to hide data
- Guest participant data is ephemeral (run-scoped, no persistent identity)
- Coordinator needs participant visibility for run management but not account-level details
- Audit logs reference user actions but must use anonymizable identifiers (see data protection architecture)
