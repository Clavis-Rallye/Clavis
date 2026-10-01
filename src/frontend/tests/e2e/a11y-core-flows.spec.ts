/**
 * axe scans of the five core paths.
 *
 * The spec runs in `chromium` and in `webkit-iphone` (iPhone 15 profile, which
 * the project's testMatch limits to this file). All coordinator setup goes
 * through the API, because the desktop coordinator shell cannot be driven at
 * iPhone size. The browser walks only the sign-in page and participant screens.
 *
 * Unlike the onboarding scans in onboarding-team-flow.spec.ts, no rules are
 * disabled here: a violation on a core path fails the test.
 */
import { expect, Page, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { joinAsGuest, setupActiveRunViaApi } from './helpers';

/** Scan the page and fail with one line per rule id and its targets. */
async function expectNoA11yViolations(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  const summary = results.violations.map(
    (v) => `${v.id} → ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
  expect(summary, `axe violations on ${label}`).toEqual([]);
}

test.describe('a11y — five core paths', () => {
  // Two guest joins (one stops at the team step, one completes) plus five axe
  // analyses per browser project need more than the 60 s default.
  test.setTimeout(90_000);

  test('sign-in, join→team, station, challenge, feed scan clean @a11y', async ({
    browser,
    page,
  }) => {
    // 1 — the sign-in page (public, no setup needed). Scan it before the API
    // login below puts its cookies on this context.
    await page.goto('/login');
    await expect(page.getByRole('button', { name: 'Anmelden' })).toBeVisible();
    await expectNoA11yViolations(page, 'sign-in page');

    const { joinCode, challengeTitle } = await setupActiveRunViaApi(page, 'a11y');

    // 2 — the join flow up to the team step, in a fresh unauthenticated
    // context. The first half of joinAsGuest, spelled out so it can stop here.
    const joinCtx = await browser.newContext();
    const joiner = await joinCtx.newPage();
    await joiner.goto(`/join/${joinCode}`);
    await joiner.getByRole('button', { name: /weiter|continue/i }).click(); // Welcome → auth-choice
    await joiner // auth-choice → profile
      .getByRole('button', { name: /als gast mitmachen|join as a guest/i })
      .click();
    await joiner.getByLabel(/anzeigename|display name/i).fill('Axel');
    await joiner.getByRole('button', { name: /weiter|continue/i }).click(); // profile → team/find
    await expect(joiner).toHaveURL(/\/join\/.+\/team\/find$/);
    await expect(
      joiner.getByRole('button', { name: /create.*team|team.*erstellen/i }),
    ).toBeVisible();
    await expectNoA11yViolations(joiner, 'join flow — team step');
    await joinCtx.close();

    // 3–5 — participant paths after a completed join in another fresh context.
    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'Bea', 'Team A11y');
    // joinAsGuest parks on /runs/:runId/participant, which redirects to the stations list.
    await expect(guest).toHaveURL(/\/participant\/stations$/);

    // 3 — a station view (the station's challenge list).
    await guest.locator('.MuiCardActionArea-root').first().click();
    await expect(guest).toHaveURL(/\/participant\/stations\/[0-9a-f-]+$/);
    await expect(guest.getByRole('heading', { level: 1 })).toBeVisible();
    await expectNoA11yViolations(guest, 'participant station view');

    // 4 — a challenge with its answer form (text challenge → textarea).
    await guest.getByText(challengeTitle).click();
    await expect(guest).toHaveURL(/\/challenges\/[0-9a-f-]+$/);
    await expect(guest.getByRole('button', { name: 'Antwort senden' })).toBeVisible();
    await expectNoA11yViolations(guest, 'challenge answer form');

    // 5 — the participant feed, via the bottom nav's activity tab. Match by
    // regex: with unread notices the accessible name is "Mitteilungen, N neue Meldungen".
    await guest.getByRole('button', { name: /mitteilungen|activities/i }).click();
    await expect(guest).toHaveURL(/\/participant\/feed$/);
    await expect(guest.getByRole('heading', { level: 1 })).toBeVisible();
    await expectNoA11yViolations(guest, 'participant feed');

    await guestCtx.close();
  });
});
