# API Structure

## URL Hierarchy

The API uses a hierarchical URL scheme that mirrors the domain model. Tenant-scoped endpoints nest under `/api/orgs/{slug}/`:

### System Endpoints

```
POST   /api/auth/register           — Create org + first user
POST   /api/auth/login              — Login (returns JWT or 2fa_required)
POST   /api/auth/logout             — Clear session
POST   /api/auth/2fa/totp/setup         - Start TOTP setup (provisioning URI, 8 recovery codes)
POST   /api/auth/2fa/totp/verify        - Confirm the setup with a first code
POST   /api/auth/2fa/totp/authenticate  - Second sign-in step (TOTP or recovery code)
DELETE /api/auth/2fa/totp               - Switch 2FA off

GET    /api/health                  — Health check (DB + storage connectivity)
POST   /api/organizations           — Refuses creation for every organization token (coded 403 ORG_CREATE_INSTANCE_ONLY — creation is operator-only, see Instance Endpoints)
```

### Public Endpoints

```
GET    /api/public/runs/{code}           — Join-code resolution (run id, rallye name, org slug); no auth
GET    /api/public/runs/{code}/my-team   — Team lookup for later team joining; requires the access_token cookie
GET    /api/public/runs/{run_id}/state   — Participant run state (incl. grading_complete); requires the access_token cookie
GET    /api/public/organizations         — Public org directory (slug, name); no auth
POST   /api/public/results/lookup        — A published run's frozen final standing, by slug in the body; no auth
GET    /api/public/config                — Instance config for the app shell (brand_name, bootstrap_open, single_org_slug); no auth
GET    /api/public/manifest.webmanifest  — Web manifest with name and short_name from BRAND_NAME; cached 5 min; no auth
GET    /api/public/legal/{kind}          — Best-known legal text for imprint/privacy/accessibility, optional ?org=<slug>; no auth
GET    /api/geocode/config               — Address search of the station form: suggestions allowed or not, and the sources; creator+
GET    /api/geocode/suggest              — Hits while typing, from the regional place index or a Photon service; creator+
GET    /api/geocode/search               — Hits for a sent search, index first, then the outside service; creator+
GET    /api/geocode/reverse              — The address of a point, for a pin set on the map; creator+
```

`/legal/{kind}` answers the organization's own text when `org` names an active
(not archived, not soft-deleted) organization with a non-empty text of that
kind; otherwise the instance's own text (saved through `PUT /api/instance/legal`);
otherwise `source: "none"` and `text: null`. An unknown `kind` is the only 404
here (coded `LEGAL_KIND_UNKNOWN`) — having no text configured yet is a normal
state, not an error. Rate-limited to 60/minute per IP.

`/results/lookup` takes the slug in the **POST body**, never in a path or query, so the
link capability cannot leak into access-log request lines. Unknown slugs answer 404
(`result_link_not_found`), revoked or expired ones 410 (`result_link_revoked` /
`result_link_expired`); the response carries the standing only, no member or user data,
and is rate-limited (30/minute per IP).

`/config` answers, before any authentication, what the app shell needs at boot:
the instance-wide `brand_name` (one value for the whole instance, from
`BRAND_NAME`), whether organization self-registration is still open
(`bootstrap_open`, mirroring the server-side bootstrap gate in
`POST /api/auth/register` — true while no organization exists, or when
unlimited self-registration is enabled server-side with
`ALLOW_UNLIMITED_ORG_REGISTRATIONS`), and `single_org_slug` — the slug when
exactly one non-archived organization exists, so onboarding can skip the
organization picker. Only slugs and flags leave the instance: no organization
names, counts or operational state.

### Instance Endpoints (Operator)

Above the tenants sits the **instance operator** — a separate identity that
belongs to no organization
([roles-and-permissions.md](../domain/roles-and-permissions.md)). Its console
(`/instance` in the frontend) talks to its own API surface:

