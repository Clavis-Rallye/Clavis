import { defineConfig, devices } from '@playwright/test';

/**
 * The E2E suite runs against the live dev stack at http://localhost (Caddy on
 * :80 in front of the Vite frontend and the FastAPI backend).
 *
 * Bring the stack up first:
 *   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
 *
 * Then run from src/frontend:
 *   npx playwright test            # headless
 *   npx playwright test --ui       # interactive runner
 *   npx playwright test --headed   # see the browser
 */
export default defineConfig({
  testDir: './tests/e2e',
  // Each test gets its own timestamp-keyed org slug, so reruns don't collide.
  // Sequential runs keep the shared dev stack predictable.
  fullyParallel: false,
  workers: 1,
  // One retry absorbs the render or network race when a canSubmit gate waits
  // for auth and rallye context to load in turn. A second failure still fails.
  retries: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  // The scoring and scoreboard tests register an org, set up a rallye and an
  // active run, join as a guest, submit and score. That adds up on self-hosted
  // runners: a 30 s limit leaves almost no time for the final assertions.
  timeout: 60_000,
  // The default of 5 s is too tight for a post-submit navigation through the
  // whole stack (caddy, frontend, backend, postgres) under CI load. The
  // redirect of registerNewOrg and the participant shell's nav would flake.
  // 15 s stays well under the 60 s test limit.
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    locale: 'de-DE',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // The a11y scans also run in mobile WebKit. Only the a11y spec runs here:
      // every other spec drives the desktop coordinator shell (sidebar nav,
      // wizards), which an iPhone viewport cannot show. Guest contexts from
      // browser.newContext() inherit this device profile.
      name: 'webkit-iphone',
      use: { ...devices['iPhone 15'] },
      testMatch: /a11y-core-flows\.spec\.ts/,
    },
  ],
});
