import { BrowserContext, Locator, Page, expect } from '@playwright/test';

/** Unique-per-run identifiers so reruns don't collide on the shared dev DB. */
export function uniqueOrg(prefix = 'e2e') {
  const ts = Date.now().toString(36);
  return {
    name: `E2E ${prefix} ${ts}`,
    slug: `e2e-${prefix}-${ts}`,
    email: `e2e-${prefix}-${ts}@example.com`,
    displayName: `E2E ${prefix} ${ts}`,
    password: 'E2ePass1234!',
  };
}

/** Register a fresh org-admin and land on /dashboard. */
export async function registerNewOrg(page: Page, prefix?: string) {
  const creds = uniqueOrg(prefix);
  // The operator's org-bootstrap form has its own route. `/register` is the
  // ProfileSetup step.
  await page.goto('/register/organization');
  // MUI matches labels partially ("Name…" matches both "Name der Organisation"
  // and "Kurzname der Organisation"), so target by role with an exact name.
  await page.getByRole('textbox', { name: 'Name der Organisation', exact: true }).fill(creds.name);
  await page.getByRole('textbox', { name: 'Kurzname der Organisation' }).fill(creds.slug);
  await page.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(creds.email);
  await page.getByRole('textbox', { name: 'Anzeigename', exact: true }).fill(creds.displayName);
  // Two password fields, in document order. <input type=password> has no
  // implicit ARIA role, and MUI's required-field label gets a trailing " *".
  // So neither getByRole('textbox') nor getByLabel('Passwort', exact) reliably
  // hits them. Index by attribute selector instead.
  const passwords = page.locator('input[type="password"]');
  await passwords.nth(0).fill(creds.password);
  await passwords.nth(1).fill(creds.password);
  await page.getByRole('button', { name: 'Konto erstellen' }).click();
  // The heaviest transition of the suite: the backend creates an org and an
  // admin (argon2 hashing) and checks TOTP enforcement, then the SPA runs
  // checkAuth and redirects. Under concurrent CI load this can exceed the 15 s
  // expect window, so this one redirect gets 30 s. That helps a slow backend
  // and still fails a starved one.
  await expect(page).toHaveURL(/\/dashboard$/, { timeout: 30_000 });
  await expect(page.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeVisible();
  return creds;
}

/** Log in with previously-registered credentials. */
export async function login(page: Page, email: string, password: string) {
  await page.goto('/login');
  await page.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(email);
  await page.locator('input[type="password"]').first().fill(password);
  await page.getByRole('button', { name: 'Anmelden' }).click();
  // An org_admin is redirected to /2fa-setup on the first login after
  // registration, because TOTP enforcement is on. Either landing page proves
  // that auth worked.
  await expect(page).toHaveURL(/\/(dashboard|2fa-setup)$/);
}

/** Click a sidebar nav item by its visible label. */
export async function navTo(page: Page, label: string) {
  await page.getByRole('button', { name: label, exact: true }).click();
}

/** Create a rallye with a unique DE title. Returns its id from the URL. */
export async function createRallye(page: Page, title: string): Promise<string> {
  await page.goto('/rallyes/new');
  await page.getByRole('textbox', { name: /Titel.*Deutsch/i }).fill(title);
  await page.getByRole('button', { name: 'Rallye erstellen' }).click();
  await expect(page).toHaveURL(/\/rallyes\/[0-9a-f-]+$/);
  const url = page.url();
  return url.split('/').pop()!;
}

/**
 * Add a station to an existing rallye through the library-composition UX
 * (stations are org-level library entities, linked into rallyes):
 *   RallyeDetail → "Station hinzufügen" → AddChoiceDialog
 *   → "Neue Station erstellen" → /stations/new?rallyeId= → fill → save.
 * The flow returns to the rallye with the new station open. Returns the station id.
 */
export async function createStation(page: Page, rallyeId: string, title: string): Promise<string> {
  await page.goto(`/rallyes/${rallyeId}`);
  await page.getByRole('button', { name: 'Station hinzufügen' }).click();
  // AddChoiceDialog renders the two choices as CardActionAreas (role=button)
  // whose accessible name includes the description text. Match by substring.
  await page.getByRole('button', { name: /Neue Station erstellen/ }).click();
  await expect(page).toHaveURL(/\/stations\/new\?rallyeId=/);
  // The URL changes at once, but StationCreate is a lazy chunk. The rallye
  // detail page, which has its own "Titel (Deutsch)" field, can still be on
  // screen when the fill below runs. Wait for the new page's heading first.
  await expect(page.getByRole('heading', { name: 'Neue Station', level: 1 })).toBeVisible();
  await page.getByRole('textbox', { name: /Titel.*Deutsch/i }).fill(title);
  // The page does not land on the station, so the id comes from the response
  // of the create-and-link call.
  const created = page.waitForResponse(
    (r) => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/rallyes/${rallyeId}/stations`),
  );
  await page.getByRole('button', { name: 'Station erstellen' }).click();
  const link = (await (await created).json()) as { link_id: string; station: { id: string } };
  await expect(page).toHaveURL(new RegExp(`/rallyes/${rallyeId}\\?open=${link.link_id}$`));
  return link.station.id;
}

/**
 * Add a NEW challenge to the (first) station of a rallye through the
 * composition UX: RallyeDetail → expand station → "Aufgabe hinzufügen"
 * → AddChoiceDialog → "Neue Aufgabe erstellen"
 * → /challenges/new?rallyeId=&rallyeStationId= → fill → save.
 * The create-and-link flow navigates back to the rallye detail page.
 */
export async function addChallengeToRallye(
  page: Page,
  rallyeId: string,
  title: string,
  typeLabel: 'Text' | 'Quiz' | 'Foto' | 'QR-Code' = 'Text',
): Promise<void> {
  await page.goto(`/rallyes/${rallyeId}`);
  await page.getByRole('button', { name: 'Aufgaben anzeigen' }).first().click();
  await page.getByRole('button', { name: 'Aufgabe hinzufügen', exact: true }).first().click();
  await page.getByRole('button', { name: /Neue Aufgabe erstellen/ }).click();
  await expect(page).toHaveURL(/\/challenges\/new\?rallyeId=.+&rallyeStationId=.+/);
  // Same lazy-chunk race as in createStation: wait for the heading of
  // ChallengeCreate before touching a field that the source page also has.
  await expect(page.getByRole('heading', { name: 'Neue Aufgabe', level: 1 })).toBeVisible();
  await page.getByRole('button', { name: typeLabel, exact: true }).click();
  await page.getByRole('textbox', { name: /Titel.*Deutsch/i }).fill(title);
  await page.getByRole('textbox', { name: /Anleitung.*Deutsch/i }).fill('Test instructions.');
  await page
    .getByRole('textbox', { name: /Lösung \/ Referenzantwort/ })
    .fill('Referenz: 42.');
  await page.getByRole('button', { name: 'Aufgabe erstellen' }).click();
  // Create-and-link returns to the composition surface with ?open= the link_id
  // of the rallye station. The station stays expanded, so a caller must not
  // click "Aufgaben anzeigen" again: that would collapse it.
  await expect(page).toHaveURL(new RegExp(`/rallyes/${rallyeId}\\?open=`));
}

/**
 * Walk the RunCreate wizard for a published rallye and land on the new run's
 * dashboard. Returns the run id and its join code, read from the join link
 * in the collapsible join panel.
 */
export async function createRunForRallye(
  page: Page,
  rallyeTitle: string,
  runName: string,
  options: { maxTeams?: number } = {},
): Promise<{ runId: string; joinCode: string }> {
  await navTo(page, 'Läufe');
  await page.getByRole('button', { name: 'Lauf erstellen' }).click();
  await expect(page).toHaveURL(/\/runs\/new$/);
  // RunCreate is a lazy chunk. The URL changes at once, but the runs list can
  // still be on screen. Wait for the heading of the wizard first.
  await expect(page.getByRole('heading', { name: 'Neuer Lauf', level: 1 })).toBeVisible();
  await page.getByRole('heading', { name: rallyeTitle }).click();
  await page.locator('main').getByRole('button', { name: 'Weiter' }).click();
  await page.getByRole('textbox', { name: 'Name' }).fill(runName);
  if (options.maxTeams !== undefined) {
    // The team limit of the run: a number field, so a spinbutton.
    await page
      .getByRole('spinbutton', { name: 'Maximale Teamanzahl' })
      .fill(String(options.maxTeams));
  }
  await page.getByRole('button', { name: 'Weiter' }).click();
  await page.getByRole('button', { name: 'Lauf erstellen' }).click();
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/);
  const runId = page.url().split('/').pop()!;
  const joinCode = await readJoinCode(page);
  return { runId, joinCode };
}

/** Read the join code from the RunDashboard's join panel (via the join link). */
async function readJoinCode(page: Page): Promise<string> {
  const joinUrl = await page.getByRole('link', { name: /\/join\// }).innerText();
  return joinUrl.trim().split('/').pop()!;
}

/** Start the run from its dashboard and wait for the live state chip. */
export async function startRunFromDashboard(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Starten', exact: true }).click();
  await expect(page.getByText('Aktiv', { exact: true })).toBeVisible();
}

/**
 * Add a NEW quiz challenge to the (first) station of a rallye, configured for
 * auto-scoring (ScoringRulesEditor's quiz-only fields: options, "Automatisch
 * bewerten", "Richtige Antwort"), so a guest's answer is scored without
 * coordinator input. Follows the create-and-link flow of addChallengeToRallye.
 */
export async function addAutoScoredQuizChallengeToRallye(
  page: Page,
  rallyeId: string,
  title: string,
  options: string[],
  correctOption: string,
): Promise<void> {
  await page.goto(`/rallyes/${rallyeId}`);
  await page.getByRole('button', { name: 'Aufgaben anzeigen' }).first().click();
  await page.getByRole('button', { name: 'Aufgabe hinzufügen', exact: true }).first().click();
  await page.getByRole('button', { name: /Neue Aufgabe erstellen/ }).click();
  await expect(page).toHaveURL(/\/challenges\/new\?rallyeId=.+&rallyeStationId=.+/);
  // Same lazy-chunk wait as in addChallengeToRallye.
  await expect(page.getByRole('heading', { name: 'Neue Aufgabe', level: 1 })).toBeVisible();
  await page.getByRole('button', { name: 'Quiz', exact: true }).click();
  await page.getByRole('textbox', { name: /Titel.*Deutsch/i }).fill(title);
  await page.getByRole('textbox', { name: /Anleitung.*Deutsch/i }).fill('Test instructions.');
  for (let i = 0; i < options.length; i++) {
    await page.getByRole('button', { name: 'Option hinzufügen' }).click();
    await page.getByRole('textbox', { name: `Option ${i + 1}`, exact: true }).fill(options[i]);
  }
  await page.getByRole('checkbox', { name: 'Automatisch bewerten' }).check();
  await page.getByRole('combobox', { name: 'Richtige Antwort' }).click();
  const correctIdx = options.indexOf(correctOption);
  await page.getByRole('option', { name: `${correctIdx + 1}. ${correctOption}`, exact: true }).click();
  await page.getByRole('button', { name: 'Aufgabe erstellen' }).click();
  // Same create-and-link return as addChallengeToRallye.
  await expect(page).toHaveURL(new RegExp(`/rallyes/${rallyeId}\\?open=`));
}

/**
 * End the run from its dashboard (the page must be on `/runs/:runId`), confirm
 * the "Beenden" dialog and wait for the "Beendet" state chip.
 */
export async function endRunFromDashboard(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Beenden', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: 'Beenden', exact: true }).click();
  await expect(page.getByText('Beendet', { exact: true })).toBeVisible();
}

/** Create rallye + station + publish. Required for run creation. */
export async function createPublishedRallye(
  page: Page,
  prefix: string,
): Promise<{ rallyeId: string; stationId: string; title: string }> {
  const ts = Date.now().toString(36);
  const title = `${prefix}-Rallye-${ts}`;
  const rallyeId = await createRallye(page, title);
  const stationId = await createStation(page, rallyeId, `${prefix}-Stat-${ts}`);
  await page.goto(`/rallyes/${rallyeId}`);
  const respPromise = page.waitForResponse(
    (r) => r.url().includes(`/rallyes/${rallyeId}`) && r.request().method() === 'PUT',
  );
  // The switch only changes the form. "Speichern" sends the publication.
  await page.getByRole('switch').first().click();
  await page.getByRole('button', { name: 'Speichern', exact: true }).click();
  const resp = await respPromise;
  if (resp.status() !== 200) {
    throw new Error(`publish PUT failed: ${await resp.text()}`);
  }
  return { rallyeId, stationId, title };
}

/**
 * Set up a live run with one text challenge through the API alone. No
 * coordinator UI is touched, and the cookies land on `page`'s context. For
 * specs that run in every browser project and viewport, such as the a11y scans
 * as `webkit-iphone`, where the desktop coordinator shell cannot be driven.
 * onboarding-team-flow.spec.ts keeps a local twin, setupRunForTeamFlow.
 */
export async function setupActiveRunViaApi(
  page: Page,
  prefix: string,
): Promise<{ joinCode: string; runId: string; challengeTitle: string }> {
  const ts = Date.now().toString(36);
  const slug = `e2e-${prefix}-${ts}`;
  const email = `${slug}@example.com`;
  const password = 'E2ePass1234!';
  const ctx = page.context();

  // 1. Register org-admin via API
  const reg = await ctx.request.post('/api/auth/register', {
    data: {
      email,
      password,
      display_name: `E2E ${prefix} ${ts}`,
      organization_name: `E2E ${prefix} ${ts}`,
      organization_slug: slug,
    },
  });
  expect(reg.ok(), await reg.text()).toBeTruthy();

  // 2. Login (sets cookies on context)
  const login = await ctx.request.post('/api/auth/login', {
    data: { email, password },
  });
  expect(login.ok(), await login.text()).toBeTruthy();

  // 3. Rallye + station + challenge through the composition endpoints. The
  //    challenge endpoint takes the rallye-station LINK id, not the station id.
  const rallye = await ctx.request.post(`/api/orgs/${slug}/rallyes/`, {
    data: {
      title: { de: `${prefix} Rallye ${ts}` },
      description: { de: 'E2E test rallye' },
    },
  });
  expect(rallye.ok(), await rallye.text()).toBeTruthy();
  const rallyeId = (await rallye.json()).id;

  const station = await ctx.request.post(
    `/api/orgs/${slug}/rallyes/${rallyeId}/stations`,
    {
      data: {
        title: { de: `${prefix} Station ${ts}` },
        description: { de: 'First' },
      },
    },
  );
  expect(station.ok(), await station.text()).toBeTruthy();
  const rallyeStationId = (await station.json()).link_id;

  const challengeTitle = `${prefix} Challenge ${ts}`;
  const challenge = await ctx.request.post(
    `/api/orgs/${slug}/rallyes/${rallyeId}/stations/${rallyeStationId}/challenges`,
    {
      data: {
        challenge_type: 'text',
        title: { de: challengeTitle },
        instructions: { de: 'Do something' },
        scoring_rules: { max_points: 10 },
      },
    },
  );
  expect(challenge.ok(), await challenge.text()).toBeTruthy();

  // 4. Publish (a run needs a published rallye)
  const publish = await ctx.request.put(`/api/orgs/${slug}/rallyes/${rallyeId}`, {
    data: { is_published: true },
  });
  expect(publish.ok(), await publish.text()).toBeTruthy();

  // 5. Create + start the run
  const run = await ctx.request.post(`/api/orgs/${slug}/runs/`, {
    data: { rallye_id: rallyeId, name: `${prefix}-Run-${ts}` },
  });
  expect(run.ok(), await run.text()).toBeTruthy();
  const runJson = await run.json();

  const started = await ctx.request.post(`/api/orgs/${slug}/runs/${runJson.id}/start`);
  expect(started.ok(), await started.text()).toBeTruthy();

  return { joinCode: runJson.join_code, runId: runJson.id, challengeTitle };
}

/**
 * Open a fresh browser context (no coordinator cookies) and join a run as a
 * guest, walking the onboarding wizard:
 *   Welcome → auth-choice → ProfileSetup (guest toggle on) → FindTeam
 *   → (create a fresh team) → Invite → PickTeamName → run participant view.
 * Returns the guest page, parked on `/runs/:runId/participant/*`.
 */
export async function joinAsGuest(
  context: BrowserContext,
  joinCode: string,
  displayName: string,
  teamName: string,
): Promise<Page> {
  const guest = await context.newPage();
  await guest.goto(`/join/${joinCode}`);
  await guest.getByRole('button', { name: /weiter|continue/i }).click(); // Welcome → auth-choice
  await guest // auth-choice → profile
    .getByRole('button', { name: /als gast mitmachen|join as a guest/i })
    .click();
  await guest.getByLabel(/anzeigename|display name/i).fill(displayName);
  await guest.getByRole('button', { name: /weiter|continue/i }).click(); // profile → team/find
  await guest.getByRole('button', { name: /create.*team|team.*erstellen/i }).click();
  await guest.getByRole('button', { name: /weiter|continue/i }).click(); // team/find → team/invite
  // InviteTeamStep bootstraps the guest account and the team on mount
  // (POST /api/join/<code>), which sets the access_token cookie. The QR code
  // renders only after the bootstrap resolved with a share code, so waiting
  // for it is the reliable "bootstrap done" signal.
  await expect(guest).toHaveURL(/\/join\/.+\/team\/invite$/);
  await expect(guest.getByRole('img', { name: /QR/ })).toBeVisible();
  await expect(guest.getByRole('button', { name: /weiter|continue/i })).toBeEnabled();
  const cookies = await context.cookies();
  if (!cookies.find((c) => c.name === 'access_token')) {
    throw new Error(`access_token cookie not set after team bootstrap. Cookies: ${JSON.stringify(cookies)}`);
  }
  const me = await guest.request.get('/api/auth/me');
  if (me.status() !== 200) {
    throw new Error(`/auth/me failed ${me.status()}: ${await me.text()}. Cookies: ${JSON.stringify(cookies)}`);
  }
  await guest.getByRole('button', { name: /weiter|continue/i }).click(); // team/invite → team/name
  await expect(guest).toHaveURL(/\/join\/.+\/team\/name$/);
  await guest.getByLabel(/team.name|team-name/i).fill(teamName);
  await guest.getByRole('button', { name: /start.*rallye|rallye.*starten/i }).click();
  await expect(guest).toHaveURL(/\/runs\/[0-9a-f-]+\/participant/);
  return guest;
}

/**
 * A joined guest answers a text challenge of the first station: bottom nav
 * "Rallye" → first station → the challenge → text → "Antwort senden". The
 * window stays on the challenge.
 */
export async function submitTextAnswer(
  guest: Page,
  challengeTitle: string,
  text: string,
): Promise<void> {
  // BottomNavigationAction renders as role="button", not role="tab".
  await guest.getByRole('button', { name: 'Rallye', exact: true }).click();
  await expect(guest).toHaveURL(/\/participant\/stations$/);
  // StationList has a ToggleButtonGroup (list/map view) at the top, so the
  // station is found by its CardActionArea.
  await guest.locator('.MuiCardActionArea-root').first().click();
  await guest.getByText(challengeTitle).click();
  await guest.locator('textarea').first().fill(text);
  await guest.getByRole('button', { name: 'Antwort senden' }).click();
  await expect(guest.getByText(/gesendet|gespeichert/i).first()).toBeVisible();
}

/**
 * Grade the answer of one team on the open review page
 * (`/runs/:runId/submissions`). The row of an answer is the block under the
 * h3 that carries the team name. Waits for the response of `POST .../scores/`,
 * because the score chip is drawn from it.
 */
export async function scoreSubmissionOfTeam(
  page: Page,
  teamName: string,
  points: number,
): Promise<void> {
  const row = page
    .getByRole('heading', { name: teamName, exact: true, level: 3 })
    .locator('xpath=ancestor::div[2]');
  const scored = page.waitForResponse(
    (r) => r.request().method() === 'POST' && /\/scores\/?$/.test(new URL(r.url()).pathname),
  );
  await row.getByRole('spinbutton', { name: 'Punkte' }).fill(String(points));
  await row.getByRole('button', { name: 'Bewerten' }).click();
  expect((await scored).ok()).toBeTruthy();
  await expect(row.getByText(new RegExp(`\\b${points}\\s*/\\s*\\d+\\b`))).toBeVisible();
}

/**
 * Open the dialog "<team> verwalten" of a team row on the run page
 * (`/runs/:runId`) and return it.
 */
export async function openTeamMembersDialog(page: Page, teamName: string): Promise<Locator> {
  await page.getByRole('button', { name: `${teamName} verwalten`, exact: true }).click();
  const dialog = page.getByRole('dialog', { name: `${teamName} verwalten`, exact: true });
  await expect(dialog).toBeVisible();
  return dialog;
}

/**
 * The teams of a run as the server lists them now, read with the cookies of
 * `page` (the coordinator). For what a page cannot show: that nothing was
 * removed while a confirmation is still open.
 */
export async function listTeamsViaApi(
  page: Page,
  orgSlug: string,
  runId: string,
): Promise<{ id: string; name: string; member_count: number }[]> {
  const resp = await page.request.get(`/api/orgs/${orgSlug}/runs/${runId}/teams/`);
  expect(resp.ok(), await resp.text()).toBeTruthy();
  return ((await resp.json()) as { items: { id: string; name: string; member_count: number }[] })
    .items;
}
