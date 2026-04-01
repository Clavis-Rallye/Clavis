import { test, expect, Page } from '@playwright/test';
import {
  registerNewOrg,
  createPublishedRallye,
  addChallengeToRallye,
  createRunForRallye,
  startRunFromDashboard,
  joinAsGuest,
} from './helpers';

/**
 * Create a published rallye with one challenge, create a run, start it, and
 * return its join code. Uses the coordinator session that is already on `page`.
 */
async function setupActiveRunWithChallenge(
  page: Page,
  prefix: string,
  challengeType: 'Text' | 'Quiz' | 'Foto' | 'QR-Code',
): Promise<{ joinCode: string; runId: string; challengeTitle: string }> {
  const ts = Date.now().toString(36);
  const { rallyeId, title: rallyeTitle } = await createPublishedRallye(page, prefix);

  const challengeTitle = `${prefix}-Ch-${ts}`;
  await addChallengeToRallye(page, rallyeId, challengeTitle, challengeType);

  const { runId, joinCode } = await createRunForRallye(page, rallyeTitle, `${prefix}-Run-${ts}`);
  await startRunFromDashboard(page);

  return { joinCode, runId, challengeTitle };
}

test.describe('participant flow', () => {
  test('guest joins via code → lands in participant shell @smoke', async ({ browser, page }) => {
    await registerNewOrg(page, 'pjoin');
    const { joinCode } = await setupActiveRunWithChallenge(page, 'pj', 'Text');

    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'Anna', 'Team A');
    await expect(guest.getByRole('heading', { level: 1 })).toBeVisible();
    await guestCtx.close();
  });

  test('text challenge: guest opens, submits, success @smoke', async ({ browser, page }) => {
    await registerNewOrg(page, 'ptext');
    const { joinCode, challengeTitle } = await setupActiveRunWithChallenge(page, 'pt', 'Text');

    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'Bea', 'Team B');

    // Open the stations list via the bottom nav's "Rallye" entry, which routes
    // to /stations. BottomNavigationAction renders as role="button", not role="tab".
    await guest.getByRole('button', { name: 'Rallye', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/stations$/);
    // StationList has a ToggleButtonGroup (list/map view) at the top, so
    // getByRole('button') would hit that first. Click the station's CardActionArea.
    await guest.locator('.MuiCardActionArea-root').first().click();
    await expect(guest).toHaveURL(/\/participant\/stations\/[0-9a-f-]+$/);
    await guest.getByText(challengeTitle).click();
    await expect(guest).toHaveURL(/\/challenges\/[0-9a-f-]+$/);
    await guest.locator('textarea').first().fill('Meine Antwort.');
    await guest.getByRole('button', { name: 'Antwort senden' }).click();
    await expect(guest.getByText(/gesendet|gespeichert/i).first()).toBeVisible();
    await guestCtx.close();
  });

  test('run end auto-redirects the guest to feedback, with no org 403s', async ({ browser, page }) => {
    await registerNewOrg(page, 'pfb');
    const { joinCode, runId } = await setupActiveRunWithChallenge(page, 'pfb', 'Text');

    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'Cara', 'Team C');

    // No participant request may be *forbidden*. The coordinator-scoped run
    // read would 403 the guest, so the participant reads the run state through
    // the membership-bound /state endpoint.
    const orgForbidden: string[] = [];
    guest.on('response', (r) => {
      const path = new URL(r.url()).pathname;
      if (r.status() === 403 && path.startsWith('/api/orgs/')) orgForbidden.push(path);
    });

    await page.goto(`/runs/${runId}`);
    await page.getByRole('button', { name: 'Beenden', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Beenden' }).click();
    await expect(page.getByText('Beendet', { exact: true })).toBeVisible();

    // The run_state_change arrives over the guest-authorized WebSocket and the
    // shell redirects to the feedback screen on its own.
    await expect(guest).toHaveURL(
      new RegExp(`/runs/${runId}/participant/feedback$`),
      { timeout: 15000 },
    );
    // MUI Rating hides the radio inputs visually, so click the wrapping label by
    // its accessible text. The stars carry the app's own names ("4 Sterne",
    // "4 stars"), not MUI's default "4 Stars".
    await guest.getByLabel(/^4 (Sterne|stars)$/i).click({ force: true });
    await guest.getByRole('button', { name: /Senden|Abschicken|Submit|senden|absenden/i }).click();
    await expect(guest.getByText(/Danke|Vielen|Thank you|gespeichert/i).first()).toBeVisible();

    expect(orgForbidden, `participant hit forbidden org endpoints: ${orgForbidden.join(', ')}`).toEqual([]);
    await guestCtx.close();
  });
});
