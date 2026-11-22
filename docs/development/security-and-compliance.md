# Security & Compliance

## Structured Logging

The logging system uses structlog with four separate JSON log streams, each written to its own file:

| Category | File | Level | Purpose |
|----------|------|-------|---------|
| `audit` | `audit.log` | INFO | Security-relevant events (login, role change, data deletion) |
| `app` | `application.log` | INFO | Business logic events (run started, submission received) |
| `error` | `error.log` | ERROR | Exceptions and error conditions |
| `access` | `access.log` | INFO | HTTP request/response logs |

Configuration lives in `app/logging.py`. Each category has its own Python `logging.Logger` with a dedicated `TimedRotatingFileHandler` (daily rotation at midnight UTC). Loggers don't propagate to root to avoid cross-contamination.

### Log Processing Pipeline

Every log entry passes through these structlog processors in order:

1. **`merge_contextvars`** — Injects request-scoped context (request_id, user info)
2. **`add_log_level`** — Adds `level` field
3. **`add_logger_name`** — Adds `logger` field
4. **`TimeStamper`** — Adds ISO 8601 UTC timestamp
5. **`add_service_identifier`** — Adds `"service": "clavis-backend"`
6. **`add_log_category`** — Maps logger name to category (audit/app/error/access)
7. **`filter_sensitive_fields`** — Redacts sensitive data
8. **`format_exc_info`** — Formats exception tracebacks

### Sensitive Field Redaction

Any log key containing these substrings (case-insensitive) has its value replaced with `[REDACTED]`:

```
password, token, secret, authorization, cookie,
totp_secret, jwt_secret_key, hmac_audit_key, access_key, secret_key
```

This applies to all log categories, not just audit.

### Request Context

The request context middleware (`app/middleware/request_context.py`) binds a `request_id` to structlog's contextvars at the start of each request. This ID:

- Comes from the `X-Request-ID` header if present, otherwise auto-generated
- Is returned in the `X-Request-ID` response header
- Appears in every log entry produced during that request
- Enables correlating logs across the audit, app, and error streams

### Development vs Production

In development mode, a colored console renderer is added to all loggers for human-readable stdout output. In production, only JSON file output is used.

## Audit Logging with HMAC Integrity Chain

The audit service (`app/services/audit.py`) produces tamper-evident audit logs. Each entry includes an HMAC-SHA256 hash that chains to the previous entry.

### How the Chain Works

```
Entry 1: hash = HMAC(serialize(entry1) + "|" + "0"*64)
Entry 2: hash = HMAC(serialize(entry2) + "|" + hash_of_entry_1)
Entry 3: hash = HMAC(serialize(entry3) + "|" + hash_of_entry_2)
...
```

- **HMAC key**: `HMAC_AUDIT_KEY` environment variable (min 32 chars)
- **Genesis hash**: `"0" * 64` (start of chain)
- **Serialization**: JSON with sorted keys, `default=str` for UUIDs/datetimes
- **Hash storage**: Thread-local to allow concurrent requests to maintain independent chains

### Audit Entry Structure

```json
{
  "event": "user_login",
  "actor": {"user_id": "...", "display_name": "Anna Admin", "email": "anna@example.org", "role": "org_admin", "ip": "192.168.1.1"},   <!-- estate-fact-ok: fabricated example payload, alongside anna@example.org -->
  "target": {"entity": "user", "id": "...", "display_name": "...", "email": "..."},
  "changes": {"role": {"from": "participant", "to": "coordinator"}},
  "organization_id": "...",
  "integrity_hash": "abc123..."
}
```

Actor and user-target dicts are **denormalized at emit time** (`actor_from_user` / `target_from_user` in `app/services/audit.py`): `display_name` and `email` are embedded in the entry so the log stays meaningful after the referenced user row is anonymized (GDPR erasure rewrites the DB row; the tamper-evident log is retained under LK_F and is only visible to org_admins per the PII visibility matrix). Users without email (guests) omit the `email` key.

### Standard Audit Events

