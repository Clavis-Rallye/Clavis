import { test, expect } from '@playwright/test';
import {
  registerNewOrg,
  createPublishedRallye,
  addChallengeToRallye,
  addAutoScoredQuizChallengeToRallye,
  createRunForRallye,
  startRunFromDashboard,
  endRunFromDashboard,
  joinAsGuest,
} from './helpers';

/** A published rallye with one station and one text challenge, and a started run of it. */
async function setupRunWithSubmittableChallenge(
  page: import('@playwright/test').Page,
  prefix: string,
) {
  const ts = Date.now().toString(36);
  const { rallyeId, title: rallyeTitle } = await createPublishedRallye(page, prefix);
  const challengeTitle = `${prefix}-Ch-${ts}`;
  await addChallengeToRallye(page, rallyeId, challengeTitle, 'Text');
  const { runId, joinCode } = await createRunForRallye(page, rallyeTitle, `${prefix}-Run-${ts}`);
  await startRunFromDashboard(page);
  return { joinCode, runId, challengeTitle };
}

/**
 * Scores the first submission row and waits for the `POST .../scores/` response.
 * SubmissionReview renders the score chip ("X / 10") from that response, not from
 * a WebSocket feed. Without the wait, an assert on the chip races the round trip
 * and flakes under CI load.
 */
async function scoreFirstSubmission(
  page: import('@playwright/test').Page,
  points: string,
) {
  const scored = page.waitForResponse(
    (r) =>
      r.request().method() === 'POST' &&
      /\/scores\/?$/.test(new URL(r.url()).pathname),
  );
  await page.getByRole('spinbutton', { name: 'Punkte' }).first().fill(points);
  await page.getByRole('button', { name: 'Bewerten' }).first().click();
  await scored;
}

/**
 * Re-opens the first scored row (deletes its score) through the ConfirmDialog.
 * The wait for the dialog to close lets the row's ScoringRow re-mount before the
 * caller scores it again.
 */
