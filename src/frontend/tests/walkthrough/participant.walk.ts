import { request, test, type Locator, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { deflateSync } from 'node:zlib';
import { capture, kebabStep, loginAs, note, setCaptureContext, walkLang, watchConsole } from './capture';
import { recordTeams, seedPath, type SeedData, type SeedTeam } from './seed';

/**
 * Participant walks, on the helpers of capture.ts and seed.ts.
 *
 * The guest walks the whole funnel in a fresh browser context: join code,
 * welcome, entry choice, profile, team creation, then the run with all six
 * challenge forms, the feed, the team tab, the overflow menu, run history,
 * feedback and the three legal pages. The registered participant logs in and
 * joins the guest's team by its share code.
 *
 * It is a review tool, not a test suite: a step that cannot find its element
 * notes the reason in its console.json and the walk goes on. The walk fails
 * only on a broken seed, a broken join code or a run that is not ACTIVE.
 *
 * Run from src/frontend:
 *   E2E_BASE_URL=http://localhost WALK_LANG=de \
 *   WALK_SEED=tests/walkthrough/out/seed.json \
 *   npx playwright test -c playwright.walkthrough.config.ts --project=phone --grep @participant
 */

const lang = walkLang();

/**
 * Every label the walk clicks or fills, as one regex per control matching the
 * German and the English string. The same walk then runs under WALK_LANG=de
 * and WALK_LANG=en against one seed. Sources: src/i18n/locales/{de,en}/ and
 * `joinAsGuest` in tests/e2e/helpers.ts.
 */
const RX = {
  continue: /^(weiter|continue)$/i,
  // The guest way is its own button on the entry step.
  guestEntry: /als gast mitmachen|join as a guest/i,
  displayName: /anzeigename|display name/i,
  languageField: /^(sprache|language)$/i,
  guestToggle: /als gast fortfahren|continue as guest/i,
  createTeam: /neues team erstellen|create a new team/i,
  joinTeam: /bestehendem team beitreten|join an existing team/i,
  teamNameField: /team.?name/i,
  startRallye: /rallye starten|start rallye/i,
  teamCodeField: /team.?code/i,
  joinTeamSubmit: /team beitreten|join team/i,
  submitAnswer: /antwort senden|submit answer/i,
  answerSettled: /antwort ändern|change answer|bereits beantwortet|already submitted/i,
  qrManual: /manuell eingeben|enter the code manually/i,
  chatMessage: /nachricht eingeben|enter message/i,
  chatSend: /^(senden|send)$/i,
  overflowMenu: /^(mehr|more)$/i,
  overflowProfile: /^(profil|profile)$/i,
} as const;

const GUEST_NAME = 'Walk Gast';
const GUEST_TEAM = 'Walkthrough Team';
const SECOND_TEAM = 'Walkthrough Team 2';
/** The seeded qr_scan challenge auto-approves exactly this value. */
const QR_EXPECTED = 'CLAVIS-DOM';

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost';

// ---------------------------------------------------------------------------
// A 64x64 PNG built in memory. The photo form needs a real image file, and
// building it here keeps the harness free of a binary fixture.
// ---------------------------------------------------------------------------

function crc32(buf: Buffer): number {
  let crc = ~0;
  for (const byte of buf) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return ~crc >>> 0;
}

function pngChunk(type: string, data: Buffer): Buffer {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, 'latin1'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}

/** A 64x64 truecolour PNG with a visible gradient, so a reviewer sees content. */
function walkPng(size = 64): Buffer {
  const stride = size * 3 + 1; // one filter byte per scanline
  const raw = Buffer.alloc(size * stride);
  for (let y = 0; y < size; y += 1) {
    const row = y * stride;
    raw[row] = 0; // filter type "none"
    for (let x = 0; x < size; x += 1) {
      const pixel = row + 1 + x * 3;
      raw[pixel] = (x * 4) & 0xff;
      raw[pixel + 1] = (y * 4) & 0xff;
      raw[pixel + 2] = 0x80;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // colour type: truecolour
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(raw)),
    pngChunk('IEND', Buffer.alloc(0)),
  ]);
}

const PNG_UPLOAD = {
  name: 'walk-photo.png',
  mimeType: 'image/png',
  buffer: walkPng(),
};

// ---------------------------------------------------------------------------
// Step helpers — the same soft contract the staff walk uses.
// ---------------------------------------------------------------------------

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

/** Wait for a locator without failing the walk. */
async function appears(locator: Locator, timeout = 5_000): Promise<boolean> {
  return locator
    .first()
    .waitFor({ state: 'visible', timeout })
    .then(() => true)
    .catch(() => false);
}

