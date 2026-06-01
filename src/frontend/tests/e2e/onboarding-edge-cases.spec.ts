/**
 * Onboarding edge cases. The happy path is in onboarding-team-flow.spec.ts.
 *   - ProfileSetupStep password confirmation: mismatch, match, weak.
 *   - JoinTeamStep: the submit button is hidden until a code is typed, and an
 *     unknown code shows an error.
 *   - Browser-back between auth steps.
 *
 * Setup uses the isolated `request` fixture (its own cookie jar), not the
 * page's context. Otherwise the org-admin cookies would leak onto the
 * participant page, and /join/<code> would skip Welcome → EntryChoice → Profile
 * and land on FindTeam.
 */
import { expect, type APIRequestContext, Page, test } from '@playwright/test';

async function setupRun(request: APIRequestContext, prefix: string): Promise<{ joinCode: string }> {
  const ts = Date.now().toString(36) + Math.floor(performance.now()).toString(36);
  const slug = `e2e-${prefix}-${ts}`.toLowerCase().slice(0, 40);
  const email = `${slug}@example.com`;
  const password = 'E2ePass1234!';

  const reg = await request.post('/api/auth/register', {
    data: { email, password, display_name: `E2E ${prefix}`, organization_name: `E2E ${prefix} ${ts}`, organization_slug: slug },
  });
  expect(reg.ok(), await reg.text()).toBeTruthy();
  const login = await request.post('/api/auth/login', { data: { email, password } });
  expect(login.ok(), await login.text()).toBeTruthy();

  const rallye = await request.post(`/api/orgs/${slug}/rallyes/`, {
    data: { title: { de: `${prefix} Rallye` }, description: { de: 'edge' }, is_published: true },
  });
  expect(rallye.ok(), await rallye.text()).toBeTruthy();
  const rallyeId = (await rallye.json()).id;
  const station = await request.post(`/api/orgs/${slug}/rallyes/${rallyeId}/stations/`, {
    data: { title: { de: 'S1' }, description: { de: 'd' }, sort_order: 0 },
  });
  const stationId = (await station.json()).id;
  await request.post(`/api/orgs/${slug}/rallyes/${rallyeId}/stations/${stationId}/challenges/`, {
    data: { challenge_type: 'text', title: { de: 'C1' }, instructions: { de: 'do' }, scoring_rules: { max_points: 10 }, sort_order: 0 },
  });
  const run = await request.post(`/api/orgs/${slug}/runs/`, { data: { rallye_id: rallyeId, name: `${prefix}-Run` } });
  const runJson = await run.json();
  await request.post(`/api/orgs/${slug}/runs/${runJson.id}/start`);
  return { joinCode: runJson.join_code };
}

/** Welcome → EntryChoice ("Als Gast mitmachen") → ProfileSetupStep, guest switch on. */
async function gotoProfile(page: Page, joinCode: string): Promise<void> {
  await page.goto(`/join/${joinCode}`);
  await page.getByRole('button', { name: /weiter|continue/i }).click();
  await page.getByRole('button', { name: /als gast mitmachen|join as a guest/i }).click();
  await expect(page.locator('#profile-username')).toBeVisible();
}

/** Reveal the account fields (email/password/confirm) by switching off guest mode. */
async function switchToAccountMode(page: Page): Promise<void> {
  const guestSwitch = page.getByRole('switch');
  if ((await guestSwitch.getAttribute('aria-checked')) === 'true') await guestSwitch.click();
  await expect(page.locator('#profile-password-confirm')).toBeVisible();
}

/** Profile (guest) → fill name → FindTeam → choose "existing team" → JoinTeamStep. */
async function gotoJoinTeam(page: Page, joinCode: string, name = 'Bob'): Promise<void> {
  await gotoProfile(page, joinCode);
  await page.locator('#profile-username').fill(name);
  await page.getByRole('button', { name: /weiter|continue/i }).click();
  await expect(page).toHaveURL(/\/team\/find$/);
  // FindTeam's "Weiter" is disabled until a card is chosen.
  await page.getByRole('button', { name: /bestehendem team beitreten|existing.*team/i }).click();
  await page.getByRole('button', { name: /weiter|continue/i }).click();
  await expect(page).toHaveURL(/\/team\/join$/);
}