```
POST   /api/instance/auth/login               — Operator login: password + TOTP (5/minute per IP)
POST   /api/instance/auth/totp/confirm        — Complete the TOTP enrollment; logs in (5/minute per IP)
GET    /api/instance/auth/me                  — Current operator; the console's session probe
POST   /api/instance/auth/logout              — Clear the operator cookie only

GET    /api/instance/organizations                    — Every organization with aggregate counts
POST   /api/instance/organizations                    — Create an organization, invite its first org admin
PATCH  /api/instance/organizations/{slug}              — Edit name and content languages (slug never accepted)
POST   /api/instance/organizations/{slug}/archive     — Archive an organization (idempotent)
POST   /api/instance/organizations/{slug}/restore     — Reverse an archive (idempotent)
POST   /api/instance/organizations/{slug}/deletion    — Schedule an archived organization's hard deletion
GET    /api/instance/organizations/{slug}/admins     — The organization's admins, to pick whom an invitation replaces (audited)
POST   /api/instance/organizations/{slug}/admin-invitations — Invite (or renew an invitation for) an organization admin
GET    /api/instance/audit                            — The operator's own instance.* audit log, paged and filterable
GET    /api/instance/legal                            — The instance's own imprint/privacy/accessibility texts
PUT    /api/instance/legal                            — Save the instance's own legal texts
POST   /api/instance/auth/password                    — Change the signed-in operator's own password
```

`/audit` and `/legal` sit on a second router (`operator_router` in
`instance.py`) mounted at `/api/instance` in `main.py`, because `router`
above is mounted at `/api/instance/organizations` and a route on it can
never reach a sibling path like `/api/instance/audit` or `/api/instance/legal`.
`/auth/password` lives on the operator auth router (`/api/instance/auth`),
next to `/login` and `/totp/confirm` below.

No HTTP route creates an operator account — that happens only on the server,
via `python -m app.cli instance-admin create --email <e>` inside the api
container ([deployment/README.md](../../deployment/README.md)). The account is
born with an Argon2id password hash and a *pending* TOTP setup.

**Login** takes `{email, password, totp_code?}`. On the first login after
creation the setup is still pending, so the answer is
`status: "totp_setup_required"` with the `otpauth://` provisioning URI — handed
out exactly once; a later unconfirmed login gets the same status without the
secret. `POST /api/instance/auth/totp/confirm` takes
`{email, password, totp_code}` (stateless — no session exists before the
enrollment completes), confirms the setup and sets the operator cookie. From
then on, login requires the code. Unknown email, wrong password and wrong or
missing code all answer the same generic 401 — the endpoint never reveals
which factor failed.

**The session cookie is its own realm.** The token is an instance-scoped JWT
(`scope: "instance"`, no `org_id`, no `role`) under the cookie name
`instance_token`, so an operator who is also an organization member keeps both
sessions side by side in one browser. The realms never cross
(`app/auth/dependencies.py`): organization endpoints answer an operator token
with 403 *Instance operator token not valid for organization endpoints*, and
instance endpoints answer an organization token with 403 *Organization token
not valid for instance endpoints* — a valid token for the wrong realm is a
permission problem, not the anonymous 401.

**Organizations.** `GET /api/instance/organizations` lists every organization
with name, slug, content languages, `created_at` and `is_archived`, plus:

- `user_count`, `rallye_count`, `run_count` — non-deleted totals
- `org_admin_count` — non-deleted users with the `org_admin` role
- `pending_admin_invitation_count` — open org_admin invitations, never their addresses
- `active_run_count` — non-deleted runs currently in the active state
- `last_activity_at` — the latest submission or run start (null if neither exists)
- `deletion_due_at` — when a scheduled deletion will run (null if none is scheduled)

Aggregate counts and timestamps only — never user, team or submission data.
`POST /api/instance/organizations` takes
`{name, slug, content_languages, first_admin_email}` and answers 201; its
coded errors are the 409s `ORG_SLUG_TAKEN` (soft-deleted slugs count as taken)
and `EMAIL_HAS_PENDING_INVITATION`. Organization and first-admin invitation
share one transaction — a failed invitation leaves no organization behind —
and the invitation mail rides the same repository and mail path as the
org-internal invitations. `POST …/{slug}/archive` and
`POST …/{slug}/restore` are idempotent (no state change, no audit event when
nothing changes) and answer 404 for an unknown slug. What archiving does is
security-relevant and documented in
[security-and-compliance.md](security-and-compliance.md).

`PATCH /api/instance/organizations/{slug}` takes `{name?, content_languages?}` —
the slug is never accepted, it stays fixed for the organization's life. A
blank name is coded 422 `ORG_NAME_BLANK`, an empty language list is coded 422
`ORG_LANGUAGES_EMPTY`, and a code the app does not support is coded 422
`ORG_LANGUAGE_UNSUPPORTED`. A language the organization already carries stays
valid, because its own admin may enable any code in the organization settings. It works on an archived organization too — editing
carries no access implication, unlike archive/restore. Emits
`instance.org_updated` with the changed field names, never their values.

