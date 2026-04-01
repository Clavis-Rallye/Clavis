import { test, expect } from '@playwright/test';
import { registerNewOrg, navTo } from './helpers';

test.describe('audit log viewer', () => {
  test('user_created event is visible after registration', async ({ page }) => {
    await registerNewOrg(page, 'logs');
    await navTo(page, 'Protokolle');
    // The labelled landmark of the LogViewer is the table itself.
    await expect(page.getByRole('table', { name: 'Audit-Protokolleinträge' })).toBeVisible();
    // The audit event from registering this very org
    await expect(page.getByRole('cell', { name: 'user_created' }).first()).toBeVisible();
  });

  test('CSV export download triggers', async ({ page }) => {
    await registerNewOrg(page, 'logsexport');
    await navTo(page, 'Protokolle');
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Als CSV exportieren' }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
  });

  test('category filter excludes audit events from the view', async ({ page }) => {
    await registerNewOrg(page, 'logsfilter');
    await navTo(page, 'Protokolle');
    // Wait for the initial logs request to settle
    await expect(page.getByRole('cell', { name: 'user_created' }).first()).toBeVisible();
    // The category goes to the server, which filters the whole period.
    // A non-audit category must hide the audit-only user_created event.
    await page.getByRole('combobox', { name: 'Kategorie' }).click();
    await page.getByRole('option', { name: 'Anwendung' }).click();
    await expect(page.getByRole('cell', { name: 'user_created' })).toHaveCount(0);
    // And switching back to "Alle" restores it
    await page.getByRole('combobox', { name: 'Kategorie' }).click();
    await page.getByRole('option', { name: 'Alle' }).click();
    await expect(page.getByRole('cell', { name: 'user_created' }).first()).toBeVisible();
  });

  test('CSV export holds exactly what the view filters', async ({ page }) => {
    await registerNewOrg(page, 'logsexportfilter');
    await navTo(page, 'Protokolle');
    await expect(page.getByRole('cell', { name: 'user_created' }).first()).toBeVisible();
    await page.getByRole('combobox', { name: 'Kategorie' }).click();
    await page.getByRole('option', { name: 'Audit' }).click();
    await page.getByRole('textbox', { name: 'Suchen' }).fill('user_created');
    await expect(page.getByRole('cell', { name: 'user_created' }).first()).toBeVisible();

    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Als CSV exportieren' }).click();
    const download = await downloadPromise;
    const stream = await download.createReadStream();
    let text = '';
    for await (const chunk of stream) text += chunk;

    const rows = text.split(/\r?\n/).slice(1).filter((line) => line.trim());
    expect(rows.length).toBeGreaterThan(0);
    for (const row of rows) {
      const [, category, event] = row.split(',');
      expect(category).toBe('audit');
      expect(event).toBe('user_created');
    }
  });
});