The audit service provides typed helper functions for common events. A few
routers call `record_audit_event` directly instead, for events specific to
one router:

| Function | Event | When |
|----------|-------|------|
| `audit_user_login` | `user_login` | Successful login |
| `audit_user_login_failed` | `user_login_failed` | Failed login attempt |
| `audit_user_logout` | `user_logout` | Manual, timeout, or forced logout |
| `audit_user_created` | `user_created` | New user registration or creation |
| `audit_user_role_changed` | `user_role_changed` | Role promotion/demotion |
| `audit_user_locked` | `user_locked` | Account lockout triggered |
| `audit_user_deleted` | `user_deleted` | User account deletion |
| `audit_config_changed` | `config_changed` | Organization settings changed |
| `audit_data_deletion_requested` | `data_deletion_requested` | GDPR erasure request |
| `audit_organization_updated` | `organization_updated` | Org settings changed |
| `audit_organization_deleted` | `organization_deleted` | Org deletion |
| `record_audit_event` (`chat.py`) | `chat.coordinator_requested` | A team member opens the team's coordinator assist window |
| `record_audit_event` (`chat.py`) | `chat.coordinator_joined` | A coordinator's first message into an open assist window |
| `record_audit_event` (`chat.py`) | `chat.coordinator_left` | A coordinator closes the team's assist window |
| `record_audit_event` (`teams.py`) | `team.member_moved` | A coordinator moves a member to another team of the same run |
| `record_audit_event` (`admin.py`) | `org.media_purged` | An org admin purges every participant medium of one type |
| `record_audit_event` (`runs.py`) | `run.results_published` | A coordinator publishes or re-publishes a run's public result link |
| `record_audit_event` (`runs.py`) | `run.results_revoked` | A coordinator revokes a published result link |
| `record_audit_event` (`instance_auth.py`) | `instance.login` | An operator signs in (password+TOTP, or the TOTP confirmation) |
| `record_audit_event` (`instance_auth.py`) | `instance.login_failed` | An operator login fails (any factor) |
| `record_audit_event` (`instance.py`) | `instance.org_created` | The operator creates an organization (first-admin invitation queued) |
| `record_audit_event` (`instance.py`) | `instance.org_updated` | The operator edits an organization's name or content languages |
| `record_audit_event` (`instance.py`) | `instance.org_archived` | The operator archives an organization |
| `record_audit_event` (`instance.py`) | `instance.org_restored` | The operator restores an archived organization |
| `record_audit_event` (`instance.py`) | `instance.org_admin_invited` | The operator invites (or renews an invitation for) an organization admin |
| `record_audit_event` (`instance.py`) | `instance.org_deletion_requested` | The operator schedules an archived organization's hard deletion |
| `record_audit_event` (`instance.py`) | `instance.org_deletion_cancelled` | A restore cancels a scheduled deletion |
| `record_audit_event` (`org_deletion.py`) | `instance.org_deleted` | The daily sweep hard-deletes an organization whose grace period has passed |
| `record_audit_event` (`instance.py`) | `instance.legal_updated` | The operator saves the instance's own legal texts |
| `record_audit_event` (`instance_auth.py`) | `instance.password_changed` | An operator changes its own password |

The result-link events record the link and run ids but never the slug: a published
slug is a bearer capability, and the audit log is retained for a year.

The `instance.*` events are emitted above every tenant: `record_audit_event`
is called with `organization_id=None`, so the entries carry no organization id
at all. Each event has its own shape — the actor identifies the operator
directly (`instance_admin_id`, email, IP; there is no user row to reference)
except on failed logins, where nobody is authenticated:

