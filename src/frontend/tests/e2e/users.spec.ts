import { test, expect } from '@playwright/test';
import { registerNewOrg, navTo } from './helpers';

test.describe('user management (org_admin)', () => {
  test('table lists self after registration', async ({ page }) => {
    const creds = await registerNewOrg(page, 'users');
    await navTo(page, 'Personen');
    await expect(page.getByRole('cell', { name: creds.displayName })).toBeVisible();
    await expect(page.getByRole('cell', { name: creds.email })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'Verwaltung' }).first()).toBeVisible();
  });

  test('create user dialog validates password policy', async ({ page }) => {
    await registerNewOrg(page, 'usercreate');
    await navTo(page, 'Personen');
    await page.getByRole('button', { name: /Person anlegen|Add person/ }).click();
    await expect(page.getByRole('dialog')).toBeVisible();

    // Weak password should not submit
    const ts = Date.now().toString(36);
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(`new-${ts}@example.com`);
    await dialog.getByRole('textbox', { name: 'Anzeigename', exact: true }).fill(`New User ${ts}`);
    await dialog.locator('input[type="password"]').fill('weak');
    await dialog.getByRole('button', { name: 'Anlegen', exact: true }).click();
    // Either inline aria-invalid OR an alert; we just assert dialog is still open
    await expect(dialog).toBeVisible();
  });

  test('role filter dropdown narrows visible rows', async ({ page }) => {
    await registerNewOrg(page, 'rolefilter');
    await navTo(page, 'Personen');

    // Filter to "Teilnehmende" — none exist, table goes empty
    await page.getByRole('combobox', { name: /Rolle/ }).click();
    await page.getByRole('option', { name: 'Teilnehmende' }).click();
    await expect(page.getByText(/Keine Personen gefunden/)).toBeVisible();

    // Reset to "Alle" (the shared DataTable's all-filter sentinel) — admin row reappears
    await page.getByRole('combobox', { name: /Rolle/ }).click();
    await page.getByRole('option', { name: 'Alle', exact: true }).click();
    await expect(page.getByRole('cell', { name: 'Verwaltung' }).first()).toBeVisible();
  });
});
