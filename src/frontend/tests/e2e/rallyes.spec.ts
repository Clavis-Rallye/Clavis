import { test, expect } from '@playwright/test';
import { registerNewOrg, navTo } from './helpers';

test.describe('rallye CRUD (creator+)', () => {
  test('create rallye → land on detail page @smoke', async ({ page }) => {
    await registerNewOrg(page, 'rallye');
    await navTo(page, 'Rallyes');
    await expect(page.getByText(/Noch keine Rallyes erstellt/)).toBeVisible();

    await page.getByRole('button', { name: 'Rallye erstellen' }).click();
    await expect(page).toHaveURL(/\/rallyes\/new$/);
    // RallyeCreate is a lazy chunk: the URL changes at once, but the rallye
    // list can still be on screen. Wait for the new page's own heading first.
    await expect(page.getByRole('heading', { name: 'Neue Rallye', level: 1 })).toBeVisible();

    const ts = Date.now().toString(36);
    const title = `E2E Rallye ${ts}`;

    // MultilingualField shows one input, for the active locale tab. DE is active by default.
    await page.getByRole('textbox', { name: /Titel.*Deutsch/i }).fill(title);
    await page.getByRole('button', { name: 'Rallye erstellen' }).click();

    await expect(page).toHaveURL(/\/rallyes\/[0-9a-f-]+$/);
    await expect(page.getByRole('heading', { name: title })).toBeVisible();
  });

  test('search filter narrows the list', async ({ page }) => {
    await registerNewOrg(page, 'rallyesearch');
    await navTo(page, 'Rallyes');

    // Create one rallye via /rallyes/new (avoids name collision with the
    // submit button at the bottom of the form).
    await page.goto('/rallyes/new');
    const ts = Date.now().toString(36);
    const uniqueTitle = `Findable-${ts}`;
    await page.getByRole('textbox', { name: /Titel.*Deutsch/i }).fill(uniqueTitle);
    await page.getByRole('button', { name: 'Rallye erstellen' }).click();
    await expect(page).toHaveURL(/\/rallyes\/[0-9a-f-]+$/);

    // Back to list, search. The shared DataTable's search field is labelled
    // "Suchen" (placeholder carries the page-specific "Rallyes durchsuchen").
    await navTo(page, 'Rallyes');
    await expect(page.getByText(uniqueTitle)).toBeVisible();
    const search = page.getByRole('textbox', { name: 'Suchen' });
    await search.fill('zzzz-no-match');
    await expect(page.getByText(uniqueTitle)).toHaveCount(0);
    await search.fill('Findable');
    await expect(page.getByText(uniqueTitle)).toBeVisible();
  });

  test('Entwurf vs Veröffentlicht status filter toggles the list', async ({ page }) => {
    await registerNewOrg(page, 'rallyestate');

    // Create one draft rallye so the filter has something to include/exclude.
    await page.goto('/rallyes/new');
    const ts = Date.now().toString(36);
    const draftTitle = `Draft-${ts}`;
    await page.getByRole('textbox', { name: /Titel.*Deutsch/i }).fill(draftTitle);
    await page.getByRole('button', { name: 'Rallye erstellen' }).click();
    await expect(page).toHaveURL(/\/rallyes\/[0-9a-f-]+$/);

    // The DataTable filter is a "Status" select with Alle/Veröffentlicht/Entwurf.
    await navTo(page, 'Rallyes');
    await expect(page.getByText(draftTitle)).toBeVisible();

    await page.getByRole('combobox', { name: 'Status' }).click();
    await page.getByRole('option', { name: 'Veröffentlicht' }).click();
    // The only rallye is a draft — filtering to published empties the list
    await expect(page.getByText(draftTitle)).toHaveCount(0);
    await expect(page.getByText(/Noch keine Rallyes/)).toBeVisible();

    await page.getByRole('combobox', { name: 'Status' }).click();
    await page.getByRole('option', { name: 'Entwurf' }).click();
    await expect(page.getByText(draftTitle)).toBeVisible();

    await page.getByRole('combobox', { name: 'Status' }).click();
    await page.getByRole('option', { name: 'Alle' }).click();
    await expect(page.getByText(draftTitle)).toBeVisible();
  });
});
