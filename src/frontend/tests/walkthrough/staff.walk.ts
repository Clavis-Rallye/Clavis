import { test, type Locator, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { capture, kebabStep, loginAs, note, setCaptureContext, watchConsole, walkLang } from './capture';
import { seedPath, type SeedData } from './seed';

/**
 * Staff walk: org_admin, creator and coordinator walk every staff screen in a
 * fixed order, one capture per step.
 *
 * It is a review tool: a step that cannot find its element notes the reason in
 * its console.json and the walk goes on. Only a broken login or seed fails it.
 *
 * WALK_LANG picks the labels: German from tests/e2e/helpers.ts and the UI,
 * English from src/i18n/locales/en/.
 */

const lang = walkLang();

/** UI labels the walk clicks/types, per language. */
const L = {
  createUser: 'Person anlegen',
  invitePerson: 'Person einladen',
  settingsTabs: ['Allgemein', 'Rechtliches', 'Löschfristen', 'E-Mail'],
  stationSearch: 'Stationen durchsuchen',
  challengeSearch: 'Aufgaben durchsuchen',
  tableSearchStations: 'Stationen durchsuchen',
  typeFilter: 'Typ',
  quiz: 'Quiz',
  showChallenges: 'Aufgaben anzeigen',
  addStation: 'Station hinzufügen',
  useExistingStation: 'Vorhandene Station verwenden',
  addChallenge: 'Aufgabe hinzufügen',
  useExistingChallenge: 'Vorhandene Aufgabe verwenden',
  challengeTypes: ['Foto', 'Text', 'QR-Code', 'Quiz', 'Audio', 'Video'],
  toSettingsStep: 'Weiter',
  toReviewStep: 'Weiter',
  nameField: 'Name',
  joinCode: 'Beitrittscode',
  showLargeCode: 'Beitrittscode groß anzeigen',
  closeLargeCode: 'Schließen',
} as const;

const L_EN = {
  createUser: 'Add person',
  invitePerson: 'Invite person',
  settingsTabs: ['General', 'Legal', 'Retention', 'E-Mail'],
  stationSearch: 'Search stations',
  challengeSearch: 'Search challenges',
  tableSearchStations: 'Search stations',
  typeFilter: 'Type',
  quiz: 'Quiz',
  showChallenges: 'Show challenges',
  addStation: 'Add station',
  useExistingStation: 'Use existing station',
  // RallyeDetail uses its own key, capitalised "Challenge" in EN.
  addChallenge: 'Add Challenge',
  useExistingChallenge: 'Use existing challenge',
  challengeTypes: ['Photo', 'Text', 'QR code', 'Quiz', 'Audio', 'Video'],
  toSettingsStep: 'Continue',
  toReviewStep: 'Continue',
  nameField: 'Name',
  joinCode: 'Join code',
  showLargeCode: 'Show join code large',
  closeLargeCode: 'Close',
} as const;

const labels = lang === 'en' ? L_EN : L;

/** Seed title matches both languages (EN carries an " (EN)" suffix). */
const RALLYE_TITLE_PATTERN = /Walkthrough Rallye/;

async function loadSeed(): Promise<SeedData> {
  try {
    return JSON.parse(await readFile(seedPath(), 'utf8')) as SeedData;
  } catch (err) {
    throw new Error(
      `staff.walk: cannot read seed ${seedPath()}: ${err instanceof Error ? err.message : err}`,
      { cause: err },
    );
  }
}

/** Run one step: a failed interaction becomes a note, the capture always happens. */
async function softStep(page: Page, rawStep: string, body: () => Promise<void>): Promise<void> {
  const step = kebabStep(rawStep);
  try {
    await body();
  } catch (err) {
    note(`${step}: ${err instanceof Error ? err.message.split('\n')[0] : String(err)}`);
  }
  await capture(page, step);
}

async function gotoStep(page: Page, url: string, step: string): Promise<void> {
  await softStep(page, step, async () => {
    await page.goto(url);
  });
}

/**
 * Fill a search box and wait past the 300 ms debounce of the DataTable and both
 * import dialogs. Otherwise capture()'s networkidle wait can resolve before the
 * request starts, and the capture shows the previous query's rows.
 */
async function fillSearch(field: Locator, term: string): Promise<void> {
  await field.fill(term);
  await field.page().waitForTimeout(600);
}

/** Click through a dialog: open, capture, close via Escape (label-free). */
async function dialogStep(
  page: Page,
  step: string,
  open: () => Promise<void>,
  interact?: () => Promise<void>,
): Promise<void> {
  await softStep(page, step, async () => {
    await open();
    await interact?.();
  });
  await page.keyboard.press('Escape');
}

test.describe('staff walks', () => {
  // A missing element must cost seconds, not the whole 120 s test budget,
  // because a soft step notes a missing element only after its action times out.
  test.use({ actionTimeout: 5_000 });

  let seedData: SeedData;
  test.beforeAll(async () => {
    seedData = await loadSeed();
  });

  test('org_admin @staff', async ({ page }, testInfo) => {
    setCaptureContext({ project: testInfo.project.name, lang, persona: 'org_admin' });
    watchConsole(page);
    await loginAs(page, seedData.accounts.org_admin);

    await gotoStep(page, '/dashboard', 'dashboard');

    await gotoStep(page, '/admin/users', 'users');
    await dialogStep(page, 'users-create-dialog', () =>
      page.getByRole('button', { name: labels.createUser, exact: true }).click(),
    );
    await dialogStep(page, 'users-invite-dialog', () =>
      page.getByRole('button', { name: labels.invitePerson, exact: true }).click(),
    );

    await gotoStep(page, '/admin/logs', 'admin-logs');

    await gotoStep(page, '/admin/settings', 'settings-general');
    for (const [index, tab] of labels.settingsTabs.entries()) {
      if (index === 0) continue; // captured as settings-general above
      const tabName = tab;
      await softStep(page, `settings-${['general', 'legal', 'retention', 'mail'][index]}`, () =>
        page.getByRole('tab', { name: tabName, exact: true }).click(),
      );
    }

    await gotoStep(page, '/profile', 'profile');
    await gotoStep(page, '/rallyes', 'rallyes');
    await gotoStep(page, '/stations', 'stations');
    // Station library search, a hit and a miss. The backend matches only titles
    // and addresses, so the hit uses a seeded title and not "Station".
    await softStep(page, 'stations-search-hit', () =>
      fillSearch(page.getByPlaceholder(labels.tableSearchStations), lang === 'en' ? 'City Hall' : 'Rathaus'),
    );
    await softStep(page, 'stations-search-no-match', () =>
      fillSearch(page.getByPlaceholder(labels.tableSearchStations), 'zzz'),
    );

    await gotoStep(page, '/challenges', 'challenges');
    await softStep(page, 'challenges-filter-quiz', async () => {
      await page.getByRole('combobox', { name: labels.typeFilter }).click();
      await page.getByRole('option', { name: labels.quiz, exact: true }).click();
    });
  });

  test('creator @staff', async ({ page }, testInfo) => {
    setCaptureContext({ project: testInfo.project.name, lang, persona: 'creator' });
    watchConsole(page);
    await loginAs(page, seedData.accounts.creator);

    await gotoStep(page, '/dashboard', 'dashboard');
    await gotoStep(page, '/rallyes', 'rallyes');
    await gotoStep(page, '/rallyes/new', 'rallye-new');

    const rallyeUrl = `/rallyes/${seedData.rallyeId}`;
    await gotoStep(page, rallyeUrl, 'rallye-detail');
    await softStep(page, 'rallye-station-expanded', () =>
      page.getByRole('button', { name: labels.showChallenges }).first().click(),
    );

    await dialogStep(
      page,
      'station-import-dialog',
      async () => {
        await page.getByRole('button', { name: labels.addStation, exact: true }).click();
        await page.getByRole('button', { name: new RegExp(labels.useExistingStation) }).click();
      },
      async () => {
        await fillSearch(page.getByLabel(labels.stationSearch), 'Walk');
      },
    );

    await dialogStep(
      page,
      'challenge-import-dialog',
      async () => {
        await page.getByRole('button', { name: labels.addChallenge, exact: true }).first().click();
        await page.getByRole('button', { name: new RegExp(labels.useExistingChallenge) }).click();
      },
      async () => {
        await fillSearch(page.getByLabel(labels.challengeSearch), 'Walk');
      },
    );

    await gotoStep(page, `/stations/${seedData.stationIds[0]}`, 'station-detail');
    await gotoStep(page, '/stations/new', 'station-new');
    await gotoStep(page, `/challenges/${seedData.challengeIds.quiz}`, 'challenge-detail-quiz');

    // One capture per type toggle on the shared create form. A type without a
    // toggle is recorded as a finding, not a broken step. The button row wraps
    // once later types are reached, so scroll to the viewport centre and keep
    // the sticky header from covering the click.
    for (const [index, typeLabel] of labels.challengeTypes.entries()) {
      const type = ['photo', 'text', 'qr_scan', 'quiz', 'audio', 'video'][index];
      // The domain type carries an underscore (qr_scan); kebabStep is the one
      // place that turns a step name into the folder name.
      const step = kebabStep(`challenge-new-${type}`);
      await softStep(page, step, async () => {
        if (index === 0) await page.goto('/challenges/new');
        const toggle = page.getByRole('button', { name: typeLabel, exact: true }).first();
        const present = await toggle
          .waitFor({ state: 'visible', timeout: 5_000 })
          .then(() => true)
          .catch(() => false);
        if (!present) {
          note(`${step}: no type toggle "${typeLabel}" on /challenges/new`);
          return;
        }
        await toggle.evaluate((el) => el.scrollIntoView({ block: 'center', inline: 'nearest' }));
        await toggle.click();
      });
    }

    await gotoStep(page, '/challenges', 'challenges');
  });

  test('coordinator @staff', async ({ page }, testInfo) => {
    setCaptureContext({ project: testInfo.project.name, lang, persona: 'coordinator' });
    watchConsole(page);
    await loginAs(page, seedData.accounts.coordinator);

    await gotoStep(page, '/dashboard', 'dashboard');
    await gotoStep(page, '/runs', 'runs');

    // Walk the three wizard steps without creating a run.
    await gotoStep(page, '/runs/new', 'run-new-step1');
    await softStep(page, 'run-new-step2', async () => {
      await page.getByRole('heading', { name: RALLYE_TITLE_PATTERN }).click();
      await page.locator('main').getByRole('button', { name: labels.toSettingsStep, exact: true }).click();
    });
    await softStep(page, 'run-new-step3', async () => {
      await page.getByRole('textbox', { name: labels.nameField, exact: true }).fill('Walkthrough Wizard');
      await page.locator('main').getByRole('button', { name: labels.toReviewStep, exact: true }).click();
    });

    await gotoStep(page, `/runs/${seedData.runId}`, 'run-dashboard');
    // The join panel of a fresh run starts expanded. RunDashboard collapses it
    // for a run 15 min or older, such as one of a reused WALK_SEED. A collapsed
    // header shows the join code beside the heading, and only then does the step
    // click the toggle, so the capture always shows the panel open.
    await softStep(page, 'run-dashboard-join', async () => {
      const collapsedCode = page
        .getByRole('heading', { name: labels.joinCode, exact: true })
        .locator('xpath=following-sibling::*[1]')
        .getByText(seedData.joinCode, { exact: true });
      if ((await collapsedCode.count()) > 0) {
        await page.getByRole('button', { name: labels.joinCode, exact: true }).click();
      }
    });
    // The large join code for a projector. Closed again after the capture, so the
    // next step starts from the plain page.
    await softStep(page, 'run-dashboard-large-code', async () => {
      await page.getByRole('button', { name: labels.showLargeCode, exact: true }).click();
      await page.getByRole('dialog').getByRole('button', { name: labels.closeLargeCode, exact: true }).waitFor();
    });
    await page.keyboard.press('Escape');

    await gotoStep(page, `/runs/${seedData.runId}/submissions`, 'run-submissions');
    await gotoStep(page, `/runs/${seedData.runId}/scoreboard`, 'run-scoreboard');
    await gotoStep(page, '/analytics', 'analytics');
  });
});
