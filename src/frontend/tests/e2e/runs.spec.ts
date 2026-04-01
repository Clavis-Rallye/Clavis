import { test, expect } from '@playwright/test';
import { registerNewOrg, createPublishedRallye, navTo } from './helpers';

test.describe('run lifecycle', () => {
  test('create run from published rallye lands on dashboard', async ({ page }) => {
    await registerNewOrg(page, 'runcreate');
    const { title } = await createPublishedRallye(page, 'rc');
    await navTo(page, 'Läufe');
    await page.getByRole('button', { name: 'Lauf erstellen' }).click();
    await expect(page).toHaveURL(/\/runs\/new$/);
    // RunCreate is a lazy chunk. The URL changes at once, but the runs list can
    // still be on screen. Wait for the heading of the wizard first.
    await expect(page.getByRole('heading', { name: 'Neuer Lauf', level: 1 })).toBeVisible();

    // Step 1: pick the rallye card
    await page.getByRole('heading', { name: title }).click();
    await page.locator('main').getByRole('button', { name: 'Weiter' }).click();

    // Step 2: name
    const ts = Date.now().toString(36);
    const runName = `Run-${ts}`;
    await page.getByRole('textbox', { name: 'Name' }).fill(runName);
    await page.getByRole('button', { name: 'Weiter' }).click();

    // Step 3: submit
    await page.getByRole('button', { name: 'Lauf erstellen' }).click();
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/);
    await expect(page.getByText('Konfiguriert', { exact: true })).toBeVisible();
  });

  test('start → pause → resume → end lifecycle transitions', async ({ page }) => {
    await registerNewOrg(page, 'runlife');
    const { title } = await createPublishedRallye(page, 'rl');
    await navTo(page, 'Läufe');
    await page.getByRole('button', { name: 'Lauf erstellen' }).click();
    await expect(page).toHaveURL(/\/runs\/new$/);
    // Same lazy-chunk wait as in the first test.
    await expect(page.getByRole('heading', { name: 'Neuer Lauf', level: 1 })).toBeVisible();
    await page.getByRole('heading', { name: title }).click();
    await page.locator('main').getByRole('button', { name: 'Weiter' }).click();
    await page.getByRole('textbox', { name: 'Name' }).fill(`LifeRun-${Date.now().toString(36)}`);
    await page.getByRole('button', { name: 'Weiter' }).click();
    await page.getByRole('button', { name: 'Lauf erstellen' }).click();
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/);

    // Configured → Active
    await page.getByRole('button', { name: 'Starten', exact: true }).click();
    await expect(page.getByText('Aktiv', { exact: true })).toBeVisible();

    // Active → Paused
    await page.getByRole('button', { name: 'Pausieren', exact: true }).click();
    await expect(page.getByText('Pausiert', { exact: true })).toBeVisible();

    // Paused → Active
    await page.getByRole('button', { name: 'Fortsetzen', exact: true }).click();
    await expect(page.getByText('Aktiv', { exact: true })).toBeVisible();

    // Active → Ended (confirmation dialog)
    await page.getByRole('button', { name: 'Beenden', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Beenden' }).click();
    await expect(page.getByText('Beendet', { exact: true })).toBeVisible();
  });

  test('schedule sets a future start time', async ({ page }) => {
    await registerNewOrg(page, 'runsched');
    const { title } = await createPublishedRallye(page, 'rs');
    await navTo(page, 'Läufe');
    await page.getByRole('button', { name: 'Lauf erstellen' }).click();
    await expect(page).toHaveURL(/\/runs\/new$/);
    // Same lazy-chunk wait as in the first test.
    await expect(page.getByRole('heading', { name: 'Neuer Lauf', level: 1 })).toBeVisible();
    await page.getByRole('heading', { name: title }).click();
    await page.locator('main').getByRole('button', { name: 'Weiter' }).click();
    await page.getByRole('textbox', { name: 'Name' }).fill(`SchedRun-${Date.now().toString(36)}`);
    await page.getByRole('button', { name: 'Weiter' }).click();
    await page.getByRole('button', { name: 'Lauf erstellen' }).click();

    // "Planen" opens the inline schedule picker. Fill the datetime and confirm
    // with "OK". The state chip must flip to "Geplant".
    await page.getByRole('button', { name: 'Planen', exact: true }).click();
    const dtInput = page.getByLabel('Startzeit');
    const future = new Date(Date.now() + 60 * 60 * 1000).toISOString().slice(0, 16);
    await dtInput.fill(future);
    await page.getByRole('button', { name: 'OK', exact: true }).click();
    await expect(page.getByText('Geplant', { exact: true })).toBeVisible();
  });

  test('join code + QR are visible on the dashboard', async ({ page }) => {
    await registerNewOrg(page, 'joincode');
    const { title } = await createPublishedRallye(page, 'jc');
    await navTo(page, 'Läufe');
    await page.getByRole('button', { name: 'Lauf erstellen' }).click();
    await expect(page).toHaveURL(/\/runs\/new$/);
    // Same lazy-chunk wait as in the first test.
    await expect(page.getByRole('heading', { name: 'Neuer Lauf', level: 1 })).toBeVisible();
    await page.getByRole('heading', { name: title }).click();
    await page.locator('main').getByRole('button', { name: 'Weiter' }).click();
    await page.getByRole('textbox', { name: 'Name' }).fill(`JoinRun-${Date.now().toString(36)}`);
    await page.getByRole('button', { name: 'Weiter' }).click();
    await page.getByRole('button', { name: 'Lauf erstellen' }).click();
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/);

    // Collapsible join panel: heading + code row + copy button + QR (SVG title)
    await expect(page.getByRole('heading', { name: 'Beitrittscode' })).toBeVisible();
    await expect(page.getByRole('img', { name: /Beitritts-QR/ })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Code kopieren' })).toBeVisible();
  });
});
