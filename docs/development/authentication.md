# Authentication & Authorization

## Overview

The auth system has three layers:

1. **Authentication** — Who are you? (JWT tokens from login)
2. **Tenant isolation** — Which org do you belong to? (slug resolution + membership check)
3. **Authorization** — What can you do? (RBAC role hierarchy)

All three are implemented as FastAPI dependencies that compose via `Depends()`.

## Authentication Flow

### Registration

`POST /api/auth/register`

Creates a new organization and its first user (as `org_admin`):

1. Validate password against policy (min length, 3-of-4 criteria)
2. Hash password with Argon2id
3. Generate org slug from name
4. Create `Organization` + `User` in same transaction
5. Emit the `user_created` audit event
6. Issue JWT in HttpOnly cookie

### Login

`POST /api/auth/login`

The checks run in this order. The first one that fails answers.

1. Look up the account by email. A missing account gets 401 `INVALID_CREDENTIALS`.
2. Check the lock. A lock that still runs gets 423 `ACCOUNT_LOCKED`. A lock that has run out is cleared.
3. Check the password. A wrong password adds to the failed count. It gets 423 `ACCOUNT_LOCKED` when the count reaches the limit, and 401 `INVALID_CREDENTIALS` otherwise. This holds for a disabled account too, so a disabled account can become locked.
4. Check that the account is enabled. A disabled account gets 401 `ACCOUNT_DISABLED`, and only after a correct password.
5. Check the organization. An archived organization gets 403 `ORG_ARCHIVED`.
6. If TOTP is enabled, return `{"status": "2fa_required"}` and no session. The response sets the proof cookie of the password step.
7. If the account is an org admin without TOTP and the instance enforces 2FA, return `{"status": "2fa_setup_required"}`.
8. Otherwise the sign-in completes. The failed count is reset, the account is checked for a change made in the meantime, and the session cookie is set.

A completed sign-in updates `last_login` and emits a `user_login` audit event.

On failed login:
- Increment `failed_login_attempts`
- If attempts >= `MAX_LOGIN_ATTEMPTS` (default 5): set `locked_until` to now + `LOCKOUT_DURATION_MINUTES` (default 15)
- Emit `user_login_failed` audit event

### 2FA Flow (if enabled)

After the initial login returns `2fa_required`:

1. The client sends a TOTP code or a recovery code to `POST /api/auth/2fa/totp/authenticate`
2. Backend checks the proof of the password step first. A request without a valid proof gets 401 `PASSWORD_STEP_REQUIRED`
3. Backend checks the code against the TOTP secret or the hashed recovery codes
4. On success: issue JWT, update `last_login`
5. Recovery codes: 8 single-use codes, Argon2id-hashed, generated during 2FA setup

## Token Management

### JWT Structure

| Claim | Value |
|-------|-------|
| `sub` | User UUID (string) |
| `role` | User's role value |
| `org_id` | Organization UUID (string) |
| `exp` | Expiration timestamp |
| `iat` | Issued-at timestamp |
| `sv` | The account's session version at mint time (see [Session Version](#session-version)) |

Algorithm: HS256 (HMAC-SHA256). Secret key: min 32 characters (validated at startup).

### Token Delivery

Tokens are delivered in HttpOnly cookies:

```
Set-Cookie: access_token=<jwt>; HttpOnly; Secure; SameSite=Lax; Path=/
```

API clients can alternatively use the `Authorization: Bearer <token>` header. The `get_current_user` dependency checks cookies first, then the header.

### Session Timeout

There is no refresh token. Session inactivity is enforced by the token's own
expiry: every token is minted with `exp = now + SESSION_TIMEOUT_MINUTES`, and
an expired token is rejected at decode time.

For cookie sessions the window slides: `get_current_user` re-mints the cookie
on every authenticated request, so the timeout restarts from the user's last
activity instead of expiring a fixed window after login. Bearer (API) clients
manage their own tokens and are not re-minted. Default timeout: 60 minutes of
inactivity, configurable via `SESSION_TIMEOUT_MINUTES`.

### Session Version

Every account carries a **session version** — an integer that starts at 0
(`users.session_version`, `instance_admins.session_version`). Every token
carries the account's version at mint time as the `sv` claim, and
`get_current_user`, `require_instance_admin` and `ws_authenticate` compare
the claim with the account's current version on every request. A mismatch
means the token predates the account's last password change — that session
was ended by it. HTTP requests answer 401 `SESSION_REVOKED` (any realm, user
and operator alike); a WebSocket connect is closed with 1008 "Session
revoked" before `accept()`. A token without the `sv` claim counts as version
0, so tokens issued before this mechanism existed stay valid until the
account's first password change.

**Which changes increment the version** — every way an existing account's
password is replaced:

- the organization password change (`POST /api/auth/password-change`),
- the password reset by mail link,
- the operator's own password change in the console
  (`POST /api/instance/auth/password`), and
- the `set-password` server command for operators.

Setting a *first* password — registration, invitation acceptance, account
creation — changes no version: a first password has no earlier sessions.

**The changing session continues.** The increment rides in the same database
update that stores the new password hash. The password change and the operator
console change then set a fresh cookie carrying the new version, and it must
be the last `Set-Cookie` for the cookie name in the response: the sliding
re-mint of `get_current_user` has already set a cookie with the *old* version
earlier in the same request, and the fresh cookie replaces it, so the browser
ends up holding a token for the new version. The reset by mail sets no cookie
at all — it signs nobody in, so every session issued before the reset is
refused.

