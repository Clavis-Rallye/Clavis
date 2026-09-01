import { defineConfig } from '@playwright/test';

/**
 * The walkthrough harness captures evidence and is not a test suite. It walks
 * the staff screens (`@staff`) and the participant journey (`@participant`)
 * against the live dev stack at http://localhost and writes screenshots, aria
 * snapshots, axe results and metrics under tests/walkthrough/out/. A walk fails
 * only when a step breaks (login, seed, navigation). UI problems go into the
 * evidence.
 *
 * Each project has its own tag default, so `--project` is only an override:
 *
 *   phone    @participant and @staff (the participant journey, plus a phone
 *                                      check of the staff screens)
 *   tablet   @staff only
 *   desktop  @staff only
 *
 * So `--grep @participant` runs on `phone` alone. WALK_ALL=1 drops the defaults
 * and runs every walk in every viewport.
 *
 * Run from src/frontend:
 *   E2E_BASE_URL=http://localhost WALK_LANG=de npm run walkthrough -- --grep @staff --project=desktop
 *   E2E_BASE_URL=http://localhost WALK_LANG=de npm run walkthrough -- --grep @participant
 *   E2E_BASE_URL=http://localhost WALK_LANG=de WALK_ALL=1 npm run walkthrough
 */

/** WALK_LANG is 'de' (default) or 'en'; it maps onto a full UI locale. */
const walkLang = process.env.WALK_LANG === 'en' ? 'en' : 'de';
const locale = walkLang === 'en' ? 'en-GB' : 'de-DE';

/** WALK_ALL=1 turns the per-project tag defaults off. */
const walkAll = process.env.WALK_ALL === '1';

/**
 * A project-level `grep` is ANDed with the caller's `--grep`, so these two
 * filters narrow the run and never widen it.
 */
const phoneTags = walkAll ? {} : { grep: /@participant|@staff/ };
const staffOnlyTags = walkAll ? {} : { grepInvert: /@participant/ };

export default defineConfig({
  testDir: './tests/walkthrough',
  testMatch: '**/*.walk.ts',
  // Sequential and no retries: a retried step would duplicate the evidence
  // numbers of its capture folder.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 120_000,
  reporter: 'list',
  outputDir: './tests/walkthrough/out/traces',
  globalSetup: './tests/walkthrough/global-setup.ts',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost',
    locale,
    colorScheme: 'light' as const,
  },
  projects: [
    {
      name: 'phone',
      ...phoneTags,
      use: {
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
        deviceScaleFactor: 2,
      },
    },
    {
      name: 'tablet',
      ...staffOnlyTags,
      use: {
        viewport: { width: 820, height: 1180 },
        hasTouch: true,
      },
    },
    {
      name: 'desktop',
      ...staffOnlyTags,
      use: {
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
});