`POST /api/instance/organizations/{slug}/admin-invitations` takes
`{email, language?}` and replaces a lost organization admin through the same
invitation repository and mail path as organization creation. An open
invitation for the same address in that organization is renewed with a fresh
token instead of duplicated; an open invitation for the address in a
*different* organization, or an archived target organization, is coded 409
(`EMAIL_HAS_PENDING_INVITATION` / `ORG_ARCHIVED`), and an address that
already belongs to an account is coded 409 `EMAIL_HAS_ACCOUNT`, because it
could never accept. Emits `instance.org_admin_invited` with the target slug,
never the address.

**Takeover.** When no admin of an organization can
act, the invitation may carry `replace_admin_ids`: current admins of that
organization (anything else is coded 422 `REPLACE_NOT_ORG_ADMIN`). To pick
them, `GET /api/instance/organizations/{slug}/admins` lists the
organization's admins with address, name, state and last sign-in. That list
is a deliberate, narrow exception to "the operator sees no people", and every
look emits `instance.org_admins_viewed`. When the invited person accepts,
the replaced accounts are deactivated (not deleted) and their sessions end;
the organization log records `user_deactivated` with reason
`operator_takeover` per account, the operator log
`instance.org_admin_takeover` with the count only. The new admin reads the
takeover in user management (`GET /api/orgs/{slug}/invitations/takeover`,
30 days).

`POST /api/instance/organizations/{slug}/deletion` takes
`{confirm_slug}` — the operator retyping the organization's own slug — and
schedules a hard deletion 30 days out. Refused with 409 `ORG_NOT_ARCHIVED` for
an active organization or 422 `CONFIRMATION_MISMATCH` when `confirm_slug`
does not match; neither refusal changes anything. Emits
`instance.org_deletion_requested`. A restore before the due date cancels it
(`instance.org_deletion_cancelled`); the daily sweep that carries out the
actual delete is documented in
[security-and-compliance.md](security-and-compliance.md).

`GET /api/instance/audit` lists the operator's own `instance.*` events,
newest first, filterable by exact `event` and paged with `offset`/`limit`
(max 200). It reads the same audit log files the organization log viewer
does, kept to events whose name starts with `instance.` — an organization's
own events never carry that prefix, so they never appear here.

`GET`/`PUT /api/instance/legal` read and write the instance's own imprint,
privacy and accessibility texts, backed by the single-row `instance_settings`
table. Every field answers null before the first save. `PUT` only touches
fields present in the payload — an explicit `null` clears a text, an omitted
field is left untouched — and sanitizes each one through the same HTML
allowlist as organization legal texts. Emits `instance.legal_updated` with
the changed kinds (e.g. `["imprint"]`), never their text, and only when
something actually changed. The public counterpart is
`GET /api/public/legal/{kind}` above.

`POST /api/instance/auth/password` takes `{old_password, new_password}` and
changes the signed-in operator's own password; the password policy applies
and the new password must not reuse the current or the last one (coded 400
`INSTANCE_PASSWORD_REUSE_NOT_ALLOWED`). Emits `instance.password_changed`.
A disabled operator (`disabled_at` set via `instance-admin disable`, see
[deployment/README.md](../../deployment/README.md)) is refused everywhere
with 403 `INSTANCE_ADMIN_DISABLED`, checked after the password so the state
is only disclosed to someone who proved the credentials.

Organization creation left the organization level: `POST /api/organizations`
refuses every organization token — org_admin included — with the coded 403
`ORG_CREATE_INSTANCE_ONLY`. Organizations come into existence either through
bootstrap self-registration (`POST /api/auth/register`, open only until the
first organization exists) or through the operator.

### Tenant-Scoped: Stable Layer (Templates)

```
/api/orgs/{slug}/
├── GET|PUT|DELETE                                              — Org CRUD
├── rallyes/
│   ├── GET|POST                                                — List/create rallyes
│   └── {rallye_id}/
│       ├── GET|PUT|DELETE                                      — Rallye CRUD
│       └── stations/
│           ├── GET|POST                                        — List/create stations
│           └── {station_id}/
│               ├── GET|PUT|DELETE                              — Station CRUD
│               └── challenges/
│                   ├── GET|POST                                — List/create challenges
│                   └── {challenge_id}/
│                       └── GET|PUT|DELETE                      — Challenge CRUD
```