A password change also closes the account's already-open WebSocket
connections — see [realtime.md](realtime.md).

## FastAPI Dependencies

### `get_current_user`

The core auth dependency. Every protected endpoint depends on this (directly or via wrappers).

Steps:
1. Extract token from cookie (`access_token`) or Authorization header
2. Decode JWT, validate signature and expiration
3. Load user from database (unscoped lookup — org unknown at this point)
4. Check `is_active` flag
5. Check `locked_until` (clear expired locks automatically)
6. Check session inactivity timeout (the token's own `exp`)
7. Check the token's `sv` claim against the account's `session_version` — a mismatch answers 401 `SESSION_REVOKED` (see [Session Version](#session-version))
8. Return `User` object

### `get_org_from_slug`

Resolves org from URL path and validates membership:

1. Extract `{slug}` from URL path
2. Look up `Organization` by slug
3. Verify `current_user.organization_id == org.id`
4. Return `Organization` object

This is the tenant isolation boundary for all `/api/orgs/{slug}/*` endpoints. It admits a caller only to the organization of their own account. Together with `TenantScopedRepository`, which filters every query by its `organization_id`, it is the whole tenant boundary.

### `require_role(min_role)`

Factory function that creates a dependency checking minimum role level:

```python
@router.post("/")
async def create_rallye(
    current_user: User = Depends(require_role("creator")),
    ...
):
```

## Role-Based Access Control (RBAC)

### Role Hierarchy

Cumulative — each role inherits all permissions of roles below it:

```
guest (0) < participant (1) < coordinator (2) < creator (3) < org_admin (4)
```

Implemented as `IntEnum` in `app/auth/rbac.py`. The `require_role()` dependency compares integer levels:

```python
if user_level < min_level:
    raise HTTPException(403, f"Requires {min_role} role or higher")
```

### Role Capabilities

| Role | Can do |
|------|--------|
| `guest` | View public rallye info |
| `participant` | Join runs, submit answers, view feed, chat in team |
| `coordinator` | View all teams, moderate chat, manage feed events |
| `creator` | Create/edit rallyes, stations, challenges |
| `org_admin` | Manage users, roles, organization settings |

### PII Filtering

Response models filter fields based on the caller's role:
- **Coordinator**: sees names but not emails
- **Org Admin**: sees all fields including emails

This is handled in `app/auth/pii_filter.py` and applied in response serialization.

## Password Security

### Argon2id Configuration

Per BSI TR-02102 recommendations:

| Parameter | Value |
|-----------|-------|
| Algorithm | Argon2id |
| time_cost (t) | 3 |
| memory_cost (m) | 65536 (64 MiB) |
| parallelism (p) | 4 |
| salt_len | 16 bytes (128 bits) |
| hash_len | 32 bytes (256 bits) |

### Password Policy

- Minimum length: 8 characters (configurable via `PASSWORD_MIN_LENGTH`)
- Must meet 3 of 4 criteria: uppercase, lowercase, digit, special character (configurable via `PASSWORD_CRITERIA_COUNT`)
- No password reuse (compared against `last_password_hash`)

### Account Lockout

- Max failed attempts: 5 (configurable via `MAX_LOGIN_ATTEMPTS`)
- Lockout duration: 15 minutes (configurable via `LOCKOUT_DURATION_MINUTES`)
- Expired locks are cleared automatically during the next auth check
- The lock answer (423 `ACCOUNT_LOCKED`) comes before the password check.
- A password check during a lock would turn the lock into a password oracle.
- The order reveals nothing new. The public registration already tells whether an address has an account.
- A wrong password on a disabled account counts as a failed attempt.

## Two-Factor Authentication

### TOTP (Time-based One-Time Password)

- RFC 6238 compliant (via `pyotp`)
- Setup returns a QR code URI + 8 recovery codes
- Recovery codes are Argon2id-hashed and single-use
- Managed via four routes under `/api/auth/2fa`:
  - `POST /totp/setup` returns the provisioning URI and 8 recovery codes
  - `POST /totp/verify` confirms the setup with a first code
  - `POST /totp/authenticate` is the second step of the sign-in and takes a TOTP code or a recovery code
  - `DELETE /totp` switches 2FA off

## WebSocket Authentication

WebSocket endpoints cannot use cookies in the initial handshake across all browsers. Instead, the client fetches a short-lived token from `GET /api/auth/ws-token` before each connect and reconnect, and passes it as a query parameter. The token is a JWT with the claims of the session, valid for 5 minutes (`WS_TOKEN_TTL` in `app/auth/tokens.py`):

```
wss://example.com/api/orgs/{slug}/runs/{run_id}/ws?token=<ws-token>
```

The `ws_authenticate()` function in `app/auth/ws_dependencies.py` handles token extraction, validation, user lookup, and org membership check — same logic as the HTTP auth dependencies but adapted for the WebSocket handshake. On failure, the WebSocket is closed with an appropriate status code before `accept()`. That includes the session version: a token whose `sv` claim predates the account's last password change is closed with 1008 "Session revoked" (see [Session Version](#session-version)), and a WebSocket connection that is already open when the password changes is closed with 4000 — see [realtime.md](realtime.md).