| Event | `actor` | `target` | Other fields |
|-------|---------|----------|--------------|
| `instance.login` | `instance_admin_id`, `email`, `ip` | the operator itself: `entity: "instance_admin"`, `id`, `email` | `auth_method`: `password+totp` (regular login) or `totp_confirm` (the first login that completes the TOTP enrollment) |
| `instance.login_failed` | `ip` only | the attempted address: `entity: "instance_admin"`, `email` — no id, the account may not exist | `reason`: `user_not_found`, `invalid_password`, `account_disabled` or `invalid_totp` |
| `instance.org_created` | `instance_admin_id`, `email`, `ip` | — | `target_slug`: the slug of the created organization |
| `instance.org_updated` | `instance_admin_id`, `email`, `ip` | — | `target_slug`, `changed_fields`: sorted field names (`name`, `languages`) — never the new values |
| `instance.org_archived` | `instance_admin_id`, `email`, `ip` | — | `target_slug` |
| `instance.org_restored` | `instance_admin_id`, `email`, `ip` | — | `target_slug` |
| `instance.org_admin_invited` | `instance_admin_id`, `email`, `ip` | — | `target_slug` — never the invited address |
| `instance.org_deletion_requested` | `instance_admin_id`, `email`, `ip` | — | `target_slug` |
| `instance.org_deletion_cancelled` | `instance_admin_id`, `email`, `ip` | — | `target_slug` |
| `instance.org_deleted` | the system actor `{system: "retention_sweep", ip: "internal"}`, not an operator | — | `target_slug`, `rows_deleted`, `objects_deleted` |
| `instance.legal_updated` | `instance_admin_id`, `email`, `ip` | — | `changed_kinds`: sorted kinds (e.g. `["imprint"]`) — never their text. No `target_slug`: the texts are instance-wide, not per organization |
| `instance.password_changed` | `instance_admin_id`, `email`, `ip` | the operator itself: `entity: "instance_admin"`, `id`, `email` | — |

Most org events name their organization as a top-level `target_slug` instead
of a `target` dict; `instance.password_changed` follows the `instance.login`
shape instead, since it targets the operator itself, not an organization.

### Verification

The integrity chain can be verified with:

```bash
docker compose exec api python -m app.tools.verify_audit_log /var/log/clavis/audit.log
```

This tool reads the log file, recomputes each entry's HMAC hash using the current key, and compares against the stored hash. Any tampering (insertion, deletion, modification of entries) breaks the chain.

## Security Headers

Security headers are applied at two levels:

### Caddy (Reverse Proxy)

Caddy sets only the headers that nothing behind it sets. The API and the frontend server set the Content-Security-Policy, `X-Content-Type-Options`, `X-Frame-Options` and `Referrer-Policy` themselves; Caddy does not repeat them, because a second copy reaches the browser as a second header.

| Header | Value |
|--------|-------|
| `Permissions-Policy` | `geolocation=(self), microphone=(self), camera=(self)` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (standalone server only) |

### FastAPI Middleware and Frontend Server

The `add_security_headers` middleware in `app/main.py` adds CSP and other headers to API responses; `src/frontend/nginx.conf` does the same for the pages. In the dev overlay the Vite dev server sets none, so `Caddyfile.dev` sets them for the pages.

### Content Security Policy

```
default-src 'self';
script-src 'self' 'unsafe-inline';
style-src 'self' 'unsafe-inline';
font-src 'self';
img-src 'self' data: blob:;
connect-src 'self' ws: wss:;
frame-ancestors 'none';
base-uri 'self';
form-action 'self';
```

`ws:` and `wss:` are allowed in `connect-src` for WebSocket connections.

This policy of the API names no foreign host. It matters for the two pages the
API serves itself, `/api/docs` and `/api/redoc`. FastAPI's stock pages load
their script from `cdn.jsdelivr.net`, an icon from `fastapi.tiangolo.com` and,
for ReDoc, a style sheet from `fonts.googleapis.com`. The policy blocks all
three, so both pages stay empty in a browser. `/api/openapi.json` works.
`tests/test_security_headers.py` holds the rule.

### Outside Requests

The product promises that it contacts no third party except the services the
privacy text names. These are all requests to a foreign host at runtime:

