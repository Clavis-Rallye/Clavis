# Frontend E2E (Playwright)

End-to-end tests that drive the **live dev stack** at `http://localhost`
(Caddy → Vite + FastAPI). Most specs register a fresh org with a
timestamp-keyed slug and email, so reruns don't collide on the shared dev DB.

## Run

Create `.env` in the repository root first, with `JWT_SECRET_KEY` and `HMAC_AUDIT_KEY`.
`./scripts/quickstart.sh` creates it.

```bash
# 1. Bring the dev stack up
cd <repo-root>
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build --wait
```

`--build` builds the development images, so an image that `quickstart.sh` built for production is not reused.
On a host where another Clavis stack runs, see [Try it on your computer](../../../../README.md#try-it-on-your-computer) in the main `README.md` to keep the two stacks apart.
The API applies the database migrations when it starts, so a fresh volume needs no separate step.
`--wait` returns when every service is healthy.

```bash
# 2. Run from src/frontend. Install the packages, then the browsers once: chromium and webkit.
npm ci
npx playwright install chromium webkit
npm run test:e2e             # headless
npm run test:e2e:headed      # see the browser
npm run test:e2e:ui          # Playwright's interactive runner

# Single file or single test
npx playwright test tests/e2e/auth.spec.ts
npx playwright test tests/e2e/auth.spec.ts:5
```

## Layout

| File | Covers |
|---|---|
| `helpers.ts` | Shared setup: `registerNewOrg`, `login`, `navTo`, rallye, station and challenge builders, run start and end, API setup of an active run, guest join, text answer, scoring. |
| `a11y-core-flows.spec.ts` | axe scans of five core paths (sign-in, join, station, challenge, feed). Also runs in the `webkit-iphone` project. |
| `auth.spec.ts` | Register, logout, login round-trip, and the 422 detail-array regression (a reserved-TLD email shows an inline error). |
| `logs.spec.ts` | Audit log viewer: events, CSV export, category filter. |
| `nav.spec.ts` | Every sidebar route loads through Caddy. The first Tab reaches the skip link. Language switch DE↔EN. |
| `onboarding-edge-cases.spec.ts` | Password mismatch and weak password, join-code errors, browser-back in the onboarding steps. |
| `onboarding-team-flow.spec.ts` | Team flow with a creator and a joiner at phone size: create, join, name, start. Browser-back through every step. |
| `participant-chat-layout.spec.ts` | The team chat input stays above the bottom navigation at four phone sizes. |
| `participant.spec.ts` | Guest joins by code, submits a text challenge, is sent to feedback when the run ends. |
| `profile.spec.ts` | Display name update, password change, weak password rejected. |
| `rallyes.spec.ts` | Rallye create, search filter, draft and published filter. |
| `realtime.spec.ts` | WebSocket updates: announcements, team chat, help request, run end. |
| `runs.spec.ts` | Run create, start, pause, resume, end, schedule, join code and QR. |
| `scoring.spec.ts` | Scoring, re-open grading, re-scoring, scoreboard, manual override of a quiz score. |
| `settings.spec.ts` | Organisation settings and user CRUD: rename, read-only slug, create, edit role, delete, deactivate. |
| `station-reorder.spec.ts` | Drag and drop reorder of stations persists across reload. |
| `stations-challenges.spec.ts` | Station and challenge CRUD, challenge type toggles, publish and unpublish. |
| `team-dissolution.spec.ts` | A team dissolves with its last member, its name is free again, a whole team is removed. |
| `users.spec.ts` | User table, role filter, password policy in the create dialog. |
| `welcome-language-persistence.spec.ts` | The language chosen on `/welcome` persists into a fresh session. |
| `welcome-polish.spec.ts` | `/welcome` at 390×844: axe scans, screenshot, silent skip of an unknown code, next-page timing. |

## Conventions

- **Selectors**: prefer `getByRole({ exact: true })` over `getByLabel`. MUI labels
  partially overlap (`Name der Organisation` is a substring of `Kurzname der Organisation`)
  and required-field labels get a trailing ` *` that breaks `getByLabel` matching.
- **Password fields**: `<input type="password">` has no implicit ARIA role. Use
  `page.locator('input[type="password"]')` indexed by `nth()`.
- **Multilingual fields**: `MultilingualField` renders one input at a time. The
  active-locale tab is reflected in the input's `aria-label`, e.g.
  `getByRole('textbox', { name: /Titel.*Deutsch/i })`.
- **org_admin login** lands on `/2fa-setup` (TOTP enforcement), not `/dashboard`.
  `helpers.login()` accepts either.

## CI

Two tiers. Both need the live API and database, so each job composes its own
docker stack instead of mocking it.

- **PR gate.** The job `e2e-smoke` ("Frontend / E2E smoke") in
  `.github/workflows/ci.yml` runs `npx playwright test --grep "@smoke"`. The
  required check `CI / required` waits for it.
- **Full suite.** `.github/workflows/e2e-full.yml` runs every spec in two shards
  (job `e2e-full`). It does not gate a merge. It starts nightly at 03:17 UTC
  when `main` had commits in the last 24 hours or the last nightly was not green
  (job `guard`), on every `v*` release tag, and by `workflow_dispatch`. A red run
  opens or updates an investigation issue (job `report`). When a runner was aborted,
  `report` re-runs the nightly once instead and opens no issue.
