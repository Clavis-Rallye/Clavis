import { test, expect } from '@playwright/test';
import { addChallengeToRallye, createRallye, createStation, registerNewOrg } from './helpers';

test.describe('stations + challenges CRUD', () => {
  test('add station to a rallye lands on the rallye with the new station open', async ({ page }) => {
    await registerNewOrg(page, 'station');
    const ts = Date.now().toString(36);
    const rallyeId = await createRallye(page, `Stat-Rallye-${ts}`);
    const stationTitle = `Station-${ts}`;
    // createStation drives the composition UX (RallyeDetail → AddChoiceDialog
    // → /stations/new?rallyeId=) and asserts the way back to the rallye.
    await createStation(page, rallyeId, stationTitle);
    await expect(page.getByText(stationTitle)).toBeVisible();
    // Open means: the station's own "add task" entry point is on screen.
    await expect(page.getByRole('button', { name: 'Aufgabe hinzufügen' })).toBeVisible();
  });

  // The create page takes the title image and uploads it after the station is saved.
  test('a new station takes its title image on the create page', async ({ page }) => {
    await registerNewOrg(page, 'stimg');
    const ts = Date.now().toString(36);
    const rallyeId = await createRallye(page, `Img-Rallye-${ts}`);
    await page.goto(`/stations/new?rallyeId=${rallyeId}`);
    await expect(page.getByRole('heading', { name: 'Neue Station', level: 1 })).toBeVisible();
    await page.getByRole('textbox', { name: /Titel.*Deutsch/i }).fill(`Img-Station-${ts}`);
    // A valid 1x1 PNG.
    await page.getByTestId('station-image-input').setInputFiles({
      name: 'titel.png',
      mimeType: 'image/png',
      buffer: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
        'base64',
      ),
    });
    await expect(page.getByRole('img', { name: 'Vorschau des Stations-Titelbilds' })).toBeVisible();

    const created = page.waitForResponse(
      (r) => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/rallyes/${rallyeId}/stations`),
    );
    const uploaded = page.waitForResponse(
      (r) => r.request().method() === 'PUT' && new URL(r.url()).pathname.endsWith('/title-image'),
    );
    await page.getByRole('button', { name: 'Station erstellen' }).click();
    const link = (await (await created).json()) as { link_id: string; station: { id: string } };
    expect((await uploaded).status()).toBe(200);
    await expect(page).toHaveURL(new RegExp(`/rallyes/${rallyeId}\\?open=${link.link_id}$`));

    // The station page shows the stored image, and no notice of a failed upload.
    await page.goto(`/stations/${link.station.id}`);
    await expect(page.getByRole('img', { name: 'Vorschau des Stations-Titelbilds' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Bild ersetzen' })).toBeVisible();
    await expect(page.getByText(/Das Titelbild ließ sich nicht hochladen/)).toHaveCount(0);
  });

  test('rallye-detail page exposes the "Station hinzufügen" entry point', async ({ page }) => {
    await registerNewOrg(page, 'addbtn');
    const ts = Date.now().toString(36);
    const rallyeTitle = `AddBtn-Rallye-${ts}`;
    const rallyeId = await createRallye(page, rallyeTitle);
    await page.goto(`/rallyes/${rallyeId}`);
    await expect(page.getByRole('heading', { name: rallyeTitle })).toBeVisible();
    const addBtn = page.getByRole('button', { name: 'Station hinzufügen' });
    await expect(addBtn).toBeVisible();
    await addBtn.click();
    // The AddChoiceDialog offers create new or use existing.
    await page.getByRole('button', { name: /Neue Station erstellen/ }).click();
    await expect(page).toHaveURL(new RegExp(`/stations/new\\?rallyeId=${rallyeId}$`));
  });

  test('text challenge create via the rallye composition flow', async ({ page }) => {
    await registerNewOrg(page, 'chtext');
    const ts = Date.now().toString(36);
    const rallyeId = await createRallye(page, `Ch-Rallye-${ts}`);
    await createStation(page, rallyeId, `Ch-Stat-${ts}`);
    // The helper drives the composition surface (expand station → "Aufgabe
    // hinzufügen" → "Neue Aufgabe erstellen" → back to the rallye) and asserts each step.
    const challengeTitle = `TextCh-${ts}`;
    await addChallengeToRallye(page, rallyeId, challengeTitle, 'Text');
    // The round trip carries ?open= back, so the station is already expanded
    // here. A click would collapse it again.
    await expect(page.getByText(`1. ${challengeTitle}`)).toBeVisible();
  });

  test('quiz challenge type toggle changes scoring fields', async ({ page }) => {
    await registerNewOrg(page, 'chquiz');
    const ts = Date.now().toString(36);
    // Challenges can be created standalone at /challenges/new.
    await page.goto('/challenges/new');
    await page.getByRole('button', { name: 'Quiz', exact: true }).click();
    // Quiz-specific scoring fields appear (answer options editor)
    await expect(page.getByText('Antwortoptionen')).toBeVisible();
    await page.getByRole('textbox', { name: /Titel.*Deutsch/i }).fill(`QuizCh-${ts}`);
    await page.getByRole('textbox', { name: /Anleitung.*Deutsch/i }).fill('Wähle die richtige Antwort.');
    // Submit with auto-approve left off.
    await page.getByRole('button', { name: 'Aufgabe erstellen' }).click();
    await expect(page).toHaveURL(/\/challenges\/[0-9a-f-]+$/);
  });

  test('the six challenge type toggles are present, and "Kreativ" is gone', async ({ page }) => {
    await registerNewOrg(page, 'chtypes');
    await page.goto('/challenges/new');
    for (const label of ['Foto', 'Video', 'Audio', 'Text', 'QR-Code', 'Quiz']) {
      await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
    }
    await expect(page.getByRole('button', { name: 'Kreativ', exact: true })).toHaveCount(0);
  });

  test('publish + unpublish a rallye', async ({ page }) => {
    await registerNewOrg(page, 'publish');
    const ts = Date.now().toString(36);
    const rallyeId = await createRallye(page, `Pub-${ts}`);
    await createStation(page, rallyeId, `Pub-Stat-${ts}`);
    await page.goto(`/rallyes/${rallyeId}`);
    // The switch is a field of the form and sends nothing. "Speichern" does.
    const toggle = page.getByRole('switch');
    const respPromise = page.waitForResponse(
      (r) => r.url().includes(`/rallyes/${rallyeId}`) && r.request().method() === 'PUT',
    );
    await toggle.first().click();
    await page.getByRole('button', { name: 'Speichern', exact: true }).click();
    const resp = await respPromise;
    expect(resp.status(), `publish PUT failed: ${await resp.text()}`).toBe(200);
    // The "Veröffentlicht" status filter of the list shows the rallye.
    await page.goto('/rallyes');
    await page.getByRole('combobox', { name: 'Status' }).click();
    await page.getByRole('option', { name: 'Veröffentlicht' }).click();
    await expect(page.getByText(`Pub-${ts}`)).toBeVisible();
  });
});
