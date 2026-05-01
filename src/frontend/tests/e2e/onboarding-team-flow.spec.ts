/**
 * End-to-end smoke for the team-flow wizard.
 *
 * Covers the full creator and member journey in two browser contexts, the live
 * update from the creator's stage change to the member's screen (under 3 s),
 * and Browser-Back walking every step without state loss.
 *
 * Camera-based QR scanning is not exercised here: getUserMedia is unavailable
 * in headless browsers, and unit tests of <QrScanner> cover the decode path.
 * The joiner uses the manual code input instead.
 */
import { expect, Page, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * axe gate for one step. `color-contrast` is disabled: it depends on the live
 * MUI palette (the light-theme primary container ramp), a theme concern
 * outside this spec. The gate covers the structural rules: roles, labels,
 * focus order, aria attributes.
 */
async function expectNoA11yViolations(page: Page, label: string): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .disableRules(['color-contrast'])
    .analyze();
  if (results.violations.length > 0) {
    console.log(`axe violations on ${label}:`, JSON.stringify(results.violations, null, 2));
  }
  expect(results.violations, `axe violations on ${label}`).toEqual([]);
}

/**
 * Register an org-admin, create a published rallye and a run and start it, all
 * through the API, so the test stays on the team flow and off the admin UI.
 */
async function setupRunForTeamFlow(
  page: Page,
  prefix: string,
): Promise<{ joinCode: string; runId: string }> {
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
        title: { de: 'Station 1' },
        description: { de: 'First' },
      },
    },
  );
  expect(station.ok(), await station.text()).toBeTruthy();
  const rallyeStationId = (await station.json()).link_id;

  const challenge = await ctx.request.post(
    `/api/orgs/${slug}/rallyes/${rallyeId}/stations/${rallyeStationId}/challenges`,
    {
      data: {
        challenge_type: 'text',
        title: { de: 'Challenge 1' },
        instructions: { de: 'Do something' },
        scoring_rules: { max_points: 10 },
      },
    },
  );
  expect(challenge.ok(), await challenge.text()).toBeTruthy();

  // Publish. Rallye create does not accept is_published, and a run needs a
  // published rallye.
  const publish = await ctx.request.put(`/api/orgs/${slug}/rallyes/${rallyeId}`, {
    data: { is_published: true },
  });
  expect(publish.ok(), await publish.text()).toBeTruthy();

  // 4. Create + start run
  const run = await ctx.request.post(`/api/orgs/${slug}/runs/`, {
    data: { rallye_id: rallyeId, name: `${prefix}-Run-${ts}` },
  });
  expect(run.ok(), await run.text()).toBeTruthy();
  const runJson = await run.json();
  const runId = runJson.id;
  const joinCode = runJson.join_code;

  const started = await ctx.request.post(
    `/api/orgs/${slug}/runs/${runId}/start`,
  );
  expect(started.ok(), await started.text()).toBeTruthy();

  return { joinCode, runId };
}

/**
 * The invite step enables "Weiter" as soon as the team bootstrap reaches the
 * wizard's React state. A persistence effect writes that state to
 * sessionStorage a beat later, so one read of the stored state races the
 * write. Prefer the code the step shows, else poll the stored state for 10 s.
 */
async function readTeamShareCode(creator: Page): Promise<string> {
  // Fast path: the copy button's text is the share code itself ('------'
  // while the team bootstrap has not delivered one yet).
  const shown = await creator
    .getByRole('button', { name: /team-code kopieren|copy team code/i })
    .textContent();
  if (shown && shown !== '------') return shown;

  const readStoredCode = (): Promise<string | null> =>
    creator.evaluate(() => {
      const raw = window.sessionStorage.getItem('clavis_wizard_team_state');
      if (!raw) return null;
      try {
        const parsed = JSON.parse(raw) as { teamShareCode?: string | null };
        return parsed.teamShareCode || null;
      } catch {
        return null;
      }
    });
  await expect
    .poll(readStoredCode, {
      timeout: 10_000,
      message: 'creator should have a team_share_code in state',
    })
    .not.toBeNull();
  const stored = await readStoredCode();
  expect(stored, 'creator should have a team_share_code in state').not.toBeNull();
  return stored!;
}