async function loadSeed(): Promise<SeedData> {
  try {
    return JSON.parse(await readFile(seedPath(), 'utf8')) as SeedData;
  } catch (err) {
    throw new Error(
      `participant.walk: cannot read seed ${seedPath()}: ${err instanceof Error ? err.message : err}`,
      { cause: err },
    );
  }
}

/**
 * The run must be ACTIVE. Otherwise every participant screen below it is empty
 * or blocked, and a walk of blocked screens is no evidence. Checked as the
 * coordinator, the one role that may read the lifecycle of the run.
 */
async function assertRunActive(seedData: SeedData): Promise<void> {
  const ctx = await request.newContext({ baseURL: BASE_URL });
  try {
    const login = await ctx.post('/api/auth/login', {
      data: {
        email: seedData.accounts.coordinator.email,
        password: seedData.accounts.coordinator.password,
      },
    });
    if (!login.ok()) {
      throw new Error(`participant.walk: coordinator login -> ${login.status()}: ${await login.text()}`);
    }
    const resp = await ctx.get(`/api/orgs/${seedData.orgSlug}/runs/${seedData.runId}`);
    if (!resp.ok()) {
      throw new Error(`participant.walk: GET run ${seedData.runId} -> ${resp.status()}: ${await resp.text()}`);
    }
    const run = (await resp.json()) as { state?: string };
    if (run.state !== 'active') {
      throw new Error(
        `participant.walk: run ${seedData.runId} is "${run.state}", not "active". ` +
          'The participant walks need a live run — start it on the coordinator dashboard, then walk again.',
      );
    }
  } finally {
    await ctx.dispose();
  }
}

interface RunChallengeRef {
  stationId: string;
  challengeId: string;
  type: string;
}

/** Every run challenge in route order, read through the page's own session. */
async function listRunChallenges(page: Page, seedData: SeedData): Promise<RunChallengeRef[]> {
  const stationsResp = await page.request.get(
    `/api/orgs/${seedData.orgSlug}/runs/${seedData.runId}/stations/`,
  );
  if (!stationsResp.ok()) {
    note(`run-challenges: GET stations -> ${stationsResp.status()}`);
    return [];
  }
  const stations = ((await stationsResp.json()) as { items: Array<{ id: string; sort_order: number }> }).items
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order);

  const refs: RunChallengeRef[] = [];
  for (const station of stations) {
    const resp = await page.request.get(
      `/api/orgs/${seedData.orgSlug}/runs/${seedData.runId}/stations/${station.id}/challenges/`,
    );
    if (!resp.ok()) {
      note(`run-challenges: GET challenges of station ${station.id} -> ${resp.status()}`);
      continue;
    }
    const items = ((await resp.json()) as {
      items: Array<{ id: string; sort_order: number; challenge_type: string }>;
    }).items
      .slice()
      .sort((a, b) => a.sort_order - b.sort_order);
    for (const item of items) {
      refs.push({ stationId: station.id, challengeId: item.id, type: item.challenge_type });
    }
  }
  return refs;
}

/** The caller's team in this run: id, name and share code. */
async function readMyTeam(page: Page, joinCode: string): Promise<SeedTeam | null> {
  const resp = await page.request.get(`/api/public/runs/${encodeURIComponent(joinCode)}/my-team`);
  if (!resp.ok()) {
    note(`my-team: GET -> ${resp.status()}`);
    return null;
  }
  const body = (await resp.json()) as {
    team_id: string | null;
    team_name: string | null;
    team_share_code: string | null;
  };
  if (!body.team_id) return null;
  return {
    id: body.team_id,
    name: body.team_name,
    shareCode: body.team_share_code,
    createdBy: 'guest',
  };
}

/**
 * Fill one challenge form and capture it empty and filled, and submitted for
 * text, quiz and qr_scan. Photo takes the generated PNG. Audio and video have
 * no generated fixture, so their step shows the form with its two file-picker
 * buttons and notes why nothing was attached.
 */
async function walkChallengeForm(
  page: Page,
  runId: string,
  ref: RunChallengeRef,
): Promise<void> {
  const url = `/runs/${runId}/participant/stations/${ref.stationId}/challenges/${ref.challengeId}`;
  const type = kebabStep(ref.type);

  await gotoStep(page, url, `challenge-${type}-empty`);

  await softStep(page, `challenge-${type}-filled`, async () => {
    switch (ref.type) {
      case 'text':
        await page.getByRole('textbox').first().fill('1521. Die Jahresrente ist bis heute ein Rheinischer Gulden.');
        break;
      case 'quiz':
        await page.getByRole('radio').first().check();
        break;
      case 'qr_scan':
        await page.getByLabel(RX.qrManual).fill(QR_EXPECTED);
        break;
      case 'photo':
        await page.locator('input[type="file"]').last().setInputFiles(PNG_UPLOAD);
        break;
      case 'audio':
      case 'video':
        // Both forms upload through a hidden file input behind two picker
        // buttons. Scroll the whole form into view and attach nothing.
        await page.getByRole('button', { name: RX.submitAnswer }).scrollIntoViewIfNeeded();
        note(`challenge-${type}-filled: no generated ${ref.type} fixture — the file pickers stay unfilled`);
        break;
      default:
        note(`challenge-${type}-filled: unknown challenge type "${ref.type}"`);
    }
  });

  if (ref.type !== 'text' && ref.type !== 'quiz' && ref.type !== 'qr_scan') return;

  await softStep(page, `challenge-${type}-submitted`, async () => {
    await page.getByRole('button', { name: RX.submitAnswer }).click();
    const settled = await appears(page.getByText(RX.answerSettled), 10_000);
    if (!settled) note(`challenge-${type}-submitted: no submitted state within 10 s`);
  });
}

