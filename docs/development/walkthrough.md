# Walkthrough Harness

A review tool, not a test suite. The walks drive the real app on the live dev stack and record
what every screen looks like: screenshot, aria snapshot, axe result, layout metrics, console
output. Nothing is asserted. A step that cannot find its element writes the reason into that
step's `console.json` and the walk continues, so one broken control never hides the other forty
screens. A walk fails only when the seed, the login, the join code or the run state is broken.
The evidence is the deliverable, and a green walk is not a passing test — `tests/e2e/` asserts.

## Seeding

`tests/walkthrough/global-setup.ts` seeds one complete organisation per `playwright test`
invocation: four staff accounts, a published rallye with three Augsburg stations and one challenge
of each of the seven types, plus a started run with its join code. Everything it created lands in
`tests/walkthrough/out/seed.json`.

Set `WALK_SEED=<path>` to reuse an existing `seed.json` instead of seeding a new organisation.
That is how the same run is walked twice in two languages: seed once in German, then reuse the
file for the English pass, and the two evidence trees compare screen for screen. The participant
walks append the teams they built to that file under `teams` (id, name, share code), so a
reviewer can find them again on the coordinator dashboard of the run.

## Running

Bring the stack up from the repo root, then run everything below from `src/frontend`.

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# staff screens, German, desktop viewport — seeds a fresh organisation
E2E_BASE_URL=http://localhost WALK_LANG=de \
  npm run walkthrough -- --grep @staff --project=desktop

# guest + registered participant, German — reuses that seed, phone by default
E2E_BASE_URL=http://localhost WALK_LANG=de WALK_SEED=tests/walkthrough/out/seed.json \
  npm run walkthrough -- --grep @participant

# the same participant journey in English, on the same organisation
WALK_LANG=en WALK_SEED=tests/walkthrough/out/seed.json \
  npm run walkthrough -- --grep @participant
npm run walkthrough:index   # builds out/index.html, the review page
```

Each project carries a tag default, so the viewport follows from the persona and `--project` is
only an override: `phone` (390x844, touch) runs `@participant` and `@staff`, `tablet` (820x1180)
and `desktop` (1440x900) run `@staff` alone. The participant journey therefore never walks a
viewport no participant uses. `WALK_ALL=1` drops the defaults and runs every walk in every
viewport. `WALK_LANG` is `de` (default) or `en`; it sets the browser locale and the app's stored
language before the first navigation. The `@participant` walks need the seeded run to be ACTIVE,
and stop with its actual state if it is not.

## Evidence

One folder per step, numbered in walk order:
`out/<project>/<lang>/<persona>/<NN>-<step>/`. `out/` is git-ignored.

| File | Holds |
|---|---|
| `screen.png` | The full page at CSS scale. |
| `viewport.png` | The viewport only, which is what the user actually sees first. |
| `aria.yml` | The accessibility tree of `<body>`: the screen as a screen reader reads it. |
| `axe.json` | The axe result for WCAG 2.1 A and AA. Informational, never a gate. |
| `metrics.json` | URL, title, `lang`, heading counts, horizontal overflow, touch targets below 44 px, text below 12 px, the largest image's natural versus rendered size, the focused element. |
| `console.json` | Console errors and warnings since the previous capture, plus the walk's own notes for anything a step could not do. |