### Tenant-Scoped: Dynamic Layer (Runtime)

```
/api/orgs/{slug}/
└── runs/
    ├── GET|POST                                                — List/create runs
    └── {run_id}/
        ├── GET|PUT|DELETE                                      — Run CRUD
        ├── POST .../start|pause|resume|end                     — State transitions
        ├── teams/
        │   ├── GET|POST                                        — List/create teams
        │   └── {team_id}/
        │       ├── GET|PUT|DELETE                              — Team CRUD
        │       ├── POST .../members/{member_id}/move           — Move a member to another team (live runs only)
        │       └── chat/
        │           ├── GET|POST                                — List/send messages
        │           └── ws (WebSocket)                          — Real-time chat
        ├── submissions/
        │   ├── GET|POST                                        — List/submit answers
        │   └── {submission_id}/
        │       └── GET                                         — Get submission
        ├── scores/
        │   ├── GET|POST                                        — List/create scores
        │   └── GET scoreboard                                  — Standing: shared ranks, is_final, winners
        ├── results/
        │   ├── POST publish                                    — Publish/re-publish the result snapshot
        │   ├── DELETE publish                                  — Revoke the published link
        │   └── GET link                                        — Link status (url, expiry, revoked)
        ├── feed/
        │   ├── GET|POST                                        — List/create feed events
        │   └── {event_id}/
        │       └── GET                                         — Get feed event
        ├── feedback/
        │   ├── GET|POST                                        — List/submit feedback
        │   └── {feedback_id}/
        │       └── GET                                         — Get feedback
        └── ws (WebSocket)                                      — Real-time feed + state
```

Tenant-scoped at the org level (not under a run):

```
/api/orgs/{slug}/
└── admin/
    ├── GET  media/summary                                      — Stored participant media counts per type (org_admin)
    └── POST media/purge                                        — Delete every medium of one type (org_admin, slug confirmation)
```

## Router Organization

Each domain area has its own router module in `app/routers/`.
The script `scripts/docs-router-table.py` writes the table below from `src/backend/app/main.py`.

<!-- router-table:start -->
| Module | Prefix | Tags |
|--------|--------|------|
| `auth.py` | `/api/auth` | auth |
| `two_factor.py` | `/api/auth/2fa` | 2fa |
| `instance_auth.py` | `/api/instance/auth` | instance-auth |
| `instance.py` | `/api/instance/organizations` | instance-organizations |
| `instance.py` (`operator_router`) | `/api/instance` | instance-operator |
| `geocode.py` | `/api/geocode` | geocode |
| `health.py` | `/api` | health |
| `me.py` | `/api/me` | me |
| `guest.py` | `/api/join` | guest |
| `public.py` | `/api/public` | public |
| `organizations.py` | `/api/organizations` | organizations |
| `organizations.py` (`slug_router`) | `/api/orgs/{slug}` | organizations |
| `rallyes.py` | `/api/orgs/{slug}/rallyes` | rallyes |
| `stations.py` | `/api/orgs/{slug}/stations` | stations |
| `challenges.py` | `/api/orgs/{slug}/challenges` | challenges |
| `users.py` | `/api/orgs/{slug}/users` | users |
| `invitations.py` (`admin_router`) | `/api/orgs/{slug}/invitations` | invitations |
| `invitations.py` (`public_router`) | `/api/invitations` | invitations |
| `admin.py` | `/api/orgs/{slug}/admin` | admin |
| `runs.py` | `/api/orgs/{slug}/runs` | runs |
| `team_progress.py` | `/api/orgs/{slug}/runs` | runs |
| `teams.py` | `/api/orgs/{slug}/runs/{run_id}/teams` | teams |
| `submissions.py` | `/api/orgs/{slug}/runs/{run_id}/submissions` | submissions |
| `files.py` | `/api/orgs/{slug}/runs/{run_id}/submissions/upload` | files |
| `files.py` (`serve_router`) | `/api/files` | files |
| `media.py` | `/api/orgs/{slug}/media` | media |
| `scores.py` | `/api/orgs/{slug}/runs/{run_id}/scores` | scores |
| `feed.py` | `/api/orgs/{slug}/runs/{run_id}/feed` | feed |
| `chat.py` | `/api/orgs/{slug}/runs/{run_id}/teams/{team_id}/chat` | chat |
| `feedback_router.py` | `/api/orgs/{slug}/runs/{run_id}/feedback` | feedback |
| `analytics.py` | `/api/orgs/{slug}/runs/{run_id}/analytics` | analytics |
| `analytics.py` (`timeseries_router`) | `/api/orgs/{slug}/analytics` | analytics |
| `analytics_compare.py` (`compare_router`) | `/api/orgs/{slug}/analytics` | analytics |
| `qa_feedback.py` | `/api/qa` | qa-feedback |
| `ws.py` |  | websocket |
<!-- router-table:end -->

