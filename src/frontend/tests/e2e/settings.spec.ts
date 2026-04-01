import { test, expect } from '@playwright/test';
import { registerNewOrg, navTo } from './helpers';

test.describe('org settings + admin user CRUD', () => {
  test('rename org persists', async ({ page }) => {
    await registerNewOrg(page, 'orgname');
    await navTo(page, 'Einstellungen');
    const input = page.getByRole('textbox', { name: 'Name', exact: true });
    // Wait until the org has loaded — input gains a non-empty value
    await expect(input).not.toHaveValue('');
    const newName = `Renamed ${Date.now().toString(36)}`;
    await input.fill(newName);
    const saveBtn = page.getByRole('button', { name: 'Speichern' });
    await expect(saveBtn).toBeEnabled();
    await saveBtn.click();
    await expect(page.getByText('Einstellungen wurden gespeichert.')).toBeVisible();
    // Reload — name field still has new value
    await page.reload();
    await expect(input).toHaveValue(newName);
  });

  test('save button stays disabled when name unchanged', async ({ page }) => {
    await registerNewOrg(page, 'orgnameunchanged');
    await navTo(page, 'Einstellungen');
    await expect(page.getByRole('button', { name: 'Speichern' })).toBeDisabled();
  });

  test('slug field is read-only', async ({ page }) => {
    await registerNewOrg(page, 'orgslugro');
    await navTo(page, 'Einstellungen');
    const slug = page.getByRole('textbox', { name: /Kurzname/ });
    await expect(slug).toHaveAttribute('readonly', '');
  });
});

test.describe('user management — full CRUD', () => {
  test('create coordinator user appears in list', async ({ page }) => {
    await registerNewOrg(page, 'usercrud');
    await navTo(page, 'Personen');
    await page.getByRole('button', { name: /Person anlegen|Add person/ }).click();
    const dialog = page.getByRole('dialog');
    const ts = Date.now().toString(36);
    const newEmail = `coord-${ts}@example.com`;
    await dialog.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(newEmail);
    await dialog.getByRole('textbox', { name: 'Anzeigename', exact: true }).fill(`Coord ${ts}`);
    await dialog.locator('input[type="password"]').fill('CoordPass1234!');
    // Default role = participant; pick coordinator
    await dialog.getByRole('combobox', { name: 'Rolle' }).click();
    await page.getByRole('option', { name: 'Spielleitung' }).click();
    await dialog.getByRole('button', { name: 'Anlegen', exact: true }).click();
    await expect(page.getByRole('cell', { name: newEmail })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'Spielleitung' })).toBeVisible();
  });

  test('edit user role with confirmation dialog', async ({ page }) => {
    await registerNewOrg(page, 'useredit');
    await navTo(page, 'Personen');
    // Create a participant first
    await page.getByRole('button', { name: /Person anlegen|Add person/ }).click();
    const dialog = page.getByRole('dialog');
    const ts = Date.now().toString(36);
    const email = `edit-${ts}@example.com`;
    await dialog.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(email);
    await dialog.getByRole('textbox', { name: 'Anzeigename', exact: true }).fill(`Edit ${ts}`);
    await dialog.locator('input[type="password"]').fill('EditPass1234!');
    await dialog.getByRole('button', { name: 'Anlegen', exact: true }).click();
    await expect(page.getByRole('cell', { name: email })).toBeVisible();

    // Edit that row — IconButton with aria-label "Bearbeiten" in the row
    await page.getByRole('row', { name: new RegExp(email) }).getByRole('button', { name: 'Bearbeiten' }).click();
    const editDialog = page.getByRole('dialog');
    // Picking a different role opens the confirm dialog IMMEDIATELY (not on save).
    await editDialog.getByRole('combobox', { name: 'Rolle' }).click();
    await page.getByRole('option', { name: 'Spielleitung' }).click();
    // Confirm dialog mentions the change
    const confirm = page.getByRole('dialog').filter({ hasText: /Rolle.*ändern|change.*role/i });
    await expect(confirm).toBeVisible();
    await confirm.getByRole('button', { name: 'Bestätigen' }).click();
    // Now Speichern in the edit dialog actually submits
    await editDialog.getByRole('button', { name: 'Speichern' }).click();
    await expect(page.getByRole('cell', { name: 'Spielleitung' })).toBeVisible();
  });

  test('delete user with anonymization warning', async ({ page }) => {
    await registerNewOrg(page, 'userdel');
    await navTo(page, 'Personen');
    // Create the user we'll delete
    await page.getByRole('button', { name: /Person anlegen|Add person/ }).click();
    const dialog = page.getByRole('dialog');
    const ts = Date.now().toString(36);
    const email = `del-${ts}@example.com`;
    await dialog.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(email);
    await dialog.getByRole('textbox', { name: 'Anzeigename', exact: true }).fill(`Del ${ts}`);
    await dialog.locator('input[type="password"]').fill('DelPass1234!');
    await dialog.getByRole('button', { name: 'Anlegen', exact: true }).click();
    await expect(page.getByRole('cell', { name: email })).toBeVisible();

    await page.getByRole('row', { name: new RegExp(email) }).getByRole('button', { name: 'Löschen' }).click();
    // The grace-delete dialog has a 30-day restore hint and an explicit Art. 17
    // checkbox. Both mention anonymization, hence first().
    await expect(page.getByRole('dialog').getByText(/anonymisier/i).first()).toBeVisible();
    await expect(page.getByRole('dialog').getByText(/30 Tage/)).toBeVisible();
    await expect(page.getByRole('dialog').getByRole('checkbox', { name: /Art\. 17/i })).toBeVisible();
    await page.getByRole('button', { name: 'Löschen', exact: true }).last().click();
    await expect(page.getByRole('cell', { name: email })).toHaveCount(0);
  });

  test('deactivate (toggle active → inactive) shows in status column', async ({ page }) => {
    await registerNewOrg(page, 'userdeactivate');
    await navTo(page, 'Personen');
    await page.getByRole('button', { name: /Person anlegen|Add person/ }).click();
    const dialog = page.getByRole('dialog');
    const ts = Date.now().toString(36);
    const email = `deact-${ts}@example.com`;
    await dialog.getByRole('textbox', { name: 'E-Mail', exact: true }).fill(email);
    await dialog.getByRole('textbox', { name: 'Anzeigename', exact: true }).fill(`Deact ${ts}`);
    await dialog.locator('input[type="password"]').fill('DeactPass1234!');
    await dialog.getByRole('button', { name: 'Anlegen', exact: true }).click();
    await expect(page.getByRole('cell', { name: email })).toBeVisible();

    await page.getByRole('row', { name: new RegExp(email) }).getByRole('button', { name: 'Deaktivieren' }).click();
    await expect(page.getByRole('row', { name: new RegExp(email) }).getByText('Inaktiv')).toBeVisible();
  });
});