test.describe('Onboarding auth-flow edge cases', () => {
  test('password mismatch blocks account creation and shows the mismatch error', async ({ page, request }) => {
    const { joinCode } = await setupRun(request, 'pwmm');
    await gotoProfile(page, joinCode);
    await switchToAccountMode(page);

    await page.locator('#profile-username').fill('Mismatch User');
    await page.locator('#profile-email').fill(`mm-${Date.now()}@example.com`);
    await page.locator('#profile-password').fill('ValidPass1!');
    await page.locator('#profile-password-confirm').fill('DifferentPass2!');
    await page.getByRole('button', { name: /weiter|continue/i }).click();

    await expect(page.getByText(/stimmen nicht überein/i)).toBeVisible();
    await expect(page.locator('#profile-password-confirm')).toBeVisible();
  });

  test('matching valid passwords create the account and advance to the team flow', async ({ page, request }) => {
    const { joinCode } = await setupRun(request, 'pwok');
    await gotoProfile(page, joinCode);
    await switchToAccountMode(page);

    await page.locator('#profile-username').fill('Match User');
    await page.locator('#profile-email').fill(`ok-${Date.now()}@example.com`);
    await page.locator('#profile-password').fill('ValidPass1!');
    await page.locator('#profile-password-confirm').fill('ValidPass1!');
    await page.getByRole('button', { name: /weiter|continue/i }).click();

    await expect(page).toHaveURL(/\/team\/find$/, { timeout: 15_000 });
  });

  test('weak password is rejected even when confirmation matches', async ({ page, request }) => {
    const { joinCode } = await setupRun(request, 'pwweak');
    await gotoProfile(page, joinCode);
    await switchToAccountMode(page);

    await page.locator('#profile-username').fill('Weak User');
    await page.locator('#profile-email').fill(`weak-${Date.now()}@example.com`);
    await page.locator('#profile-password').fill('weak');
    await page.locator('#profile-password-confirm').fill('weak');
    await page.getByRole('button', { name: /weiter|continue/i }).click();

    await expect(page.getByText(/zu schwach/i)).toBeVisible();
    await expect(page.locator('#profile-password')).toBeVisible();
  });
});

test.describe('Onboarding team-flow edge cases', () => {
  test('JoinTeam: the "Team beitreten" submit appears only after a code is typed', async ({ page, request }) => {
    const { joinCode } = await setupRun(request, 'jhide');
    await gotoJoinTeam(page, joinCode);

    await expect(page.getByRole('button', { name: /team beitreten/i })).toHaveCount(0);
    await page.getByLabel(/team.?code/i).fill('AB12CD');
    await expect(page.getByRole('button', { name: /team beitreten/i })).toBeVisible();
  });

  test('JoinTeam: an unknown team code surfaces the not-found error and stays on the step', async ({ page, request }) => {
    const { joinCode } = await setupRun(request, 'jbad');
    await gotoJoinTeam(page, joinCode);

    await page.getByLabel(/team.?code/i).fill('ZZ99XX');
    await page.getByRole('button', { name: /team beitreten/i }).click();

    await expect(page.getByText(/Code unbekannt|unbekannt|not.*found/i)).toBeVisible();
    await expect(page).toHaveURL(/\/team\/join$/);
  });
});

test.describe('Onboarding navigation edges', () => {
  test('browser-back from ProfileSetup returns to the entry choice', async ({ page, request }) => {
    const { joinCode } = await setupRun(request, 'back');
    await page.goto(`/join/${joinCode}`);
    await page.getByRole('button', { name: /weiter|continue/i }).click();
    await expect(page.getByRole('button', { name: /als gast mitmachen|join as a guest/i })).toBeVisible();
    await page.getByRole('button', { name: /als gast mitmachen|join as a guest/i }).click();
    await expect(page.locator('#profile-username')).toBeVisible();

    await page.goBack();
    await expect(page.getByRole('button', { name: /als gast mitmachen|join as a guest/i })).toBeVisible();
  });

  test('register flow: browser-back returns to the org picker, not skips it', async ({ page, request }) => {
    // Seed a public org via the isolated request fixture so `page` stays
    // unauthenticated (an org-admin cookie on `page` would change /register).
    const ts = Date.now().toString(36) + Math.floor(performance.now()).toString(36);
    const slug = `e2e-orgback-${ts}`.toLowerCase().slice(0, 40);
    const orgName = `E2E OrgBack ${ts}`;
    const reg = await request.post('/api/auth/register', {
      data: {
        email: `${slug}@example.com`,
        password: 'E2ePass1234!',
        display_name: 'E2E OB',
        organization_name: orgName,
        organization_slug: slug,
      },
    });
    expect(reg.ok(), await reg.text()).toBeTruthy();

    // Direct /register (no run-code) → the org picker.
    await page.goto('/register');
    await page.getByText(orgName).click(); // select the seeded org
    await page.getByRole('button', { name: /weiter|continue/i }).click(); // → profile form
    await expect(page.locator('#profile-username')).toBeVisible();

    // Selecting the org pushes `?org=`, so the back button returns to the picker
    // and does not skip it on the sticky slug.
    await page.goBack();
    await expect(page.getByText(orgName)).toBeVisible();
    await expect(page.locator('#profile-username')).not.toBeVisible();
  });
});
