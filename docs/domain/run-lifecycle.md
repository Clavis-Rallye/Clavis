# Run Lifecycle — App Implementation

> **Scope.** The conceptual states and transitions, pause/resume semantics, teams, submissions + scoring, the feed, team chat, ending, and post-run feedback/reporting are the ones the code implements. This file records how.

This file documents the **App-specific implementation**: the concrete state machine, parallel-run admin UX, and the join flow.

## Run States (state machine)

```
Configured ⇄ Scheduled
     │           │
     └────→ Active ←──→ Paused ──→ Ended
                                        ↑
                              (also from Active)
```

| State | Submissions | Content Editable | Timer | Transitions To |
|-------|:-----------:|:----------------:|:-----:|---------------|
| **Configured** | No | Name, team limit, start time | Stopped | Scheduled, Active |
| **Scheduled** | No | Name, team limit, start time | Stopped | Configured, Active |
| **Active** | Yes | No (frozen) | Running | Paused, Ended |
| **Paused** | No | No | Stopped | Active, Ended |
| **Ended** | No | No | Stopped | (terminal) |

- **The start time decides between Configured and Scheduled.** A run created
  with a start time is Scheduled, and setting or clearing the time in the run
  edit (`PUT /runs/{id}`) moves it between the two states.
- **Automatic start.** A Scheduled run starts on its own once its time has
  passed, through the same snapshot, transition and notification as the
  "Starten" button (`services/run_start.py`, checked every 30 s and at API
  start, so a start missed during downtime happens at the next check and is
  audited as late). An organization can switch this off
  (`organizations.settings["auto_start_runs"]`, default on); then the time is
  only a plan. A run whose rallye has no station stays Scheduled, the reason
  sits in `runs.settings["auto_start_blocked"]` for the run page, and the next
  check tries again.
- **No restart.** To repeat an event, create a new run.
- **Multiple concurrent runs** per organization are supported. The admin UI must clearly distinguish between runs to prevent coordinator confusion (e.g., run names, status badges, color-coded indicators).

## After the Run: Grading and Results

The App implements the post-run flow entirely inside the terminal **Ended** state — publishing a result never transitions the run.

### Grading completeness

Grading is **complete** when the run has ended and no live submission is unscored (submissions of soft-deleted teams don't count; a re-opened score makes it incomplete again). The flag is exposed as `grading_complete` on the run and on the public run state. The scoreboard (`GET /api/orgs/{slug}/runs/{run_id}/scores/scoreboard`) reports `scored_count` / `submission_count` and `is_final`; ranks are shared on ties (1, 1, 3), and once the standing is final, rank-1 entries carry `is_winner`.

### The public result link

A coordinator publishes the final standing as an unauthenticated link:

- **Publish** (`POST .../runs/{run_id}/results/publish`, coordinator role) is refused with 409 unless the run is ended **and** grading is complete. It stores a frozen snapshot — run name, end date, organization name, language, entries with rank, team name, points and winner mark — and answers the public URL plus its expiry. **Team names become public** with the snapshot.
- The slug is an HMAC of the link id under the secret key: only its hash is stored, and the slug never appears in the audit log.
- **Re-publish** after a later score change replaces the snapshot under the **same** slug and restarts the expiry. Publishing again after a revoke mints a **new** slug; the revoked one stays dead.
- **Revoke** (`DELETE .../results/publish`) makes the public page answer 410 immediately; `GET .../results/link` reports the link status (unpublished, published with URL and expiry, or revoked).
- **Public read** is `POST /api/public/results/lookup` with the slug in the request body, never in a path or query: 404 for an unknown slug, 410 for a revoked or expired one, otherwise the snapshot — no member or user data.
- **Lifetime**: `result_link_days` (default 90, restarted on re-publish). Expired and revoked links are deleted by the daily retention sweep — see [`docs/development/security-and-compliance.md`](../development/security-and-compliance.md).

## Parallel Run Management

Organizations may have multiple runs in different states simultaneously. The admin interface must handle this without confusion:

### Admin UI Requirements

- **Run list view**: Dashboard showing all runs with clear status indicators (state, rallye name, participant count, start time)
- **Active run prominence**: Active/paused runs are visually prominent; configured/ended runs are secondary
- **Coordinator assignment**: Each run should display its assigned coordinator(s) so responsibilities are clear
- **Context isolation**: When a coordinator is managing a specific run, the UI must make it unambiguous which run they are acting on (e.g., persistent run name in header, color coding)
- **Bulk actions**: End-of-day operations like ending multiple runs should be supported

### Participant Experience

- Participants join a specific run via QR code or join code — they are never exposed to other concurrent runs
- If an organization has multiple active runs, the join mechanism routes to the correct run without ambiguity

## Joining a Run

| Method | Description |
|--------|-------------|
| **QR Code** | Scan to enter the run join flow |
| **Join Code** | Short alphanumeric code (e.g., 6 characters) entered manually |

Both methods lead to the same join process.

### Join Flow

1. Scan QR / enter code
2. Choose: join as guest or log in (registered participant)
3. Create a new team OR join an existing team
4. Begin participating

### Join Rules

- Cannot join multiple teams in the same run
- Cannot join after run has ended
- Guests who leave cannot rejoin
- Registered participant rejoin is **not in scope** for initial release

A join refused because the run has not started or is paused is reported to
coordination live: the refusal broadcasts a `join_blocked` event on the run's
state channel, and the run dashboard counts the refused attempts since the
page opened ([realtime.md](../development/realtime.md)).

### Moving a Member Between Teams

While a run is ACTIVE or PAUSED, a coordinator can move a member to another
team of the same run. **A move writes no ban**: no
departure record is written, so a moved guest keeps the right to be in the
run — they only changed teams. That is the deliberate difference from removal
and self-leave, which write a departure record and with it the guest's
exclusion from rejoining. The member's submissions stay with the old team,
which keeps its answers even if it becomes empty. Open clients follow the move
live through the `team_member_moved` event
([realtime.md](../development/realtime.md)); endpoint details in
[api-structure.md](../development/api-structure.md).