The `organizations.py` module is special — it has two routers:
- `router` for the system-level `POST /api/organizations` (create org)
- `slug_router` for tenant-scoped `GET/PUT/DELETE /api/orgs/{slug}`

### Rules two routers share live in `app/services/`, not in both of them

A challenge can be created down **two** paths — into the org-wide library
(`challenges.py`) and directly onto a rallye station (`rallyes.py`). Until
2026-07-28 each carried a byte-identical private copy of the scoring-rule
validation, and that is exactly how a rule ends up enforced on one path and not
the other: fixing the file you happen to have open leaves the other half broken.

Both now import from **`app/services/scoring_rules.py`**, which owns two things:
which scoring keys are meaningful per challenge type (pruning, silent) and when
an auto-scored challenge lacks what it needs to score with (validation, 422).

The general rule this stands for: **when a second router needs the same domain
rule, move it to `services/` rather than copying it.** A duplicated guard reads
as consistent right up to the day one copy changes.

## Rich Text Fields

Rich text fields take HTML of the contract in `src/frontend/src/utils/richTextContract.json`. The server cleans each one on write and returns the cleaned text. The filter is `src/backend/app/services/richtext.py`. [security-and-compliance.md](security-and-compliance.md) lists what it keeps.

The legal text routes keep an omitted text as it is. These are `PUT /api/orgs/{slug}` for the organization's texts and `PUT /api/instance/legal` for the instance's texts. The legal pages send only the texts the person changed.

The daily sweep of unused content images finds an image key by text search in every text and JSON column. An image inside a table cell or a figure counts as used.

## Participant Photos: Hygiene on Upload, Variants on Serve

Submission files upload through `POST /api/orgs/{slug}/runs/{run_id}/submissions/upload/`
(guest+, run ACTIVE, caller in a team of that run) and stream back through
`GET /api/files/{key}` (`files.py`). The browser never talks to Garage — the
storage host is internal, so the API proxies the bytes itself.

Every `image/*` upload is stored **re-encoded** (`app/services/image_variants.py`):

- The EXIF orientation is applied to the pixels and **every metadata block is
  dropped** — GPS above all: "no live tracking" is a platform invariant, and a
  stored coordinate is personal data the platform never uses.
- An embedded ICC profile is **converted to sRGB**, not dropped, so wide-gamut
  phone photos do not shift colour; if the conversion fails the pixels are kept
  as they are. Every encode runs against a copy whose `info` dict is cleared,
  so no encoder can carry an ICC profile, EXIF, XMP, a JPEG comment or a PNG
  text chunk forward.