test.describe('participant walks', () => {
  // A missing element must cost seconds, not the test budget.
  test.use({ actionTimeout: 5_000 });

  let seedData: SeedData;
  /** The guest's team, handed to the registered participant walk. */
  let guestTeam: SeedTeam | null = null;

  test.beforeAll(async () => {
    seedData = await loadSeed();
    if (!seedData.joinCode) {
      throw new Error(`participant.walk: seed ${seedPath()} carries no joinCode`);
    }
    await assertRunActive(seedData);
  });

  test('guest @participant', async ({ browser }, testInfo) => {
    // ~40 captures, each with a full-page screenshot and an axe run.
    test.setTimeout(900_000);
    setCaptureContext({ project: testInfo.project.name, lang, persona: 'guest' });

    // A fresh context: the guest journey starts without any cookie.
    const context = await browser.newContext();
    await context.addInitScript(
      (walk) => localStorage.setItem('clavis_language', walk),
      lang,
    );
    const page = await context.newPage();
    watchConsole(page);

    const code = seedData.joinCode;

    // --- onboarding ------------------------------------------------------
    await gotoStep(page, `/join/${code}`, 'welcome');
    await softStep(page, 'welcome-language-open', () =>
      page.getByRole('combobox', { name: RX.languageField }).click(),
    );
    await page.keyboard.press('Escape');

    await softStep(page, 'entry-choice', () =>
      page.getByRole('button', { name: RX.continue }).click(),
    );

    await softStep(page, 'profile-guest', async () => {
      await page.getByRole('button', { name: RX.guestEntry }).click();
      const toggle = page.getByRole('switch', { name: RX.guestToggle });
      if ((await toggle.getAttribute('aria-checked')) === 'false') await toggle.click();
      await page.getByLabel(RX.displayName).fill(GUEST_NAME);
    });

    await softStep(page, 'team-find', async () => {
      await page.getByRole('button', { name: RX.continue }).click();
      await page.getByRole('button', { name: RX.createTeam }).click();
    });

    await softStep(page, 'team-invite', async () => {
      await page.getByRole('button', { name: RX.continue }).click();
      // InviteTeamStep bootstraps the guest account and the team on mount. The
      // QR code renders only once that resolved with a share code.
      const qr = await appears(page.getByRole('img', { name: /QR/ }), 15_000);
      if (!qr) note('team-invite: no QR code within 15 s — team bootstrap may have failed');
    });

    guestTeam = await readMyTeam(page, code);
    if (guestTeam) await recordTeams([guestTeam]);

    await softStep(page, 'team-name', async () => {
      await page.getByRole('button', { name: RX.continue }).click();
      await page.getByLabel(RX.teamNameField).fill(GUEST_TEAM);
    });

    await softStep(page, 'stations', async () => {
      await page.getByRole('button', { name: RX.startRallye }).click();
      await page.waitForURL(/\/runs\/[0-9a-f-]+\/participant/, { timeout: 15_000 });
    });

    if (guestTeam) {
      guestTeam = { ...guestTeam, name: GUEST_TEAM };
      await recordTeams([guestTeam]);
    }

    const runId = new URL(page.url()).pathname.split('/')[2] ?? seedData.runId;

    // --- the run ---------------------------------------------------------
    // The toggle's aria-label is the literal "map" in both languages.
    await softStep(page, 'stations-map', async () => {
      await page.getByRole('button', { name: 'map', exact: true }).click();
      await page.waitForTimeout(1_500); // let the tiles paint before the capture
    });
    await gotoStep(page, `/runs/${runId}/participant/stations`, 'stations-list');

    const challenges = await listRunChallenges(page, seedData);
    if (challenges.length === 0) {
      note('challenges: the run carries no challenges — the forms below are skipped');
    }

    const firstStation = challenges[0]?.stationId;
    if (firstStation) {
      await gotoStep(
        page,
        `/runs/${runId}/participant/stations/${firstStation}`,
        'station-challenges',
      );
    }

    for (const ref of challenges) {
      await walkChallengeForm(page, runId, ref);
    }

    // --- feed, team tab, menus ------------------------------------------
    await gotoStep(page, `/runs/${runId}/participant/feed`, 'feed');
    await gotoStep(page, `/runs/${runId}/participant/team/leaderboard`, 'team-leaderboard');

    await gotoStep(page, `/runs/${runId}/participant/team/chat`, 'team-chat-empty');
    await softStep(page, 'team-chat', async () => {
      await page.getByLabel(RX.chatMessage).fill('Wir sind am Rathaus und fangen an.');
      await page.getByRole('button', { name: RX.chatSend }).click();
      await page.waitForTimeout(1_000);
    });

    await gotoStep(page, `/runs/${runId}/participant/team/info`, 'team-info');

    await softStep(page, 'overflow-menu', () =>
      page.getByRole('button', { name: RX.overflowMenu }).click(),
    );
    await softStep(page, 'profile-sheet', () =>
      page.getByRole('menuitem', { name: RX.overflowProfile }).click(),
    );
    await page.keyboard.press('Escape');

    // --- the pages outside the run shell --------------------------------
    await gotoStep(page, '/run-history', 'run-history');
    await gotoStep(page, `/runs/${runId}/participant/feedback`, 'feedback');
    await gotoStep(page, '/legal/impressum', 'legal-impressum');
    await gotoStep(page, '/legal/privacy', 'legal-privacy');
    await gotoStep(page, '/legal/accessibility', 'legal-accessibility');

    await context.close();
  });

  test('participant @participant', async ({ page }, testInfo) => {
    test.setTimeout(300_000);
    setCaptureContext({ project: testInfo.project.name, lang, persona: 'participant' });
    watchConsole(page);
    await loginAs(page, seedData.accounts.participant);

    const code = seedData.joinCode;
    // The guest walk runs first in the same invocation. A walk started with
    // --grep on this test alone reads the team out of the seed file instead.
    const target = guestTeam ?? (seedData.teams ?? []).find((team) => team.shareCode) ?? null;
    const shareCode = target?.shareCode ?? null;

    await gotoStep(page, '/dashboard', 'dashboard');

    // WelcomeGuard sends an authenticated user from /join/<code> through
    // ProfileSetupRedirect to /join/<code>/team/find.
    await gotoStep(page, `/join/${code}`, 'team-find');

    if (shareCode) {
      await softStep(page, 'team-join-code', async () => {
        await page.getByRole('button', { name: RX.joinTeam }).click();
        await page.getByRole('button', { name: RX.continue }).click();
        await page.getByLabel(RX.teamCodeField).fill(shareCode);
      });
      await softStep(page, 'team-joined', async () => {
        await page.getByRole('button', { name: RX.joinTeamSubmit }).click();
        await page.waitForURL(/\/(runs\/[0-9a-f-]+\/participant|join\/.+\/team\/(waiting|pending))/, {
          timeout: 15_000,
        });
        if (/\/team\/(waiting|pending)$/.test(page.url())) {
          note(`team-joined: stopped on ${page.url()} — the team has no name yet`);
        }
      });
    } else {
      // No guest team in reach: build a second team so the rest of the walk
      // still has a run to show.
      note('team-join-code: no guest team share code — creating a second team instead');
      await softStep(page, 'team-create-second', async () => {
        await page.getByRole('button', { name: RX.createTeam }).click();
        await page.getByRole('button', { name: RX.continue }).click();
        const qr = await appears(page.getByRole('img', { name: /QR/ }), 15_000);
        if (!qr) note('team-create-second: no QR code within 15 s');
      });
      const secondTeam = await readMyTeam(page, code);
      await softStep(page, 'team-name-second', async () => {
        await page.getByRole('button', { name: RX.continue }).click();
        await page.getByLabel(RX.teamNameField).fill(SECOND_TEAM);
        await page.getByRole('button', { name: RX.startRallye }).click();
        await page.waitForURL(/\/runs\/[0-9a-f-]+\/participant/, { timeout: 15_000 });
      });
      if (secondTeam) {
        await recordTeams([{ ...secondTeam, name: SECOND_TEAM, createdBy: 'participant' }]);
      }
    }

    const runId = /\/runs\/([0-9a-f-]+)\/participant/.exec(page.url())?.[1] ?? seedData.runId;
    await gotoStep(page, `/runs/${runId}/participant/stations`, 'stations');

    const challenges = await listRunChallenges(page, seedData);
    const first = challenges[0];
    if (first) {
      await gotoStep(
        page,
        `/runs/${runId}/participant/stations/${first.stationId}/challenges/${first.challengeId}`,
        `challenge-${kebabStep(first.type)}`,
      );
    } else {
      note('challenge: the run carries no challenges');
      await capture(page, 'challenge-missing');
    }
  });
});
