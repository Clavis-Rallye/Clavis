import { test, expect, type Locator, type Page } from '@playwright/test';
import {
  registerNewOrg,
  createPublishedRallye,
  addChallengeToRallye,
  createRunForRallye,
  startRunFromDashboard,
  joinAsGuest,
  submitTextAnswer,
  scoreSubmissionOfTeam,
  openTeamMembersDialog,
  listTeamsViaApi,
} from './helpers';

/**
 * A team dissolves cleanly, walked in the browser.
 *
 * The texts below are the German values of the locale keys that the run page
 * and the participant app render, copied from src/i18n/locales/de:
 * runs.json `teamMembers.*` and `dissolveRefused.*`, onboarding.json
 * `noRuns.removed`.
 */

/** `teamMembers.removeConfirm`, the plain question for a guest. */
const removeGuestQuestion = (name: string) =>
  `${name} wirklich aus dem Team entfernen? Im selben Browser kann dieser Gast dem Lauf nicht erneut beitreten. Ein anderes Gerät oder ein privates Fenster gilt als neuer Gast.`;

/** `teamMembers.removeTeam.confirm`, the plain question of "Team entfernen". */
const removeTeamQuestion = (teamName: string) => `Team ${teamName} wirklich entfernen?`;

/** `dissolveRefused.staff`, built from the numbers of the server's refusal. */
const dissolveSentence = (teamName: string, members: number, answers: number) =>
  `Mit diesem Schritt wird das Team ${teamName} aufgelöst. Mitglieder: ${members}, Antworten: ${answers}. Das Team verlässt die Rangliste.`;

/** `teamMembers.removeTeam.guests`. */
const GUESTS_BLOCKED =
  'Die Gäste des Teams können dem Lauf im selben Browser nicht erneut beitreten.';

/** `dissolveRefused.staffRunEnded`: not said while the run is active. */
const RUN_ENDED_SENTENCE = 'Der Lauf ist beendet. Die Endrangliste ändert sich.';

/** `noRuns.removed`, the notice for a removed member. */
const REMOVED_NOTICE =
  'Du bist in deinem letzten Lauf in keinem Team mehr. Die Spielleitung hat dich vielleicht aus dem Team entfernt. Wenn das ein Irrtum ist, sprich sie an.';

/** A call that the server refused because it would dissolve a team. */
const isDissolveRefusal = (r: import('@playwright/test').Response) =>
  r.request().method() === 'DELETE' && r.status() === 409 && /\/teams\//.test(r.url());

/** The button that opens the dialog of a team row. Behind an open dialog it is aria-hidden. */
const teamRow = (page: Page, teamName: string, includeHidden = false) =>
  page.getByRole('button', { name: `${teamName} verwalten`, exact: true, includeHidden });

/** The heading of the team card, which carries the count of teams. */
const teamCount = (page: Page, count: number, includeHidden = false) =>
  page.getByRole('heading', { name: `Teams (${count})`, exact: true, includeHidden });

/** The row of a team in the scoreboard of the coordinator. */
const scoreboardRow = (page: Page, teamName: string) =>
  page.getByRole('row').filter({ has: page.getByRole('cell', { name: teamName, exact: true }) });

/** The card of a team in the leaderboard of the participant app. */
const leaderboardCard = (guest: Page, teamName: string): Locator =>
  guest.locator('.MuiCard-root').filter({ hasText: teamName });

/** The open app was led out and shows the notice for a removed member. */
async function expectRemovedNotice(guest: Page) {
  await expect(guest).toHaveURL(/\/no-runs\?reason=removed$/);
  await expect(guest.getByText(REMOVED_NOTICE)).toBeVisible();
}