| Who asks | Host | When | What the host learns |
|---|---|---|---|
| Browser | `tile.openstreetmap.de` by default (FOSSGIS, Germany). `MAP_TILE_URL` names another tile server | A participant opens the map view of the stations, or a creator opens the station editor | IP address, browser, the address of the installation, the map section |
| Server | `nominatim.openstreetmap.org` by default. `photon.komoot.io` with `GEOCODER_UPSTREAM=photon`. `GEOCODER_UPSTREAM_URL` names an own instance | A creator searches an address in the station editor or moves the pin, and the regional place index has no answer. `GEOCODER_UPSTREAM=none` turns the request off | The address of the server, the search text or the coordinates of the pin, the interface language, the contact in `GEOCODER_CONTACT`. Never the creator's IP address or browser |
| Server | `overpass-api.de` | The operator runs `python -m app.cli geo import` | The address of the server and the bounding box of the region |
| Browser | `maps.apple.com` | Somebody taps a station address on an Apple device. Other devices get a `geo:` link, which opens the map app of the device | The place of the station. The link is a navigation, not a background request |
| Server | the SMTP server in `SMTP_HOST` | Invitation, password reset, password changed, role changed | Recipient address and mail text |

The map tiles come from the FOSSGIS server `tile.openstreetmap.de` in Germany, unless `MAP_TILE_URL` names another one. `nominatim.openstreetmap.org` is an alias of a `fastly.net` name, a content delivery network of a company in the USA. The S3 store in `S3_ENDPOINT` is part of the installation, and the browser loads every answer medium through `/api/files/`. The frontend policy in `src/frontend/nginx.conf` allows the tile host
and nothing else, and `src/frontend/src/outsideRequests.test.ts`
fails when a new host appears there. `/netcheck.html`, the network check page
for a blocked network, asks its own server only. Fonts, icons and the flag
font are bundled. A new outside request needs its passage in the privacy
templates (`src/frontend/src/i18n/locales/de/legalTemplates.json`) first.

## Rate Limiting

IP-based rate limiting via `slowapi` with in-memory storage:

```python
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
```

Applied per-endpoint with decorators:

```python
@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, ...):
```

In-memory storage is appropriate for single-server deployment. For horizontal scaling, this would need Redis-backed storage.

## Session Handling

Established session-handling practice — followed here as a deliberate
commitment, not an accident — is that **changing an account's password ends
the account's other sessions**. Without it, a session someone else holds
would keep working until it idles out on its own.

The commitment is kept in both dimensions:

- **Tokens**: every token minted before the change is refused with 401
  `SESSION_REVOKED` on its next request. The session that made the change
  continues with a fresh cookie.
- **Live connections**: the account's open WebSocket connections are closed
  with code 4000 "session revoked", so an open socket cannot keep reading
  chat and run events past the change.

The mechanism — the account's session version, the token's `sv` claim, which
operations increment it, and the fresh cookie of the changing session — is
documented in [authentication.md](authentication.md); the socket close is in
[realtime.md](realtime.md).

## Operator Authentication (Instance Level)

The instance operator — the identity above every organization, see
[roles-and-permissions.md](../domain/roles-and-permissions.md) — has its own
login, session and audit trail, deliberately separate from the organization
one:

- **Accounts exist only through the CLI** (`python -m app.cli
  instance-admin create --email <e>`, run inside the api container — see
  [deployment/README.md](../../deployment/README.md)); no HTTP route creates
  one. An account is born with an Argon2id password hash and a *pending* TOTP
  setup.
- **Login requires password and TOTP** (`POST /api/instance/auth/login` with
  email, password and the 6-digit code). The first login instead answers
  `totp_setup_required` and hands out the `otpauth://` provisioning URI
  exactly once; `POST /api/instance/auth/totp/confirm` (email, password,
  code) completes the enrollment and logs in. Unknown email, wrong password
  and wrong or missing code all answer the same generic 401 — the endpoint
  never reveals which factor failed.
- **The session cookie is separate**: `instance_token`, same flags as the
  user cookie (HttpOnly, Secure in production, SameSite=Lax) and the same
  `SESSION_TIMEOUT_MINUTES` lifetime — but fixed, not sliding: the operator
  session runs one window from login, while the user cookie re-mints on every
  authenticated request. An operator who is also an organization member holds
  both sessions side by side; the tokens never cross realms (403 in both
  directions).