test.describe('Team flow — creator + joiner full journey', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('create + join + name commit + auto-advance + start rallye', async ({ browser }) => {
    // -- coordinator setup --
    const coordCtx = await browser.newContext();
    const coordPage = await coordCtx.newPage();
    const { joinCode } = await setupRunForTeamFlow(coordPage, 'tf027');

    // -- creator: create a name-pending team in the order Find → Invite → PickName.
    const creatorCtx = await browser.newContext();
    const creator = await creatorCtx.newPage();
    await creator.goto(`/join/${joinCode}`);
    await creator.getByRole('button', { name: /weiter|continue/i }).click(); // Welcome → auth-choice
    await creator // EntryChoice → profile
      .getByRole('button', { name: /als gast mitmachen|join as a guest/i })
      .click();
    await creator.getByLabel(/anzeigename|display name/i).fill('Alice');
    await creator.getByRole('button', { name: /weiter|continue/i }).click(); // Profile → team/find
    // FindTeam — pick "Create"
    await expectNoA11yViolations(creator, 'FindTeamStep');
    await creator
      .getByRole('button', { name: /create.*team|team.*erstellen/i })
      .click();
    await creator.getByRole('button', { name: /weiter|continue/i }).click();
    // Invite step (creator path): the team is created here and the share code is visible.
    await expect(creator).toHaveURL(/\/join\/.+\/team\/invite$/);
    await expect(creator.getByRole('img', { name: /QR/ })).toBeVisible();
    // Wait for the Continue button to leave its disabled state so axe doesn't
    // flag MUI's transient white-on-pale-blue disabled-button contrast.
    await expect(
      creator.getByRole('button', { name: /weiter|continue/i }),
    ).toBeEnabled();
    await expectNoA11yViolations(creator, 'InviteTeamStep');

    // -- joiner: arrive while team is name-pending --
    const memberCtx = await browser.newContext();
    const member = await memberCtx.newPage();
    await member.goto(`/join/${joinCode}`);
    await member.getByRole('button', { name: /weiter|continue/i }).click(); // Welcome → auth-choice
    await member // EntryChoice → profile
      .getByRole('button', { name: /als gast mitmachen|join as a guest/i })
      .click();
    await member.getByLabel(/anzeigename|display name/i).fill('Bob');
    await member.getByRole('button', { name: /weiter|continue/i }).click();
    await member
      .getByRole('button', { name: /existing.*team|bestehend.*team/i })
      .click();
    await member.getByRole('button', { name: /weiter|continue/i }).click();
    // JoinTeam — read the share-code the creator's invite step shows.
    const shareCode = await readTeamShareCode(creator);
    await expectNoA11yViolations(member, 'JoinTeamStep');
    await member.getByLabel(/team.code/i).fill(shareCode);
    // JoinTeamStep submits through a dedicated "Team beitreten" button that
    // appears once a code is typed, not through the generic "Weiter" footer button.
    await member.getByRole('button', { name: /team beitreten|join.*team/i }).click();
    // Joiner flow: code submitted → Waiting (name-agnostic) →
    // (creator leaves Invite) → Pending → (creator commits name) → run view.
    await expect(member).toHaveURL(/\/join\/.+\/team\/waiting$/);
    await expectNoA11yViolations(member, 'TeamWaitingStep');

    // -- creator advances Invite → PickName; member auto-advances Waiting → Pending --
    const tStage = Date.now();
    await creator.getByRole('button', { name: /weiter|continue/i }).click();
    await expect(creator).toHaveURL(/\/join\/.+\/team\/name$/);
    await expectNoA11yViolations(creator, 'PickTeamNameStep');
    // The creator's stage change reaches the member live over the WebSocket.
    await expect(member).toHaveURL(/\/join\/.+\/team\/pending$/, { timeout: 5_000 });
    expect(Date.now() - tStage).toBeLessThan(3_000);
    await expectNoA11yViolations(member, 'TeamNamePendingStep');

    // -- creator commits the name (Start Rallye) → run participant view;
    //    member auto-advances Pending → run participant view in the same
    //    gesture, with no intermediate /team/ready screen --
    const tName = Date.now();
    await creator.getByLabel(/team.name|team-name/i).fill('Speedy Foxes');
    await creator.getByRole('button', { name: /start.*rallye|rallye.*starten/i }).click();
    await expect(creator).toHaveURL(/\/runs\/[0-9a-f-]+\/participant/);
    await expect(member).toHaveURL(/\/runs\/[0-9a-f-]+\/participant/, { timeout: 5_000 });
    expect(Date.now() - tName).toBeLessThan(3_000);

    await coordCtx.close();
    await creatorCtx.close();
    await memberCtx.close();
  });

  /**
   * The typed-code fallback of JoinTeamStep, the alternative to the QR scan,
   * through its fragile transition: a WRONG code shows the not-found error, and
   * the retyped correct code lands in the participant shell.
   *
   * Here the joiner arrives AFTER the creator committed the team name. The join
   * response then carries a team_name, so the joiner must route straight to
   * /runs/:id/participant and not to the waiting room.
   */
  test('257 — typed-code join: wrong code shows error, retype succeeds', async ({ browser }) => {
    // -- coordinator setup --
    const coordCtx = await browser.newContext();
    const coordPage = await coordCtx.newPage();
    const { joinCode } = await setupRunForTeamFlow(coordPage, 'tf257');

    // -- creator: full create path incl. name commit, so the team is named --
    const creatorCtx = await browser.newContext();
    const creator = await creatorCtx.newPage();
    await creator.goto(`/join/${joinCode}`);
    await creator.getByRole('button', { name: /weiter|continue/i }).click();
    await creator
      .getByRole('button', { name: /als gast mitmachen|join as a guest/i })
      .click();
    await creator.getByLabel(/anzeigename|display name/i).fill('Carla');
    await creator.getByRole('button', { name: /weiter|continue/i }).click();
    await creator
      .getByRole('button', { name: /create.*team|team.*erstellen/i })
      .click();
    await creator.getByRole('button', { name: /weiter|continue/i }).click();
    await expect(creator).toHaveURL(/\/join\/.+\/team\/invite$/);
    await expect(creator.getByRole('img', { name: /QR/ })).toBeVisible();
    const shareCode = await readTeamShareCode(creator);
    await expect(
      creator.getByRole('button', { name: /weiter|continue/i }),
    ).toBeEnabled();
    await creator.getByRole('button', { name: /weiter|continue/i }).click();
    await expect(creator).toHaveURL(/\/join\/.+\/team\/name$/);
    await creator.getByLabel(/team.name|team-name/i).fill('Flinke Igel');
    await creator
      .getByRole('button', { name: /start.*rallye|rallye.*starten/i })
      .click();
    await expect(creator).toHaveURL(/\/runs\/[0-9a-f-]+\/participant/);

    // -- joiner: manual typed-code fallback --
    const joinerCtx = await browser.newContext();
    const joiner = await joinerCtx.newPage();
    await joiner.goto(`/join/${joinCode}`);
    await joiner.getByRole('button', { name: /weiter|continue/i }).click();
    await joiner
      .getByRole('button', { name: /als gast mitmachen|join as a guest/i })
      .click();
    await joiner.getByLabel(/anzeigename|display name/i).fill('Dora');
    await joiner.getByRole('button', { name: /weiter|continue/i }).click();
    await joiner
      .getByRole('button', { name: /existing.*team|bestehend.*team/i })
      .click();
    await joiner.getByRole('button', { name: /weiter|continue/i }).click();
    await expect(joiner).toHaveURL(/\/join\/.+\/team\/join$/);

    // 1) A WRONG code: the real code with its last character flipped, so the
    //    lookup in the same run must miss and show the not-found error.
    const wrongCode =
      shareCode.slice(0, -1) + (shareCode.endsWith('X') ? 'Y' : 'X');
    const codeInput = joiner.getByLabel(/team.code/i);
    await codeInput.fill(wrongCode);
    await joiner
      .getByRole('button', { name: /team beitreten|join.*team/i })
      .click();
    await expect(joiner.getByText(/code unbekannt/i)).toBeVisible();
    // The error neither leaves the join step nor wedges the form.
    await expect(joiner).toHaveURL(/\/join\/.+\/team\/join$/);

    // 2) Correct the code in the same input. Typing must clear the error.
    //    Then submit again → participant shell.
    await codeInput.fill(shareCode);
    await expect(joiner.getByText(/code unbekannt/i)).toBeHidden();
    await joiner
      .getByRole('button', { name: /team beitreten|join.*team/i })
      .click();
    await expect(joiner).toHaveURL(/\/runs\/[0-9a-f-]+\/participant/);

    await coordCtx.close();
    await creatorCtx.close();
    await joinerCtx.close();
  });

  /**
   * Browser-Back walks every step without state loss.
   *
   * The joiner walks forward Welcome → EntryChoice → Profile → Find → Join,
   * entering a value where a step takes one, then goes back step by step. Each
   * step still shows its value, and the team-flow state in sessionStorage stays
   * unchanged.
   *
   * The typed team code is NOT submitted: a submit joins the team, and the
   * wizard then bounces a team member off Find, Profile and Welcome back to
   * their team step. The walk covers every step that holds entered state.
   */
  test('Browser-Back walks every step without state loss', async ({ browser }) => {
    // -- coordinator setup --
    const coordCtx = await browser.newContext();
    const coordPage = await coordCtx.newPage();
    const { joinCode } = await setupRunForTeamFlow(coordPage, 'tf017');

    // -- creator: walk to the invite step so a real team with a share code
    //    exists for the joiner to type (same walk as in the full journey).
    const creatorCtx = await browser.newContext();
    const creator = await creatorCtx.newPage();
    await creator.goto(`/join/${joinCode}`);
    await creator.getByRole('button', { name: /weiter|continue/i }).click();
    await creator // EntryChoice → profile
      .getByRole('button', { name: /als gast mitmachen|join as a guest/i })
      .click();
    await creator.getByLabel(/anzeigename|display name/i).fill('Erik');
    await creator.getByRole('button', { name: /weiter|continue/i }).click(); // Profile → find
    await creator
      .getByRole('button', { name: /create.*team|team.*erstellen/i })
      .click();
    await creator.getByRole('button', { name: /weiter|continue/i }).click(); // Find → invite
    await expect(creator).toHaveURL(/\/join\/.+\/team\/invite$/);
    await expect(
      creator.getByRole('button', { name: /weiter|continue/i }),
    ).toBeEnabled();
    const shareCode = await readTeamShareCode(creator);

    // -- joiner: forward through every step, entering values --
    const joinerCtx = await browser.newContext();
    const joiner = await joinerCtx.newPage();
    await joiner.goto(`/join/${joinCode}`);
    await joiner.getByRole('button', { name: /weiter|continue/i }).click(); // Welcome → auth-choice
    await joiner // EntryChoice → profile
      .getByRole('button', { name: /als gast mitmachen|join as a guest/i })
      .click();
    await joiner.getByLabel(/anzeigename|display name/i).fill('Eve');
    await joiner.getByRole('button', { name: /weiter|continue/i }).click(); // Profile → find
    await expect(joiner).toHaveURL(/\/join\/.+\/team\/find$/);
    await joiner
      .getByRole('button', { name: /existing.*team|bestehend.*team/i })
      .click();
    await joiner.getByRole('button', { name: /weiter|continue/i }).click(); // Find → join
    await expect(joiner).toHaveURL(/\/join\/.+\/team\/join$/);
    await joiner.getByLabel(/team.code/i).fill(shareCode);

    // Baseline: the exact team-flow payload written at the last forward
    // step. Every back stop below must still hold it, byte for byte.
    await expect
      .poll(() =>
        joiner.evaluate(
          () => window.sessionStorage.getItem('clavis_wizard_team_state') ?? '',
        ),
      )
      .toContain(`"enteredCode":"${shareCode}"`);
    const teamFlowBaseline = await joiner.evaluate(() =>
      window.sessionStorage.getItem('clavis_wizard_team_state'),
    );
    const expectTeamFlowSurvives = async (step: string): Promise<void> => {
      const raw = await joiner.evaluate(() =>
        window.sessionStorage.getItem('clavis_wizard_team_state'),
      );
      expect(raw, `team-flow state on ${step}`).toBe(teamFlowBaseline);
    };

    // Back at the join step → find: the join choice is still selected.
    await joiner.goBack();
    await expect(joiner).toHaveURL(/\/join\/.+\/team\/find$/);
    await expect(
      joiner.getByRole('button', { name: /existing.*team|bestehend.*team/i }),
    ).toHaveAttribute('aria-pressed', 'true');
    await expectTeamFlowSurvives('find after back from join');

    // Forward once more to the join step: it re-reads the wizard state, so
    // the code typed before is still in the input.
    await joiner.getByRole('button', { name: /weiter|continue/i }).click();
    await expect(joiner).toHaveURL(/\/join\/.+\/team\/join$/);
    await expect(joiner.getByLabel(/team.code/i)).toHaveValue(shareCode);
    await expectTeamFlowSurvives('join revisited');

    // Back at the join step → find again, then keep walking back.
    await joiner.goBack();
    await expect(joiner).toHaveURL(/\/join\/.+\/team\/find$/);
    await expect(
      joiner.getByRole('button', { name: /existing.*team|bestehend.*team/i }),
    ).toHaveAttribute('aria-pressed', 'true');
    await expectTeamFlowSurvives('find');

    // Back at find → profile: the display name entered before is still set.
    await joiner.goBack();
    await expect(joiner).toHaveURL(/\/join\/.+\/profile$/);
    await expect(joiner.getByLabel(/anzeigename|display name/i)).toHaveValue('Eve');
    await expectTeamFlowSurvives('profile');
    expect(
      await joiner.evaluate(() =>
        window.sessionStorage.getItem('clavis_wizard_displayName'),
      ),
    ).toBe('Eve');

    // Back at profile → auth-choice: the step renders again.
    await joiner.goBack();
    await expect(joiner).toHaveURL(/\/join\/.+\/auth-choice$/);
    await expect(
      joiner.getByRole('button', { name: /als gast mitmachen|join as a guest/i }),
    ).toBeVisible();
    await expectTeamFlowSurvives('auth-choice');

    // Back at auth-choice → welcome: the first step renders again.
    await joiner.goBack();
    await expect(joiner).toHaveURL(/\/join\/[^/]+$/);
    await expect(
      joiner.getByRole('button', { name: /weiter|continue/i }),
    ).toBeVisible();
    await expectTeamFlowSurvives('welcome');

    await coordCtx.close();
    await creatorCtx.close();
    await joinerCtx.close();
  });
});
