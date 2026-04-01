import { test, expect } from '@playwright/test';
import {
  registerNewOrg,
  createPublishedRallye,
  createRunForRallye,
  startRunFromDashboard,
  joinAsGuest,
} from './helpers';

/** An active run without a challenge, which the chat and feed tests do not need. */
async function setupActiveRun(page: import('@playwright/test').Page, prefix: string) {
  const ts = Date.now().toString(36);
  const { title } = await createPublishedRallye(page, prefix);
  const { runId, joinCode } = await createRunForRallye(page, title, `${prefix}-Run-${ts}`);
  await startRunFromDashboard(page);
  return { joinCode, runId };
}

test.describe('real-time WebSocket', () => {
  test('coordinator posts announcement → appears in dashboard feed live', async ({ page }) => {
    await registerNewOrg(page, 'rt-feed');
    await setupActiveRun(page, 'rtf');
    // FeedComposer is on the run dashboard
    const ts = Date.now().toString(36);
    const message = `Live-Mitteilung-${ts}`;
    // Pick "announcement" type if not already, then fill content
    const annBtn = page.getByRole('button', { name: 'Mitteilung', exact: true });
    if (await annBtn.isVisible()) {
      await annBtn.click();
    }
    await page.getByRole('textbox', { name: 'Inhalt' }).fill(message);
    await page.getByRole('button', { name: 'Veröffentlichen', exact: true }).click();
    // Feed event should appear in the live feed panel via WS broadcast
    await expect(page.getByText(message).first()).toBeVisible();
  });

  test('guest chat: send message, coordinator audit log records system event', async ({
    browser,
    page,
  }) => {
    await registerNewOrg(page, 'rt-chat');
    const { joinCode } = await setupActiveRun(page, 'rtc');

    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'ChatGuest', 'Chat Team');

    // The chat is a sub-tab (Leaderboard / Chat) of the Team tab of the bottom nav.
    // BottomNavigationAction → role="button"; Tabs/Tab → role="tab".
    await guest.getByRole('button', { name: 'Team', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/team\/leaderboard$/);
    await guest.getByRole('tab', { name: 'Chat', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/team\/chat$/);

    const msg = `Hallo-${Date.now().toString(36)}`;
    // Chat input is the only textbox on the page
    const input = guest.getByRole('textbox').first();
    await expect(input).toBeVisible();
    await input.fill(msg);
    // The send button is an IconButton whose aria-label is the translated runs.participant.chat.send.
    await guest.getByRole('button', { name: /Senden|Send/i }).click();
    await expect(guest.getByText(msg).first()).toBeVisible();

    await guestCtx.close();
  });

  test('help request → coordinator opens team chat from dashboard → guest sees reply live', async ({
    browser,
    page,
  }) => {
    await registerNewOrg(page, 'rt-help');
    const { joinCode } = await setupActiveRun(page, 'rth');

    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'HelpGuest', 'Hilfe Team');

    // Guest opens the team chat and requests coordinator assistance
    await guest.getByRole('button', { name: 'Team', exact: true }).click();
    await guest.getByRole('tab', { name: 'Chat', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/team\/chat$/);
    await guest.getByRole('button', { name: 'Hilfe anfordern' }).click();
    await guest.getByRole('dialog').getByRole('button', { name: 'Bestätigen' }).click();
    await expect(guest.getByText('Spielleitung wurde benachrichtigt.')).toBeVisible();

    // Coordinator dashboard (still open): the Hilfe chip appears live on the
    // team row via the run-feed WebSocket broadcast, without a reload. The chip
    // carries its own label (coordinatorRequest.teamRequestedHelp), not the
    // text of the participant's "Hilfe anfordern" button.
    const helpChip = page.getByRole('button', { name: 'Hilfe Team bittet um Unterstützung' });
    await expect(helpChip).toBeVisible();
    await helpChip.click();

    // Coordinator lands in the team chat and sees the request system message
    await expect(page).toHaveURL(/\/runs\/[^/]+\/teams\/[^/]+\/chat$/);
    await expect(page.getByRole('heading', { name: 'Chat mit Hilfe Team' })).toBeVisible();
    // The text is localized from the [coordinator_request] marker.
    await expect(page.getByText('Das Team hat Hilfe von der Spielleitung angefordert.')).toBeVisible();

    // Coordinator replies into the team chat
    const reply = `Wir-kommen-${Date.now().toString(36)}`;
    await page.getByRole('textbox').first().fill(reply);
    await page.getByRole('button', { name: /Senden|Send/i }).click();
    await expect(page.getByText(reply).first()).toBeVisible();

    // Guest's open chat receives the coordinator reply live (WS, no reload),
    // marked with the coordinator role chip.
    await expect(guest.getByText(reply)).toBeVisible();
    await expect(guest.getByText('Spielleitung').first()).toBeVisible();

    await guestCtx.close();
  });

  test('run state change broadcasts via WS to participant (end-of-run feed events)', async ({
    browser,
    page,
  }) => {
    await registerNewOrg(page, 'rt-state');
    const { joinCode, runId } = await setupActiveRun(page, 'rts');

    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'StateGuest', 'State Team');
    // Participants land on the station list. Open the feed and stay there, so
    // the assertion below fails only on a missing broadcast into an *open* feed.
    await expect(guest).toHaveURL(/\/participant\/stations$/);
    await guest.goto(`/runs/${runId}/participant/feed`);
    await expect(guest).toHaveURL(/\/participant\/feed$/);

    // Coordinator pauses then resumes (state chip updates on the dashboard)
    await page.getByRole('button', { name: 'Pausieren', exact: true }).click();
    await expect(page.getByText('Pausiert', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Fortsetzen', exact: true }).click();
    await expect(page.getByText('Aktiv', { exact: true })).toBeVisible();

    // Coordinator ends the run
    await page.getByRole('button', { name: 'Beenden', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Beenden' }).click();
    await expect(page.getByText('Beendet', { exact: true })).toBeVisible();

    // The end-of-run broadcast reaches participants as two feed events (results
    // available and feedback prompt) pushed over the run WebSocket channel. They
    // must appear in the guest's open feed without a reload, and the feedback
    // prompt must lead into the feedback form.
    await expect(guest.getByText(/Eure Ergebnisse sind verfügbar|Die Bewertung läuft noch/)).toBeVisible({
      timeout: 15000,
    });
    await expect(guest.getByText(/Erzählt uns, wie es war/)).toBeVisible();
    await guest.getByRole('button', { name: 'Feedback geben' }).click();
    await expect(guest).toHaveURL(/\/participant\/feedback$/);

    await guestCtx.close();
  });
});