async function reopenFirstScoredSubmission(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Bewertung zurücknehmen' }).first().click();
  await page.getByRole('button', { name: 'Zurücknehmen', exact: true }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
}

test.describe('coordinator scoring + scoreboard', () => {
  // These flows join a guest, grade, end the run and grade again. On the CI
  // runners they take 45 to 60 s, which is the default limit.
  test.describe.configure({ timeout: 120_000 });

  test('score a guest submission → unscored count decreases', async ({ browser, page }) => {
    await registerNewOrg(page, 'score');
    const { joinCode, runId, challengeTitle } = await setupRunWithSubmittableChallenge(page, 'sc');

    // Guest submits
    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'ScoreGuest', 'Score Team');
    // A bottom navigation entry has role="button", not role="tab".
    await guest.getByRole('button', { name: 'Rallye', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/stations$/);
    // StationList has a ToggleButtonGroup (list/map view) at the top, so target
    // the CardActionArea of a station directly.
    await guest.locator('.MuiCardActionArea-root').first().click();
    await guest.getByText(challengeTitle).click();
    await guest.locator('textarea').first().fill('Antwort.');
    await guest.getByRole('button', { name: 'Antwort senden' }).click();
    await expect(guest.getByText(/gesendet|gespeichert/i).first()).toBeVisible();
    await guestCtx.close();

    // Coordinator → SubmissionReview
    await page.goto(`/runs/${runId}/submissions`);
    await expect(page.getByRole('heading', { name: 'Einreichungen' })).toBeVisible();
    await page.getByRole('button', { name: 'Unbewertet', exact: true }).click();

    // The first row shows the reference answer and the challenge's max points.
    await expect(page.getByText('Referenz: 42.')).toBeVisible();
    await expect(page.getByText(/max\. 10/).first()).toBeVisible();
    await scoreFirstSubmission(page, '7');

    // The pattern matches the whole "7 / 10", because a bare "7" also occurs
    // elsewhere on the page.
    await page.getByRole('button', { name: 'Bewertet', exact: true }).click();
    await expect(page.getByText(/\b7\s*\/\s*10\b/)).toBeVisible();
  });

  test('re-open grading → guest replaces answer → coordinator re-scores', async ({ browser, page }) => {
    // The longest chain in the suite: two browser contexts, the full guest join
    // wizard, score, un-score, replace the answer, score again. `test.slow()`
    // triples the describe's 120 s limit for this test alone.
    test.slow();

    await registerNewOrg(page, 'reopen');
    const { joinCode, runId, challengeTitle } = await setupRunWithSubmittableChallenge(page, 're');

    // Guest submits a first answer
    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'ReGuest', 'Reopen Team');
    await guest.getByRole('button', { name: 'Rallye', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/stations$/);
    await guest.locator('.MuiCardActionArea-root').first().click();
    await guest.getByText(challengeTitle).click();
    await guest.locator('textarea').first().fill('Erste Antwort.');
    await guest.getByRole('button', { name: 'Antwort senden' }).click();
    await expect(guest.getByText(/gesendet|gespeichert/i).first()).toBeVisible();

    // Coordinator scores it
    await page.goto(`/runs/${runId}/submissions`);
    await expect(page.getByRole('heading', { name: 'Einreichungen' })).toBeVisible();
    await scoreFirstSubmission(page, '5');
    await expect(page.getByText(/\b5\s*\/\s*10\b/)).toBeVisible();

    // Coordinator re-opens the grading (ConfirmDialog)
    await page.getByRole('button', { name: 'Bewertung zurücknehmen' }).click();
    await page.getByRole('button', { name: 'Zurücknehmen', exact: true }).click();
    await expect(page.getByText(/\b5\s*\/\s*10\b/)).not.toBeVisible();

    // Guest replaces the answer via the UI (reload to pick up the re-opened state)
    await guest.reload();
    await guest.getByRole('button', { name: 'Antwort ändern' }).click();
    await guest.locator('textarea').first().fill('Zweite Antwort.');
    await guest.getByRole('button', { name: 'Antwort senden' }).click();
    // Replace mode ends on success, so the change button is back
    await expect(guest.getByRole('button', { name: 'Antwort ändern' })).toBeVisible();
    await guestCtx.close();

    // Coordinator sees the new text and scores again
    await page.reload();
    await expect(page.getByText('Zweite Antwort.')).toBeVisible();
    await scoreFirstSubmission(page, '9');
    await expect(page.getByText(/\b9\s*\/\s*10\b/)).toBeVisible();
  });

  test('scoreboard reflects the awarded score', async ({ browser, page }) => {
    await registerNewOrg(page, 'sboard');
    const { joinCode, runId, challengeTitle } = await setupRunWithSubmittableChallenge(page, 'sb');

    // Guest submits
    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'SbGuest', 'SB Team');
    await guest.getByRole('button', { name: 'Rallye', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/stations$/);
    await guest.locator('.MuiCardActionArea-root').first().click();
    await guest.getByText(challengeTitle).click();
    await guest.locator('textarea').first().fill('Antwort.');
    await guest.getByRole('button', { name: 'Antwort senden' }).click();
    await expect(guest.getByText(/gesendet|gespeichert/i).first()).toBeVisible();
    await guestCtx.close();

    // Coordinator scores
    await page.goto(`/runs/${runId}/submissions`);
    await page.getByRole('button', { name: 'Unbewertet', exact: true }).click();
    await scoreFirstSubmission(page, '9');

    // Scoreboard
    await page.goto(`/runs/${runId}/scoreboard`);
    await expect(page.getByRole('cell', { name: 'SB Team' })).toBeVisible();
    await expect(page.getByRole('cell', { name: '9', exact: true })).toBeVisible();
  });

  test('re-open after the run ended → re-score with different points → leaderboard updates', async ({
    browser,
    page,
  }) => {
    await registerNewOrg(page, 'endre');
    const { joinCode, runId, challengeTitle } = await setupRunWithSubmittableChallenge(page, 'er');

    // Guest submits
    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'EndReGuest', 'EndRe Team');
    await guest.getByRole('button', { name: 'Rallye', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/stations$/);
    await guest.locator('.MuiCardActionArea-root').first().click();
    await guest.getByText(challengeTitle).click();
    await guest.locator('textarea').first().fill('Antwort.');
    await guest.getByRole('button', { name: 'Antwort senden' }).click();
    await expect(guest.getByText(/gesendet|gespeichert/i).first()).toBeVisible();
    await guestCtx.close();

    // Coordinator scores it once
    await page.goto(`/runs/${runId}/submissions`);
    await expect(page.getByRole('heading', { name: 'Einreichungen' })).toBeVisible();
    await scoreFirstSubmission(page, '5');
    await expect(page.getByText(/\b5\s*\/\s*10\b/)).toBeVisible();

    // Coordinator ends the run. Re-opening a graded submission is not
    // restricted to active runs.
    await page.goto(`/runs/${runId}`);
    await endRunFromDashboard(page);

    // Re-open the scored submission and score it again with other points
    await page.goto(`/runs/${runId}/submissions`);
    await expect(page.getByText(/\b5\s*\/\s*10\b/)).toBeVisible();
    await reopenFirstScoredSubmission(page);
    await expect(page.getByText(/\b5\s*\/\s*10\b/)).not.toBeVisible();
    await scoreFirstSubmission(page, '8');
    await expect(page.getByText(/\b8\s*\/\s*10\b/)).toBeVisible();

    // The score, the leaderboard and the team's total change accordingly
    await page.goto(`/runs/${runId}/scoreboard`);
    await expect(page.getByRole('cell', { name: 'EndRe Team' })).toBeVisible();
    await expect(page.getByRole('cell', { name: '8', exact: true })).toBeVisible();
    await expect(page.getByRole('cell', { name: '5', exact: true })).not.toBeVisible();
  });

  test('quiz answer auto-scored → coordinator overrides by hand → override persists', async ({
    browser,
    page,
  }) => {
    await registerNewOrg(page, 'autoscore');
    const { rallyeId, title: rallyeTitle } = await createPublishedRallye(page, 'as');
    const ts = Date.now().toString(36);
    const challengeTitle = `as-Quiz-${ts}`;
    await addAutoScoredQuizChallengeToRallye(
      page,
      rallyeId,
      challengeTitle,
      ['Augsburg', 'München'],
      'Augsburg',
    );
    const { runId, joinCode } = await createRunForRallye(page, rallyeTitle, `as-Run-${ts}`);
    await startRunFromDashboard(page);

    // The guest answers the quiz correctly. The backend scores it to max_points
    // on submission, without the coordinator.
    const guestCtx = await browser.newContext();
    const guest = await joinAsGuest(guestCtx, joinCode, 'AutoGuest', 'Auto Team');
    await guest.getByRole('button', { name: 'Rallye', exact: true }).click();
    await expect(guest).toHaveURL(/\/participant\/stations$/);
    await guest.locator('.MuiCardActionArea-root').first().click();
    await guest.getByText(challengeTitle).click();
    await guest.getByRole('radio', { name: 'Augsburg' }).check();
    await guest.getByRole('button', { name: 'Antwort senden' }).click();
    await expect(guest.getByText(/gesendet|gespeichert/i).first()).toBeVisible();
    await guestCtx.close();

    // Coordinator sees it already scored with the max_points of 10
    await page.goto(`/runs/${runId}/submissions`);
    await expect(page.getByRole('heading', { name: 'Einreichungen' })).toBeVisible();
    await expect(page.getByText(/\b10\s*\/\s*10\b/)).toBeVisible();

    // Override by hand: re-open the automatic score, then score manually
    // with different points. The manual score replaces the automatic one.
    await reopenFirstScoredSubmission(page);
    await expect(page.getByText(/\b10\s*\/\s*10\b/)).not.toBeVisible();
    await scoreFirstSubmission(page, '3');
    await expect(page.getByText(/\b3\s*\/\s*10\b/)).toBeVisible();

    // A reload re-scores nothing, so the manual score holds.
    await page.reload();
    await expect(page.getByText(/\b3\s*\/\s*10\b/)).toBeVisible();
    await expect(page.getByText(/\b10\s*\/\s*10\b/)).not.toBeVisible();
  });
});