- **Rate limit**: login and totp/confirm are limited to 5 requests per minute
  per address — the same slowapi mechanics as the user login, so five wrong
  attempts from one address block the sixth within the minute.
- **Every operator login is audited**: `instance.login` on success,
  `instance.login_failed` on failure, both with `organization_id: null`.
- **The rest of the account lifecycle also has no HTTP route**: `python -m
  app.cli instance-admin list|disable|enable|reset-totp|set-password` (see
  [deployment/README.md](../../deployment/README.md)) list every operator, or
  disable, re-enable, rotate the TOTP secret of, or set the password of one —
  the same server-only trust anchor as account creation, unaudited (there is
  no HTTP request to attach the event to). A disabled operator gets 403
  `INSTANCE_ADMIN_DISABLED` at login or TOTP confirm, checked after the
  password so the state is only disclosed to someone who proved the
  credentials; an already-open session of it is refused by
  `require_instance_admin` at the next request. `reset-totp` clears
  `totp_confirmed_at` and `last_login_at`, so the next login hands out a new
  provisioning URI, the same one-time handout as account creation.
- **The operator changes its own password** with `POST
  /api/instance/auth/password` (current password, new password); the
  password policy applies and the new password must not reuse the current or
  the last one (coded 400 `INSTANCE_PASSWORD_REUSE_NOT_ALLOWED`). Audited as
  `instance.password_changed`.

### Archiving an Organization

`POST /api/instance/organizations/{slug}/archive` — and `/{slug}/restore` to
reverse it — is an instance-level off switch for a tenant, not a deletion:

- **Sign-in is blocked**: every login path answers 403 with the coded error
  `ORG_ARCHIVED` — the organization login, self-signup, guest join and the
  2FA verify steps alike.
- **Open sessions die with it**: the session guard in `get_current_user`
  re-checks the archive flag on every authenticated request, so a token
  minted before the archive stops authorizing the moment it happens.
- **Public reads disappear**: the organization and its runs answer the public
  endpoints as if they never existed — join-code resolution and published
  result links 404, the run-state endpoint 404s even for team members, and
  the organization leaves the public directory.
- **The data stays**: archiving sets `archived_at` and nothing else. Restore
  reverses it; both actions are idempotent and audited
  (`instance.org_archived` / `instance.org_restored`) only when they actually
  change something.

### Deleting an Organization

`POST /api/instance/organizations/{slug}/deletion` schedules the hard,
irreversible deletion of an **already-archived** organization, 30 days ahead
(`DELETION_GRACE_DAYS`, `app/services/org_deletion.py`). Only an archived
organization can be scheduled (409 `ORG_NOT_ARCHIVED` otherwise, also
backstopped at the database level by
`ck_organizations_deletion_requires_archived`), and only with the operator
retyping the organization's own slug as confirmation (422
`CONFIRMATION_MISMATCH` otherwise). Restoring the organization before the due
date cancels the schedule (`instance.org_deletion_cancelled`) — archiving it
again clears `deletion_requested_at` too, as a backstop, since a deletion is
never legitimately pending on a freshly-archived organization.

**What the daily sweep removes, once the grace period has passed, per
organization under one row lock and one transaction:**

1. Every database row scoped to the organization is deleted, table by table
   in foreign-key order — children before the parents an `ON DELETE CASCADE`
   would otherwise silently take with them — with the organization row
   itself deleted last of all rows. These deletes are only **staged** in the
   open transaction (flushed, not committed), so nothing is durable yet and
   a permanent database error at this step leaves every row, and every
   object in storage, untouched.
2. Every object under the organization's own storage prefix (`{org_id}/`),
   plus any legacy `uploads/{date}/…` key its rows still reference that no
   *other* organization's rows reference, is deleted from object storage.
   This is the **first irreversible step**: an object store delete cannot be
   rolled back the way a staged database write can.