- The stored **original keeps its source format** (JPEG at quality 85; a
  lossless WebP stays lossless; PNG; anything else Pillow can decode keeps its
  format with the plugin's defaults), long side capped at 4096 px, never
  upscaled.
- Two WebP variants land beside it: `<object_key>.thumb.webp` (400 px long
  side) and `<object_key>.display.webp` (1600 px), quality 85, never upscaled —
  lists and views stop loading full-size originals.

Bytes Pillow cannot identify as an image at all (HEIC without a plugin, plain
garbage) are **stored byte for byte as uploaded and get no variants** — such an
upload keeps working exactly as before. An image above the decode cap is
refused with 413 `IMAGE_TOO_LARGE` and nothing is stored: a JPEG always decodes
at a reduced scale, any other format above 50 megapixels is rejected before a
single pixel is decoded, and at most two images decode at once
(decompression-bomb guard).

Every `video/*` and `audio/*` upload is stored **without container metadata**
(`app/services/media_metadata.py`): place, device, software, recording time
and every track that is neither picture nor sound. Picture and sound stay byte
for byte. A container the cleaner cannot read is refused with 422
`MEDIA_NOT_READABLE` and nothing is stored. Details and limits:
[security-and-compliance.md](security-and-compliance.md#participant-video-and-audio-hygiene).

**Serving.** `GET /api/files/{key}?variant=thumb|display` tries the WebP object
beside the photo first and falls back to the original when it does not exist —
callers never need to know whether a photo has variants. The parameter changes
no authorization: same organization; coordinator and above serve any file of
the org; below that only members of that file's team in that run (`file.served`
is audited either way). The grading list loads the thumb; the full view and the
team's own answer view load the display variant.

**Variant keys are reserved.** An uploaded original must not end in
`.thumb.webp` / `.display.webp` — the upload route swaps the dot for a dash —
and a submission may not reference a variant key
(`verify_media_key_not_variant` in `submissions.py`): a variant is derived from
a photo, it is not an uploadable answer. Every path that deletes a photo — the
LK-B retention sweep, answer replacement, the orphan sweep, the purge below —
deletes its variants with it, uncounted (a variant a submission references
directly is itself a referenced key and is purged through that reference).

## The Org-Admin Media Purge

Two org-admin endpoints let an organization
erase its participant media ahead of the LK-B retention window — the GDPR
framing is in [security-and-compliance.md](security-and-compliance.md):

- `GET /api/orgs/{slug}/admin/media/summary` counts, per type, exactly what a
  purge of that type would delete: the distinct media keys the organization's
  submissions reference, in any media field. A key's type comes from its
  extension; only when the extension says nothing does the field it sits in
  decide (`photo_url` / `video_url` / `audio_url`). A `media_urls` entry of
  unknown extension has no type to fall back on: it is left alone and logged.
- `POST /api/orgs/{slug}/admin/media/purge` takes
  `{type: photo|video|audio, confirm_slug}` — the admin retyping the
  organization's own slug (422 `CONFIRMATION_MISMATCH` otherwise, nothing
  deleted). It deletes each object of the type plus a photo's WebP variants,
  then clears the key out of every submission's content and sets a
  `media_removed` marker, which the views render as "Medium entfernt".

**What the purge keeps: submission rows and their scores** — statistics and
leaderboards stay consistent. It runs inside the caller's
transaction, is idempotent, tolerates an already-missing object, and never
clears a reference whose object is still live: a failed object delete is
logged and that key's references stay for a later retry. Audited as
`org.media_purged` with the type and the count.

## Moving a Team Member Between Teams

`POST /api/orgs/{slug}/runs/{run_id}/teams/{team_id}/members/{member_id}/move`
(coordinator+, body `{target_team_id}`; `member_id` is the TeamMember UUID)
re-points a membership to another team of the same run while the run is ACTIVE
or PAUSED. Refusals are coded 422: `RUN_NOT_STARTED` /
`RUN_ENDED` outside the live states, `TARGET_NOT_IN_RUN` for a team of another
run, `SAME_TEAM`, `TEAM_FULL` at the same per-team cap the join paths enforce.
Both team rows are locked in ascending id order — a concurrent move A→B and a
move B→A cannot deadlock — then the member's own row, so one member cannot be
moved twice at once.

**The move writes no ban, no `RunDeparture` record, and that is the point.**
A move is not a departure, so a moved member keeps the right to
be in the run; they only changed teams. Removal and self-leave write a
`RunDeparture` and with it the guest's exclusion from rejoining; the move
deliberately writes neither. Submissions keep their `team_id`, so the old team
keeps its answers even if it becomes empty.

Audited as `team.member_moved` (both team names). Open clients follow the move
without a reload through the `team_member_moved` WebSocket event on both
teams' chat channels and the run's state channel — see
[realtime.md](realtime.md).

## Request Lifecycle

A typical tenant-scoped request flows through these layers:

```
1. Caddy (reverse proxy) → forwards /api/* to FastAPI

2. Middleware stack (in order):
   a. CORS middleware — validates Origin header
   b. Request context middleware — generates/extracts request_id, binds to structlog
   c. Security headers middleware — adds CSP, X-Frame-Options, etc.

3. Router dispatch — matches URL pattern to handler function

4. Dependency injection (resolved per-request):
   a. get_db() → yields AsyncSession (auto-commit/rollback)
   b. get_current_user() → extracts JWT, loads User from DB
   c. get_org_from_slug() → resolves org slug, checks membership
   d. require_role("...") → checks role level (if endpoint requires it)

5. Handler function:
   a. Validates request body (Pydantic schema)
   b. Creates repository with (session, organization_id)
   c. Performs business logic
   d. Returns response model

6. Response:
   a. Pydantic serialization (role-based PII filtering if applicable)
   b. Session auto-commits (or rollback on exception)
   c. Security headers added by middleware
```

