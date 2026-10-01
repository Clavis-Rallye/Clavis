# The feedback module for test sessions

The App carries an optional feedback bar for test sessions, such as the product walkthrough.
The bar is the tester's only guide. It shows the current step's card and moves on ok, skip, back
or a block. It also takes notes and can point at an element. The App keeps the session plan, and a session
kit uploads it and follows the events. The module is off by default and stays off in production.

In code the module is called **QA feedback**, to keep it apart from `feedback`, the
participants' rating after a run.

| Where | What |
|---|---|
| `src/backend/app/config.py`, `QaFeedbackSettings` | The switch and its settings |
| `src/backend/app/routers/qa_feedback.py` | The API under `/api/qa` |
| `src/backend/app/models/qa_feedback.py` | Tables `qa_notes` and `qa_state` |
| `src/backend/app/services/qa_feedback_retention.py` | The retention task |
| `src/frontend/src/qa/` | The mount, the bar, its transport and the pointer |

## Switching it on

The switch is read at runtime. The frontend asks `GET /api/public/config` at boot, and only
`qa_feedback: true` loads the bar's lazy chunk. A switch needs no frontend rebuild.

| Variable | Default | Meaning |
|---|---|---|
| `QA_FEEDBACK_ENABLED` | `false` | The switch. Off, every `/api/qa` endpoint answers 404 |
| `QA_FEEDBACK_TOKEN` | empty | The kit's credential in the header `X-QA-Token`, at least 32 characters |
| `QA_FEEDBACK_RETENTION_DAYS` | `30` | The retention sweep deletes older notes, also with the module off |
| `QA_FEEDBACK_NOTE_RATE_LIMIT` | `120/minute` | Events per client address. A step move is an event |

Set the variables on the api service and recreate the container. A restart alone does not read
a changed compose environment. Generate a token with `openssl rand -hex 24`. A token under 32
characters stops the API at startup.

## A session

1. The kit uploads the plan with `PUT /api/qa/session`: ordered steps, each with a card
   (`title`, `do`, `expect`, `needs`, `role`, `device`). The answer holds a new join key. Every
   earlier key ends. The App stores only the key's SHA-256 hash.
2. The tester opens `<origin>/#qa-join=<key>` once on every device. The key rides in the URL
   fragment, which no server sees. The bar keeps the key and
   removes it from the address.
3. A tap on ok, skip or back stores an event and moves the step in the same request, but only
   when the event names the current step. A double tap or a late retry moves once. The answer
   carries the next card, so the tapping device needs no poll.
4. The kit reads the events with `GET /api/qa/notes?after=<seq>` and follows the moves.
5. The kit writes to the tester only through `PATCH /api/qa/state` with `message`. A note sent
   while the message shows answers it.

## The API

| Method and path | Who |
|---|---|
| `PUT /api/qa/session`, `DELETE /api/qa/session` | kit. Upload a plan with a new key, or clear plan, key and message |
| `GET /api/qa/state` | joined or kit. Anyone else gets only `{"joined": false}` |
| `PATCH /api/qa/state` | kit. `current`, `hidden`, `message` |
| `POST /api/qa/notes` | joined, rate limited. ok, skip, back, goto or note, with the action trail |
| `GET /api/qa/notes?after=<seq>` | kit. Events after the cursor, ascending |
| `GET /api/qa/notes/export?format=json` or `csv` | kit. Every event as an attachment |
| `DELETE /api/qa/notes` | kit. Deletes every event |

"Kit" means a valid `X-QA-Token` or an operator session. An organization user gets 403. A
present but wrong token gets 401 and never falls through to the operator check. "Joined" means
the header `X-QA-Key` with the current session's key.

## Rules that keep it safe

- **No foreign keys.** Notes survive the deletion of an organization and stay outside the
  cascade graph of `app/services/org_deletion.py`. A new foreign key from `qa_notes` to
  `organizations` would change that, and the graph test would include the table.
- **No identity.** A note holds no user id, no email and no address. The organization slug and
  the role are free text in `context`.
- **The plan is a secret.** A card may hold the test instance's logins. Only a joined browser or the kit
  reads it, and the retention sweep clears a plan untouched for the retention window.
- **No typed values.** The action trail records clicks by selector and text, routes, JavaScript
  errors and failed API answers, never a field's value.
- **No session contact.** The bar uses plain `fetch` with `credentials: 'omit'`, never
  `apiClient`. The anonymous endpoints never read a session, so a poll never renews an idle
  staff session.
- **No cache.** The service worker serves `/api/qa/` NetworkOnly. The bar keeps unsent notes in
  its own `localStorage` outbox and retries them.

## Hiding the bar

Hidden means the bar renders nothing, so a screen reader pass hears only the product.

- The switch: no chunk is loaded at all.
- `PATCH /api/qa/state` with `hidden: true`: every bar on every device, within one poll.
- No join key in this browser: no request and no element at all.
- `?qa=off` in the address: this browser, until `?qa=on`.
- The bar's own hide button: same as `?qa=off`.

## The observer channel

After each stored or queued event, the bar writes one console line
`[OBS]{"t":"feedback",…}`. A browser-automation observer attached over the Chrome DevTools
Protocol can journal that line and take a screenshot. The bar does not depend on any observer.

## Testing

- Backend: `tests/test_qa_feedback.py`.
- Frontend: `src/qa/*.test.ts(x)` and `src/config/instanceConfig.test.ts`.
- The module is off in the E2E stack and in CI, so no other suite sees the bar.