3. Only once both have succeeded does the transaction commit, which is what
   makes the staged row deletions durable. Only once that commit has
   succeeded is `instance.org_deleted` audited, with the slug and the counts
   of rows and objects removed.

A failed object delete (step 2) rolls the staged row deletions back too —
nothing has committed — so the organization row, and every row that was only
staged for deletion, survives exactly as before, and the organization stays
due for the next day's sweep to retry both steps from scratch. Object
deletes are idempotent (an already-missing object is not an error and is not
counted), so a retry after a partial failure never double-counts.

**What it does not remove**: the organization's own entries in the audit log
files. The sweep deletes database rows and storage objects only — it never
touches the log files, which are not scoped by organization at all. An
organization's audit entries age out on the log system's own schedule, like
every other entry: `AUDIT_LOG_RETENTION_DAYS` (default 365, see
[Log retention](#log-retention) below).

## GDPR Readiness

### Soft Deletion

All entities use `SoftDeleteMixin` — records are marked with `is_deleted=True` and `deleted_at` timestamp but not physically removed. This:

- Preserves referential integrity
- Allows data recovery within retention windows
- Supports audit trail requirements

### User Anonymization

The GDPR service (`app/services/gdpr.py`) provides user anonymization that replaces PII with placeholders while preserving non-PII references:

- Email → `deleted-{uuid}@anonymized.local`
- Display name → `Deleted User`
- Password hash → cleared
- TOTP secret → cleared
- `webauthn_credentials` column → cleared

The user record remains for audit trail and referential integrity, but all personally identifiable information is removed.

### Deletion Classes

Different data categories have different retention periods (LK-A through LK-H). The audit log has the longest retention (365 days by default, configurable). Periods are single-sourced from `DeletionClass.retention_days` (`app/services/gdpr.py`).

The daily in-process `RetentionSweeper` (`app/services/retention.py`, started from the FastAPI lifespan, skipped under `TESTING`) now **enforces** the run-scoped classes via independent tasks in `app/services/retention_tasks.py` (one transaction each, so one failing class never blocks the others). Each task is idempotent and accepts an injectable reference date for testing:

| Class | What | When | Enforcement |
|---|---|---|---|
| LK-A | Guest accounts (throwaway run identities) | 90 d after the run ends, **or** the run is abandoned (not ended, no activity since `coalesce(started_at, created_at)` for `ABANDONED_RUN_DAYS`, default **60**) | Hard-deleted (`delete_run_guests`; FKs `ON DELETE SET NULL` preserve the run roster + scores). The guest's name in `team_members` is replaced by `ANONYMIZED_MEMBER_NAME` in the same step, and the row stays (`blank_guest_member_names`) |
| LK-B | Submission raw media (`photo_url`/`audio_url` object keys) + team chat messages | 90 d after the run ends | Media objects deleted from storage (verified) and the keys cleared from `Submission.content`; chat rows hard-deleted. A photo's WebP variants ride along uncounted |
| LK-C | Residual run data | 1 y after the run ends | **Anonymized, not deleted** — participant-authored team display names are replaced with non-identifying labels and the run is marked `retention_anonymized_at`; the run/score rows survive for statistics. Every remaining display name in `team_members` of the run is replaced by `ANONYMIZED_MEMBER_NAME`, registered members included |
| LK-H | Free text of a run: the answer part of `submissions.content` (text, quiz choice, scanned code), `scores.comment`, `feedback.comment` | 1 y after the run ends | **Cleared, not deleted** (`sweep_run_answer_text`). The rows, the points and the ratings stay. Media keys in `content` stay, they belong to LK-B |

**Content-media orphans**: a soft-deleted station's `title_image_key` object is removed only once **no** surviving station or run snapshot references that exact key (the snapshot freeze copies the *key*, so an object can be shared). Deleting the station leaves its object in place on purpose, and this sweep removes it.

LK-A's 90 days deliberately match LK-B: a guest account is the guest's only way back into a finished run — the published result, the scoreboard, the feedback prompt — so it must not vanish while the run's media is still retained.

No sweep deletes a row that a count, a sum or the published result needs: names and free text go, `teams`, `team_members`, `submissions`, `scores` and `feedback` rows stay.

The name-and-text steps carry no run marker. Each statement selects only rows that still hold a name or text, so a second pass changes nothing, and the first pass after an update also reaches rows that an earlier version left behind. The task `anonymized_member_names` does the same for accounts that were anonymized before `anonymize_user` cleared the roster.

**Backups.** The app deletes on its own schedule, but a backup written earlier still holds the data until the backup is rotated out. That period is an installation setting, because only the operator knows the backup job: the operator console (`/instance/legal`, column `instance_settings.backup_retention_days`), else `BACKUP_ROTATION_DAYS` (default 30, the rotation of the installation's backup job). `app/services/backup_retention.py` resolves it. The privacy template and the organization's retention tab state it. The app runs and changes no backup job. `BACKUP_ROTATION_DAYS` is also still the lower bound of an organization's retention period, and the value from the operator console does not move that bound.

`ABANDONED_RUN_DAYS` is configurable via `settings.retention.abandoned_run_days`. LK-E (logs) and LK-F (audit) retention are handled by the logging pipeline, not this sweep.

**Published result links** are not a Löschklasse. The link is a capability, not personal data by itself, but it still expires. A link stays resolvable for `settings.retention.result_link_days` days (default **90**), restarted by every re-publish. The sweep task `expired_result_links` (`sweep_expired_result_links`) deletes live links past their `expires_at` and revoked links once their original expiry has passed. Revoking answers 410 immediately but never shortens `expires_at`. The run and its scores are untouched.

**Images of rich texts.** The editor uploads an image under `{org_id}/media/` the moment the creator inserts it. There is no DELETE route, on purpose: one object can stand in several languages of a text, in a copied station or task, in a soft-deleted row that can be restored, and in the frozen copy of a run. The task `orphaned_content_media` (`app/services/content_media_retention.py`) counts references instead. It deletes an object only when the object is older than 7 days (`CONTENT_MEDIA_ORPHAN_MIN_AGE_DAYS`, so an image inserted but not yet saved survives the edit) and no text or JSON column of the content tables names its key. The check reads every such column of `REFERENCE_TABLES`, not a list of fields, and a test makes every table with a text column choose a side. The search is a text search for the image key, so an image inside a table cell or a figure counts as used. An image that leaves its last text goes at the first sweep after its grace period, counted from the upload. A deleted organization loses the whole prefix in `org_deletion.py`.

### Rich Text

The contract file `src/frontend/src/utils/richTextContract.json` lists every element, attribute and value the editor stores. The backend filter (`src/backend/app/services/richtext.py`, nh3) cleans every write by it. The frontend renderer (`src/frontend/src/utils/sanitizeRichText.ts`, DOMPurify) cleans every render by it. A test on each side compares the filter with the file.

No `style`, `class`, `id`, event handler or other `data-` attribute survives. `data-width` and `data-align` keep only their listed values. An image survives only with a `src` under the organization's media route. This holds also inside a figure or a table cell. A figure without an image goes.

The renderer also keeps `h1`, `code` and `pre`, only for texts written before the server filtered rich text. The editor never makes them. The editor and the backend both map them the same way: `h1` to `h2`, `pre` to paragraphs, `code` to plain text.

Pasted HTML is cleaned in the browser before the editor reads it (`src/frontend/src/utils/cleanPastedHtml.ts`). The backend filter still decides what is stored.

### Participant Photo Hygiene

Every `image/*` submission upload is stored **re-encoded**: the EXIF orientation
is applied to the pixels and every metadata block is dropped, GPS above all.
"No live tracking" is a platform invariant, and a stored coordinate would be
personal data the platform never uses. An embedded ICC profile is converted
to sRGB rather than dropped, so the strip does not shift colours. The decode
path is guarded against decompression bombs: a pixel-count cap, a
reduced-scale JPEG decode and a limit of two concurrent decodes. One
exception: the library cannot decode a HEIC or HEIF photo, so the answer
route stores it as uploaded, with its metadata and without variants. Its GPS
position reaches storage when the phone wrote one. The file is served as an
attachment. Any other image that does not decode is refused with 415
`IMAGE_NOT_DECODABLE`, and nothing is stored.
Endpoint mechanics, the variant set and the `variant` parameter
live in [api-structure.md](api-structure.md).

### Participant Video and Audio Hygiene

Every `video/*` and `audio/*` submission upload is stored **without container
metadata** (`app/services/media_metadata.py`). The place of recording, the
device model, the software, the recording time, titles, XMP and cover pictures
go. Tracks that are neither picture nor sound go together with their samples,
for example the timed metadata of an iPhone or the GPS track of an action
camera. Picture and sound are not decoded and not re-encoded.

The method is a container rewrite in pure Python, with no tool in the image.
In MP4, QuickTime, WebM, Matroska, Ogg and FLAC every metadata element is
overwritten in place with padding of the same length, so no offset moves and
the file plays as before. MP3, AAC and WAV lose their tag blocks. The rule is
an allow-list: an element the code does not know is blanked.

A file whose container the cleaner does not know or cannot read to the end is
refused with 422 `MEDIA_NOT_READABLE`, and nothing is stored. The limits:

- The encoder name inside the sound stream of an MP3 or AAC file stays, for
  example `LAME3.100`. A message an encoder writes into the picture stream
  stays too. Neither holds a place.
- A clean holds the upload and one working copy in memory. At most two cleans
  run at once.
- Objects stored before this change are not touched.

### Org-Admin Media Purge

The purge serves the organization's own duty to erase:
an org admin deletes every participant medium of one type ahead of the LK-B
window, retyping the organization's slug as confirmation. The personal data —
the media objects and the references to them — goes; submission rows and
scores stay, so statistics and leaderboards stay consistent. A
purged submission carries a `media_removed` marker and renders as
"Medium entfernt". Audited as `org.media_purged`; endpoints, classification
rules and failure behaviour in [api-structure.md](api-structure.md).

### Grace-Delete (LK-G)

Admin-initiated user deletion (`DELETE /users/{id}`, default `mode=grace`) soft-deletes and deactivates the account but keeps the PII so it stays restorable via `POST /users/{id}/restore` for the 30-day grace window. The in-process `RetentionSweeper` (started from the FastAPI lifespan, daily cadence) anonymizes accounts whose window has expired. An explicit GDPR Art. 17 erasure request uses `mode=immediate` and anonymizes right away — restoration is then impossible by design (410 on restore).

### Data Deletion Audit Trail

All data deletion requests are logged via `audit_data_deletion_requested()`, creating a verifiable record that the deletion was properly executed.

## Log retention

Retention periods are defined in the configuration:

| Category | Retention |
|----------|-----------|
| Audit logs | 365 days |
| Application logs | 90 days |
| Error logs | 90 days |
| Access logs | 90 days |

Rotation is enforced in-process by `TimedRotatingFileHandler` (daily, midnight UTC). Compression of older rotated files and hard-deletion past retention limits are handled by `deployment/scripts/log-cleanup.sh`, scheduled via a daily cron job (`0 4 * * *`). See `deployment/README.md` for cron installation instructions.

## TLS Configuration (Production)

Production TLS is terminated by the reverse proxy in front of the app. On a shared-services host that is the host's own proxy, for example Nginx Proxy Manager; a self-hosted all-in-one stack terminates it in the bundled Caddy service, whose `Caddyfile` carries a commented-out production block following BSI TR-02102:

| Setting | Value |
|---------|-------|
| Protocols | TLS 1.2, TLS 1.3 |
| Cipher suites | ECDHE-ECDSA and ECDHE-RSA with AES-GCM |
| Curves | secp384r1, secp256r1 |
| Key type | ECDSA P-384 |
| HSTS | max-age=31536000, includeSubDomains |

The block is commented out because development runs over plain HTTP; a self-hosting operator uncomments it and sets their domain. Behind Nginx Proxy Manager it stays commented out — the proxy terminates TLS and sets HSTS.