## Pydantic Schemas

Request and response schemas are defined in `app/routers/schemas.py`. The pattern:

- **Create schemas** (`XxxCreate`): fields required for creation, validated with constraints
- **Update schemas** (`XxxUpdate`): all fields optional (partial update)
- **Response schemas** (`XxxResponse`): what the client receives, with computed fields
- **List responses**: include pagination metadata

Example pattern:

```python
class RallyeCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    default_language: str = "de"

class RallyeResponse(BaseModel):
    id: UUID
    title: str
    description: str | None
    station_count: int
    created_at: datetime
    updated_at: datetime
```

## API Documentation

FastAPI auto-generates OpenAPI documentation, available at:

- **Swagger UI**: `/api/docs`
- **ReDoc**: `/api/redoc`
- **OpenAPI JSON**: `/api/openapi.json`

FastAPI serves them, and Caddy passes them through like every `/api` path: http://localhost/api/docs in the local stack.

Both pages load every file from the API's own server, under `/api/docs-assets/`
(`app/api_docs.py`). FastAPI's stock pages load them from cdn.jsdelivr.net, which
the API's content policy blocks, and Clavis makes no request to a third party at
runtime. The files live in `src/backend/app/static/api-docs/` with their licences.
`scripts/update-api-docs-assets.sh` fetches a pinned version of `swagger-ui-dist`
and `redoc` from the npm registry. Two settings keep the pages local: Swagger UI
runs with `validatorUrl: null`, and the script empties the address of the Redocly
logo in the ReDoc bundle.

## Error Responses

The API uses standard HTTP status codes:

| Code | Meaning | When |
|------|---------|------|
| 400 | Bad Request | Invalid request body (Pydantic validation) |
| 401 | Unauthorized | Missing/invalid/expired token, or a session ended by a password change (coded `SESSION_REVOKED`) |
| 403 | Forbidden | Insufficient role or wrong organization |
| 404 | Not Found | Entity doesn't exist (or belongs to another org) |
| 409 | Conflict | Duplicate entry (e.g., email already registered) |
| 422 | Unprocessable Entity | FastAPI validation error (path/query params) |
| 423 | Locked | Account locked due to failed login attempts |
| 429 | Too Many Requests | Rate limit exceeded |

Error response body:

```json
{
  "detail": "Human-readable error message"
}
```

Note: `404` is returned for entities that exist but belong to another organization. This prevents information leakage — an attacker cannot distinguish "doesn't exist" from "exists in another org" by probing UUIDs.

**Coded 401 `SESSION_REVOKED`** is not bound to one endpoint — every authenticated endpoint, user and operator realm alike, can answer it. It means the account's password changed after this token was minted, so the session was ended by that change. The frontend treats it differently from a plain 401: the client routes the user to its login page (`/login` or `/instance/login`) with a notice that the password was changed, instead of surfacing a generic error. The session-version mechanism behind the code is documented in [authentication.md](authentication.md).

### How the frontend receives these

**Every failed response reaches callers as an `ApiError`** (`src/frontend/src/api/client.ts`) carrying `status`, a flattened string `detail`, and any `error_code`. Callers branch on it:

```ts
setError(err instanceof ApiError ? err.detail : t('login.unexpectedError'));
```

That makes the wrapping a **contract, not an implementation detail**: any status that skips it silently degrades every one of those ~30 call sites to its generic fallback. A status that skips it shows the generic message ("Ein unerwarteter Fehler ist aufgetreten") instead of the reason the backend gives.

Two consequences worth keeping in mind when touching the interceptor:

- **Side effects and wrapping are separate concerns.** A 401 from a protected route still redirects to `/login`; on a public path the 401 *is* the answer to the submitted form and must render inline. Do the redirect, then fall through to the wrapping — never return early with the raw `AxiosError`.
- **Read the status off `ApiError`, never off `err.response`.** An `ApiError` has no `response` field. `fetchMyTeam` read it that way and its `404 -> 'not-found'` branch was dead for as long as it existed, undetected because the tests only ever threw a bare `Error`.

Both rules are pinned by tests in `client.test.ts` and `publicRuns.test.ts`.