test.describe('team dissolution', () => {
  // One journey with five browser contexts: four guest joins through the
  // wizard, three answers, three gradings and two dissolutions.
  test.describe.configure({ timeout: 240_000 });

  test('three teams: the middle one dissolves with its last member, its name is free again, and a whole team is removed', async ({
    browser,
    page,
  }) => {
    const ts = Date.now().toString(36);
    const alpha = `Alpha ${ts}`;
    const mitte = `Mitte ${ts}`;
    const omega = `Omega ${ts}`;
    const mitteGuestName = `GastM-${ts}`;

    // --- Setup: an active run with one text challenge and a limit of three teams ---
    const creds = await registerNewOrg(page, 'diss');
    const { rallyeId, title: rallyeTitle } = await createPublishedRallye(page, 'ds');
    const challengeTitle = `ds-Ch-${ts}`;
    await addChallengeToRallye(page, rallyeId, challengeTitle, 'Text');
    const { runId, joinCode } = await createRunForRallye(page, rallyeTitle, `ds-Run-${ts}`, {
      maxTeams: 3,
    });
    await startRunFromDashboard(page);

    // Three guests in three browser contexts form three teams, one answer each.
    const alphaCtx = await browser.newContext();
    const mitteCtx = await browser.newContext();
    const omegaCtx = await browser.newContext();
    const alphaGuest = await joinAsGuest(alphaCtx, joinCode, `GastA-${ts}`, alpha);
    const mitteGuest = await joinAsGuest(mitteCtx, joinCode, mitteGuestName, mitte);
    const omegaGuest = await joinAsGuest(omegaCtx, joinCode, `GastO-${ts}`, omega);
    await submitTextAnswer(alphaGuest, challengeTitle, 'Antwort Alpha.');
    await submitTextAnswer(mitteGuest, challengeTitle, 'Antwort Mitte.');
    await submitTextAnswer(omegaGuest, challengeTitle, 'Antwort Omega.');

    // The coordinator grades them to different points: ranks 1, 2 and 3.
    await page.goto(`/runs/${runId}/submissions`);
    await expect(page.getByRole('heading', { name: 'Einreichungen' })).toBeVisible();
    await scoreSubmissionOfTeam(page, alpha, 9);
    await scoreSubmissionOfTeam(page, mitte, 6);
    await scoreSubmissionOfTeam(page, omega, 3);

    // The scoreboard of the coordinator in a second window, open from now on.
    const scoreboard = await page.context().newPage();
    await scoreboard.goto(`/runs/${runId}/scoreboard`);
    await expect(scoreboard.locator('tbody tr')).toHaveCount(3);
    await expect(scoreboardRow(scoreboard, alpha).getByRole('cell').first()).toHaveText('1');
    await expect(scoreboardRow(scoreboard, mitte).getByRole('cell').first()).toHaveText('2');
    await expect(scoreboardRow(scoreboard, omega).getByRole('cell').first()).toHaveText('3');

    // The leaderboard in the window of another team, open from now on.
    await alphaGuest.getByRole('button', { name: 'Team', exact: true }).click();
    await expect(alphaGuest).toHaveURL(/\/participant\/team\/leaderboard$/);
    await expect(leaderboardCard(alphaGuest, alpha).getByRole('img', { name: 'Platz 1', exact: true })).toBeVisible();
    await expect(leaderboardCard(alphaGuest, mitte).getByRole('img', { name: 'Platz 2', exact: true })).toBeVisible();
    await expect(leaderboardCard(alphaGuest, omega).getByRole('img', { name: 'Platz 3', exact: true })).toBeVisible();

    // The run page, open from now on and never loaded again.
    await page.goto(`/runs/${runId}`);
    await expect(teamCount(page, 3)).toBeVisible();
    await expect(teamRow(page, mitte)).toBeVisible();

    // --- Step 1: the coordinator removes the only member of the middle team ---
    let dialog = await openTeamMembersDialog(page, mitte);
    await dialog.getByRole('button', { name: `${mitteGuestName} aus dem Team entfernen` }).click();
    // First confirmation: the plain question.
    await expect(dialog.getByText(removeGuestQuestion(mitteGuestName))).toBeVisible();
    let refused = page.waitForResponse(isDissolveRefusal);
    await dialog.getByRole('button', { name: 'Entfernen', exact: true }).click();
    await refused;
    // Second confirmation: the team is dissolved, with the numbers of the
    // server. One member and one answer are what this test created.
    await expect(dialog.getByText(dissolveSentence(mitte, 1, 1))).toBeVisible();
    await expect(dialog.getByText(RUN_ENDED_SENTENCE)).toHaveCount(0);
    // Nothing is removed before the second confirmation.
    let teams = await listTeamsViaApi(page, creds.slug, runId);
    expect(teams.map((t) => t.name).sort()).toEqual([alpha, mitte, omega]);
    expect(teams.find((t) => t.name === mitte)?.member_count).toBe(1);
    await expect(teamRow(page, mitte, true)).toHaveCount(1);
    await expect(teamCount(page, 3, true)).toHaveCount(1);
    await dialog.getByRole('button', { name: 'Entfernen', exact: true }).click();

    // The team is gone from the run page without a reload.
    await expect(dialog).toBeHidden();
    await expect(teamRow(page, mitte)).toHaveCount(0);
    await expect(teamCount(page, 2)).toBeVisible();
    await expect(teamRow(page, alpha)).toBeVisible();
    await expect(teamRow(page, omega)).toBeVisible();

    // The scoreboard shows two teams on ranks 1 and 2, without a reload.
    await expect(scoreboard.locator('tbody tr')).toHaveCount(2);
    await expect(scoreboard.getByRole('cell', { name: mitte, exact: true })).toHaveCount(0);
    await expect(scoreboardRow(scoreboard, alpha).getByRole('cell').first()).toHaveText('1');
    await expect(scoreboardRow(scoreboard, omega).getByRole('cell').first()).toHaveText('2');

    // The leaderboard in the window of another team shows the same, without a reload.
    await expect(alphaGuest.getByText(mitte)).toHaveCount(0);
    await expect(alphaGuest.getByRole('img', { name: /^Platz \d+$/ })).toHaveCount(2);
    await expect(leaderboardCard(alphaGuest, alpha).getByRole('img', { name: 'Platz 1', exact: true })).toBeVisible();
    await expect(leaderboardCard(alphaGuest, omega).getByRole('img', { name: 'Platz 2', exact: true })).toBeVisible();

    // --- Step 2: the window of the removed guest shows the notice for a removed member ---
    await expectRemovedNotice(mitteGuest);

    // --- Step 3: a new guest creates a team with the name of the dissolved one ---
    // The run allows three teams, so the join also proves that the slot is free again.
    const newCtx = await browser.newContext();
    await joinAsGuest(newCtx, joinCode, `GastN-${ts}`, mitte);
    await expect(teamCount(page, 3)).toBeVisible();
    await expect(teamRow(page, mitte)).toBeVisible();

    // --- Step 5, prepared: a member of the team that step 4 removes has the app open on the team chat ---
    await omegaGuest.getByRole('button', { name: 'Team', exact: true }).click();
    await omegaGuest.getByRole('tab', { name: 'Chat', exact: true }).click();
    await expect(omegaGuest).toHaveURL(/\/participant\/team\/chat$/);
    await expect(omegaGuest.getByRole('textbox').first()).toBeVisible();

    // --- Step 4: the coordinator removes a whole team with "Team entfernen" ---
    dialog = await openTeamMembersDialog(page, omega);
    await dialog.getByRole('button', { name: 'Team entfernen', exact: true }).click();
    // First confirmation: the plain question.
    await expect(dialog.getByText(removeTeamQuestion(omega), { exact: true })).toBeVisible();
    refused = page.waitForResponse(isDissolveRefusal);
    await dialog.getByRole('button', { name: 'Team entfernen', exact: true }).click();
    await refused;
    // Second confirmation, with the numbers of the server.
    await expect(dialog.getByText(dissolveSentence(omega, 1, 1))).toBeVisible();
    await expect(dialog.getByText(GUESTS_BLOCKED)).toBeVisible();
    await expect(dialog.getByText(RUN_ENDED_SENTENCE)).toHaveCount(0);
    // Nothing is removed before the second confirmation.
    teams = await listTeamsViaApi(page, creds.slug, runId);
    expect(teams.map((t) => t.name).sort()).toEqual([alpha, mitte, omega]);
    expect(teams.find((t) => t.name === omega)?.member_count).toBe(1);
    await expect(teamRow(page, omega, true)).toHaveCount(1);
    await expect(teamCount(page, 3, true)).toHaveCount(1);
    await dialog.getByRole('button', { name: 'Team entfernen', exact: true }).click();

    // The team is gone from the run page without a reload. Three teams stood
    // there after step 3, so two are left.
    await expect(dialog).toBeHidden();
    await expect(teamRow(page, omega)).toHaveCount(0);
    await expect(teamCount(page, 2)).toBeVisible();
    await expect(teamRow(page, alpha)).toBeVisible();
    await expect(teamRow(page, mitte)).toBeVisible();

    // --- Step 5: the open app of the member follows at once, without a reload ---
    // On `team_dissolved` of the run channel the shell asks the server and,
    // as the membership is gone, leads out to the notice for a removed member.
    await expectRemovedNotice(omegaGuest);

    await Promise.all([alphaCtx, mitteCtx, omegaCtx, newCtx].map((ctx) => ctx.close()));
  });
});
